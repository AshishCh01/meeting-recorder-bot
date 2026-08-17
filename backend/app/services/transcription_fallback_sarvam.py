"""
Fallback transcription path using Sarvam AI (Saaras v3 batch STT +
sarvam-105b chat model), used when Gemini keeps failing (429/503)
after retries are exhausted.

Produces a dict shaped identically to RESPONSE_SCHEMA in
transcription_service.py so the rest of the pipeline (DB write,
index_transcript, frontend) needs no changes.
"""

import json
import os
import tempfile

from sarvamai import SarvamAI

from app.config import settings

_client = None

def get_sarvam_client():
    global _client
    if _client is None:
        key = settings.sarvam_api_key.strip().strip('"').strip("'")
        if not key:
            raise ValueError("Sarvam API key is not configured.")
        _client = SarvamAI(api_subscription_key=key)
    return _client

def _sec_to_mmss(seconds: float) -> str:
    total_seconds = int(seconds)
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def _run_stt_job(audio_path: str) -> dict:
    job_kwargs = {
        "model": settings.sarvam_stt_model,
        "mode": "transcribe",
        "language_code": settings.sarvam_language_code,
        "with_diarization": True,
    }
    # Omit num_speakers entirely (rather than passing None) so Sarvam
    # auto-detects the speaker count - meetings have a variable number
    # of participants, so a fixed count would misdiarize most of them.
    if settings.sarvam_num_speakers is not None:
        job_kwargs["num_speakers"] = settings.sarvam_num_speakers

    client = get_sarvam_client()
    job = client.speech_to_text_job.create_job(**job_kwargs)

    job.upload_files(file_paths=[audio_path])
    job.start()
    job.wait_until_complete(poll_interval=5, timeout=420)

    file_results = job.get_file_results()
    if file_results["failed"]:
        failure = file_results["failed"][0]
        raise RuntimeError(
            f"Sarvam transcription failed for {failure['file_name']}: {failure['error_message']}"
        )

    with tempfile.TemporaryDirectory() as output_dir:
        job.download_outputs(output_dir=output_dir)

        json_files = []
        for root, _dirs, files in os.walk(output_dir):
            for f in files:
                if f.lower().endswith(".json"):
                    json_files.append(os.path.join(root, f))

        if not json_files:
            raise FileNotFoundError(f"No .json output found under '{output_dir}'")
        if len(json_files) > 1:
            raise RuntimeError(f"Expected one output file, found {len(json_files)}: {json_files}")

        with open(json_files[0], "r", encoding="utf-8") as f:
            return json.load(f)


def _build_conversation(stt_result: dict) -> list[dict]:
    conversation = []
    entries = stt_result.get("diarized_transcript", {}).get("entries", [])
    for entry in entries:
        conversation.append({
            "text": f"Speaker {entry['speaker_id']} - {entry['transcript']}",
            "timestamp_start": _sec_to_mmss(entry["start_time_seconds"]),
            "timestamp_end": _sec_to_mmss(entry["end_time_seconds"]),
        })
    return conversation


ANALYSIS_SCHEMA = {
    "name": "audio_analysis",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "3-5 sentence overview of what the audio covers"},
            "key_points": {
                "type": "array",
                "description": "Main ideas, arguments, or options discussed - not transcript lines",
                "items": {"type": "string"},
            },
            "action_items": {
                "type": "array",
                "description": "Concrete tasks/decisions/follow-ups actually mentioned. Empty if none.",
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string"},
                        "owner": {"type": "string", "description": "Who is responsible, or 'Unspecified'"},
                        "timestamp": {"type": "string", "description": "MM:SS if identifiable, else empty string"},
                    },
                    "required": ["item", "owner", "timestamp"],
                    "additionalProperties": False,
                },
            },
            "conclusion": {"type": "string", "description": "How the conversation wraps up"},
        },
        "required": ["summary", "key_points", "action_items", "conclusion"],
        "additionalProperties": False,
    },
}


def _build_analysis(transcript_text: str) -> dict:
    system_prompt = (
        "You are analyzing a transcript of spoken audio (podcast, meeting, "
        "or conversation). Extract a summary, key points, action items, and "
        "a conclusion. key_points are the main ideas/arguments/options "
        "discussed, not transcript lines. action_items are only concrete "
        "tasks/decisions/follow-ups actually mentioned - do not invent any; "
        "return an empty array if there are none."
    )

    client = get_sarvam_client()
    response = client.chat.completions(
        model=settings.sarvam_chat_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Transcript:\n\n{transcript_text}"},
        ],
        reasoning_effort=None,
        max_tokens=2000,
        request_options={
            "additional_body_parameters": {
                "response_format": {"type": "json_schema", "json_schema": ANALYSIS_SCHEMA}
            }
        },
    )

    raw = response.choices[0].message.content
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"summary": raw, "key_points": [], "action_items": [], "conclusion": "", "_parse_error": True}


def transcribe_with_sarvam_fallback(audio_path: str) -> dict:
    """
    Entry point called from transcription_service.py when Gemini
    fails. audio_path is the same tmp_path already downloaded from
    Supabase - no re-download needed.
    """
    print("[transcription_fallback] Starting Sarvam AI fallback transcription...")
    stt_result = _run_stt_job(audio_path)

    transcript_text = stt_result.get("transcript", "")
    analysis = _build_analysis(transcript_text)

    result = {
        "summary": analysis.get("summary", ""),
        "key_points": analysis.get("key_points", []),
        "action_items": analysis.get("action_items", []),
        "conclusion": analysis.get("conclusion", ""),
        "conversation": _build_conversation(stt_result),
    }
    print("[transcription_fallback] Sarvam AI fallback completed successfully")
    return result