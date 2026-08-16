import json
import tempfile
import os
import subprocess
import re
from typing import Optional

from google import genai
from google.genai import types, errors

from app.config import settings
from app.db.supabase import supabase
from app.services.embedding_service import index_transcript

client = genai.Client(api_key=settings.gemini_api_key)

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "A concise 3-5 sentence overview of what the audio covers."
        },
        "key_points": {
            "type": "array",
            "description": "The main ideas, arguments, or options discussed, as short standalone bullets.",
            "items": {"type": "string"}
        },
        "action_items": {
            "type": "array",
            "description": "Concrete tasks, decisions, or follow-ups mentioned. Empty array if none.",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string", "description": "The action or task itself."},
                    "owner": {
                        "type": "string",
                        "description": "Who is responsible, if stated or implied. Otherwise 'Unspecified'."
                    },
                    "timestamp": {
                        "type": "string",
                        "description": "MM:SS where this was mentioned."
                    }
                },
                "required": ["item", "owner"]
            }
        },
        "conclusion": {
            "type": "string",
            "description": "How the conversation wraps up: final takeaway, resolution, or closing thought."
        },
        "conversation": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Format: 'Speaker - text'. Use real name if mentioned in audio, otherwise 'Speaker 1', 'Speaker 2', etc."
                    },
                    "timestamp_start": {"type": "string", "description": "MM:SS format"},
                    "timestamp_end": {"type": "string", "description": "MM:SS format"}
                },
                "required": ["text", "timestamp_start", "timestamp_end"]
            }
        }
    },
    "required": ["summary", "key_points", "action_items", "conclusion", "conversation"]
}

PROMPT = """
Transcribe this audio in full, and also analyze its content.

Requirements for speaker identification:
1. Determine the number of distinct speakers.
2. Listen for names mentioned anywhere in the audio and reason carefully
   about WHO each name refers to:
   - If a speaker says "I'm X" or "This is X" -> the name belongs to the
     SPEAKER saying it.
   - If a speaker addresses someone directly by name (e.g. "Hi, Rohan",
     "Thanks, Priya", "Rohan, what do you think?")
     -> the name belongs to the OTHER speaker being spoken TO, not the one
     speaking.
   - If one speaker is greeted by name and then replies using a different
     name to address the first speaker back, use that exchange to assign
     both names correctly.
3. Once a speaker's real name is determined, use that name consistently
   for ALL of their segments in the transcript, including segments before
   the name was mentioned.
4. If no name is ever mentioned or the reference is ambiguous, fall back
   to "Speaker 1", "Speaker 2", etc. for that speaker only.
5. Provide accurate start/end timestamps for each segment (MM:SS format).

Requirements for content analysis:
6. summary: A concise 3-5 sentence overview of the whole audio.
7. key_points: The main ideas, arguments, options, or topics discussed,
   as short standalone bullet points (not full transcript lines).
8. action_items: Any concrete tasks, decisions, or follow-ups mentioned,
   with an owner if one is stated or clearly implied by context (otherwise
   "Unspecified") and a timestamp of where it was mentioned. Return an
   empty array if the audio has no action items.
9. conclusion: How the conversation resolves or wraps up — the final
   takeaway, decision, or closing thought.
"""

def _is_audio_silent(file_path: str) -> bool:
    """
    Uses ffmpeg to check if the audio is completely silent.
    Prevents Gemini from hallucinating conversations out of static/silence.
    """
    try:
        cmd = [
            "ffmpeg", "-i", file_path,
            "-af", "volumedetect",
            "-f", "null", "-"
        ]
        result = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
        
        # Look for the mean_volume output from ffmpeg
        mean_match = re.search(r"mean_volume:\s*([-0-9.]+)\s*dB", result.stderr)
        
        if mean_match:
            mean_vol = float(mean_match.group(1))
            print(f"[transcription] Audio mean volume: {mean_vol} dB")
            # -65.0 dB or lower is generally pure digital silence or baseline static
            if mean_vol < -65.0: 
                return True
        elif "mean_volume: -inf" in result.stderr:
            return True
            
        return False
    except Exception as e:
        print(f"[transcription] Warning: Silence detection failed ({e}). Proceeding to transcription.")
        return False


def transcribe_recording(meeting_id: str, storage_path: str) -> Optional[dict]:
    """
    Downloads the recording from Supabase Storage, sends it to Gemini
    for transcription and analysis, and writes the result to the meeting row.
    Returns None if the download fails (already marked failed in DB).
    """
    print(f"[transcription] Starting for meeting {meeting_id}, path: {storage_path}")

    try:
        file_bytes = supabase.storage.from_(
            settings.supabase_recordings_bucket
        ).download(storage_path)
    except Exception as e:
        _mark_failed(meeting_id, f"Failed to download recording: {e}")
        return None  # explicit early return — no file to process

    suffix = os.path.splitext(storage_path)[1] or ".m4a"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    print(f"[transcription] Downloaded to temp file: {tmp_path}")

    uploaded_file = None
    try:
        # Phase 1, Step 3: Silence Detection
        if _is_audio_silent(tmp_path):
            print("[transcription] Audio is completely silent. Skipping Gemini API to prevent hallucinations.")
            
            silent_result = {
                "summary": "This meeting recording was completely silent.",
                "key_points": ["No audio was detected during the recording (possibly everyone was muted or the meeting was empty)."],
                "action_items": [],
                "conclusion": "No discussion took place.",
                "conversation": []
            }
            
            supabase.table("meetings").update({
                "transcript": silent_result,
                "status": "completed",
            }).eq("id", meeting_id).execute()
            
            return silent_result

        print("[transcription] Uploading to Gemini File API...")
        uploaded_file = client.files.upload(
            file=tmp_path,
            config=types.UploadFileConfig(mime_type="audio/mp4")
        )

        if uploaded_file.uri is None:
            raise ValueError("Gemini File API returned no URI after upload")

        print(f"[transcription] Uploaded. URI: {uploaded_file.uri}")

        print("[transcription] Sending to Gemini for analysis...")
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=[
                PROMPT,
                types.Part.from_uri(
                    file_uri=uploaded_file.uri,
                    mime_type="audio/mp4"
                ),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
            ),
        )

        if response.text is None:
            raise ValueError("Gemini returned an empty response — no text content")

        result = json.loads(response.text)
        print("[transcription] Gemini response parsed successfully")

        supabase.table("meetings").update({
            "transcript": result,
            "status": "completed",
        }).eq("id", meeting_id).execute()

        try:
            index_transcript(meeting_id, result)
        except Exception as e:
            supabase.table("meetings").update({
                "error_message": f"Transcript ready, but RAG indexing failed: {e}",
            }).eq("id", meeting_id).execute()

        return result

    except errors.APIError as e:
        # Gracefully handle 503 Server Overloaded without crashing the app
        print(f"[transcription] Gemini API Error: {e}")
        msg = f"Gemini Error: {e.message}"
        if e.code == 503:
            msg = "Transcription failed: Gemini servers are currently overloaded (503). Please try again later."
        _mark_failed(meeting_id, msg)
        
    except Exception as e:
        print(f"[transcription] ERROR: {e}")
        _mark_failed(meeting_id, f"Transcription failed: {str(e)}")
        # Removed the `raise` keyword here so the background task exits cleanly

    finally:
        # Phase 3, Step 9: Delete Gemini file after transcription to save quota
        if uploaded_file and hasattr(uploaded_file, 'name') and uploaded_file.name:
            try:
                client.files.delete(name=uploaded_file.name)
                print(f"[transcription] Cleaned up Gemini storage file: {uploaded_file.name}")
            except Exception as cleanup_err:
                print(f"[transcription] Warning: Failed to clean up Gemini file: {cleanup_err}")

        # Clean up local temp file
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
            print(f"[transcription] Temp file cleaned up: {tmp_path}")


def _mark_failed(meeting_id: str, message: str) -> None:
    print(f"[transcription] Marking failed: {message}")
    supabase.table("meetings").update({
        "status": "failed",
        "error_message": message,
    }).eq("id", meeting_id).execute()