import asyncio
import json
import tempfile
import os
import subprocess
import re
import time
import random
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

transcription_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="transcribe")

from google import genai
from google.genai import types, errors

from app.config import settings
from app.observability import sentry_enabled, set_meeting_context
from app.db.supabase import supabase
from app.db.database import SessionLocal
from app.db.models import Meeting
from app.services.embedding_service import index_transcript
from app.services.transcription_fallback_sarvam import transcribe_with_sarvam_fallback
from app.services.cost_tracker import gemini_generation_cost, log_cost
from app.services.gemini_errors import TRANSIENT_EXCEPTIONS, error_code_str, is_transient

client = genai.Client(api_key=settings.gemini_api_key, http_options=types.HttpOptions(timeout=90_000))


class IndexingFailed(Exception):
    """
    index_transcript raised after the transcript was already saved and the
    meeting already said "completed".

    This needs its own type rather than propagating the original exception,
    because transcribe_recording's broad `except Exception` would otherwise
    catch it and call _mark_failed - flipping a meeting that has a perfectly
    good transcript, summary and action items to "failed". The handler
    re-raises this type specifically so the queue can retry just the indexing
    step, which the resume check at the top of transcribe_recording makes
    cheap: no re-download, no re-transcription.
    """

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "A short, descriptive title for the meeting, under 60 characters. Should reflect the main topic discussed, not a generic label like 'Meeting'."
        },
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
                    "due_date": {
                        "type": "string",
                        "nullable": True,
                        "description": "Due date or deadline for this item, in whatever form the transcript states it (e.g. 'Friday', 'next week', '2026-09-01'). Null if no due date was mentioned."
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
    "required": ["title", "summary", "key_points", "action_items", "conclusion", "conversation"]
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
6. title: A short, descriptive title for the meeting, under 60 characters,
   reflecting the main topic actually discussed. Do not use a generic
   placeholder like "Meeting" or "Recording".
7. summary: A concise 3-5 sentence overview of the whole audio.
8. key_points: The main ideas, arguments, options, or topics discussed,
   as short standalone bullet points (not full transcript lines).
9. action_items: Any concrete tasks, decisions, or follow-ups mentioned,
   with an owner if one is stated or clearly implied by context (otherwise
   "Unspecified"), a due date if one is stated or clearly implied (e.g. a
   day, date, or deadline) or null if none was mentioned, and a timestamp
   of where it was mentioned. Return an empty array if the audio has no
   action items.
10. conclusion: How the conversation resolves or wraps up — the final
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


def _get_audio_duration_seconds(file_path: str) -> Optional[float]:
    """
    Uses ffprobe to read the audio duration, for the tokens-per-minute-
    of-audio figure in the [cost] log line.
    """
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"[transcription] Warning: could not determine audio duration ({e})")
        return None


def _call_gemini_with_retry(contents, config, max_retries=4):
    """
    Calls Gemini's generate_content with exponential backoff retry
    for transient errors (429 rate limit, 503 overloaded). Re-raises
    the last error if all retries are exhausted, or immediately for
    non-retriable errors (e.g. 400, 401) - retrying those is pointless.
    """
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(
                model=settings.gemini_model,
                contents=contents,
                config=config,
            )
        except TRANSIENT_EXCEPTIONS as e:
            retriable = is_transient(e)
            code_str = error_code_str(e)
            if not retriable or attempt == max_retries - 1:
                raise
            delay = (2 ** attempt) + random.uniform(0, 1)
            print(f"[transcription] Gemini {code_str}, retrying in {delay:.1f}s (attempt {attempt+1}/{max_retries})")
            time.sleep(delay)


def transcribe_recording(meeting_id: str, storage_path: str) -> Optional[dict]:
    """
    Downloads the recording from Supabase Storage, sends it to Gemini
    for transcription and analysis, and writes the result to the meeting row.
    Returns None if the download fails (already marked failed in DB).
    """
    print(f"[transcription] Starting for meeting {meeting_id}, path: {storage_path}")

    db = SessionLocal()
    try:
        # Phase A4: attach the meeting and its owner to anything Sentry captures
        # from here on. Guarded on sentry_enabled() so the extra lookup only
        # happens where it buys something - with no SENTRY_DSN this is one
        # boolean and no query.
        if sentry_enabled():
            owner_id = db.query(Meeting.user_id).filter(Meeting.id == meeting_id).scalar()
            set_meeting_context(meeting_id, owner_id)

        resumed = _resume_if_work_already_done(db, meeting_id)
        if resumed is not _NOT_RESUMABLE:
            return resumed

        try:
            file_bytes = supabase.storage.from_(
                settings.supabase_recordings_bucket
            ).download(storage_path)
        except Exception as e:
            _mark_failed(db, meeting_id, f"Failed to download recording: {e}")
            return None  # explicit early return — no file to process

        suffix = os.path.splitext(storage_path)[1] or ".m4a"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        print(f"[transcription] Downloaded to temp file: {tmp_path}")
        audio_duration_sec = _get_audio_duration_seconds(tmp_path)

        uploaded_file = None
        try:
            # Phase 1, Step 3: Silence Detection
            if _is_audio_silent(tmp_path):
                print("[transcription] Audio is completely silent. Skipping Gemini API to prevent hallucinations.")

                meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
                date_str = meeting.created_at.strftime("%b %d, %Y") if meeting and meeting.created_at else time.strftime("%b %d, %Y")
                title = f"Silent recording - {date_str}"

                silent_result = {
                    "title": title,
                    "summary": "This meeting recording was completely silent.",
                    "key_points": ["No audio was detected during the recording (possibly everyone was muted or the meeting was empty)."],
                    "action_items": [],
                    "conclusion": "No discussion took place.",
                    "conversation": []
                }

                if meeting:
                    meeting.transcript = silent_result
                    meeting.title = title
                    meeting.status = "completed"
                    db.commit()

                return silent_result

            print("[transcription] Uploading to Gemini File API...")
            uploaded_file = client.files.upload(
                file=tmp_path,
                config=types.UploadFileConfig(mime_type="audio/mp4")
            )

            if uploaded_file.uri is None:
                raise ValueError("Gemini File API returned no URI after upload")

            print(f"[transcription] Uploaded. URI: {uploaded_file.uri}. Waiting for processing...")

            start_time = time.time()
            while True:
                if time.time() - start_time > 300:
                    raise TimeoutError(f"Gemini File API processing timed out after 5 minutes for file: {uploaded_file.name}")

                uploaded_file = client.files.get(name=uploaded_file.name)
                state_name = uploaded_file.state.name if hasattr(uploaded_file.state, "name") else uploaded_file.state
                if state_name == "ACTIVE":
                    break
                elif state_name == "FAILED":
                    raise ValueError(f"Gemini File API failed to process the audio file: {uploaded_file.name}")
                
                print(f"[transcription] File state is {state_name}. Waiting 2 seconds...")
                time.sleep(2)

            print("[transcription] File is ACTIVE. Sending to Gemini for analysis...")
            response = _call_gemini_with_retry(
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

            usage = response.usage_metadata
            prompt_tokens = (usage.prompt_token_count or 0) if usage else 0
            output_tokens = (usage.candidates_token_count or 0) if usage else 0
            transcription_cost = gemini_generation_cost(prompt_tokens, output_tokens)
            log_cost(
                "transcription",
                meeting=meeting_id,
                audio_sec=f"{audio_duration_sec:.1f}" if audio_duration_sec is not None else "unknown",
                in_tok=prompt_tokens,
                out_tok=output_tokens,
                usd=f"{transcription_cost:.6f}",
            )

            meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
            if meeting:
                meeting.transcript = result
                meeting.title = (result.get("title") or "").strip()[:60] or None
                meeting.status = "completed"
                db.commit()

            try:
                embedding_cost = index_transcript(db, meeting_id, result)
            except Exception as e:
                # Was swallowed here (meeting left "completed" with a note).
                # Under a queue that throws away a free repair: the retry
                # machinery can fix an indexing failure, and thanks to the
                # resume check the retry costs embedding only. The worker
                # writes that same note once retries are exhausted.
                raise IndexingFailed(str(e)) from e

            log_cost(
                "meeting_total",
                meeting=meeting_id,
                transcription_usd=f"{transcription_cost:.6f}",
                embedding_usd=f"{embedding_cost:.6f}",
                usd=f"{transcription_cost + embedding_cost:.6f}",
            )

            return result

        except IndexingFailed:
            # The transcript is saved and the meeting says "completed" - only
            # the RAG index is missing, so the `except Exception` below must
            # not turn this into a failed meeting. Let it out to the queue.
            raise

        except TRANSIENT_EXCEPTIONS as e:
            code_str = error_code_str(e)
            print(f"[transcription] Gemini API Error: {code_str}")

            is_retriable = is_transient(e)
            if is_retriable and settings.sarvam_api_key:
                # Retries already exhausted inside _call_gemini_with_retry -
                # Gemini is genuinely unavailable right now, switch providers.
                print(f"[transcription] Gemini {code_str} persisted after retries, falling back to Sarvam AI...")
                try:
                    result = transcribe_with_sarvam_fallback(tmp_path)
                    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
                    if meeting:
                        meeting.transcript = result
                        meeting.title = (result.get("title") or "").strip()[:60] or None
                        meeting.status = "completed"
                        meeting.error_message = f"Transcribed via Sarvam AI fallback (Gemini {code_str} unavailable)"
                        db.commit()
                    try:
                        index_transcript(db, meeting_id, result)
                    except Exception as idx_err:
                        # Same reasoning as the Gemini path above - retryable
                        # rather than swallowed.
                        raise IndexingFailed(str(idx_err)) from idx_err
                    return result
                except IndexingFailed:
                    # Sarvam succeeded and the transcript is saved - only the
                    # index is missing. This `try` wraps the indexing call, so
                    # without this clause the `except Exception` below caught
                    # IndexingFailed and marked a transcribed meeting failed,
                    # blaming Sarvam. Let it out to the queue, like the Gemini
                    # path; the `finally` cleanup still runs.
                    raise
                except Exception as fallback_err:
                    print(f"[transcription] Sarvam AI fallback also failed: {fallback_err}")
                    _mark_failed(
                        db, meeting_id,
                        f"Gemini {code_str} and Sarvam AI fallback both failed: {fallback_err}"
                    )
                return None

            # Non-retriable error (e.g. 400 bad request, 401 auth) or no
            # Sarvam key configured - no point falling back, just fail.
            if isinstance(e, errors.APIError):
                msg = f"Gemini Error: {e.message}"
                if e.code in (503, 504):
                    msg = f"Transcription failed: Gemini servers are currently overloaded ({e.code}). Please try again later."
            else:
                msg = f"Transcription failed: network error communicating with Gemini ({type(e).__name__}: {e})"
            _mark_failed(db, meeting_id, msg)
            
        except Exception as e:
            print(f"[transcription] ERROR: {e}")
            _mark_failed(db, meeting_id, f"Transcription failed: {str(e)}")
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
    finally:
        db.close()


_NOT_RESUMABLE = object()


def _resume_if_work_already_done(db, meeting_id: str):
    """
    Resume, don't restart. Returns _NOT_RESUMABLE when there is nothing to
    reuse and the full pipeline should run.

    A queue retry re-executes transcribe_recording directly - it never
    re-enters submit_transcription, so webhooks.py's
    `status.notin_(["transcribing", "completed"])` guard cannot help. Without
    the check below, every retry would re-download the recording, re-upload it
    to the Gemini File API and re-transcribe it from scratch. Transcription is
    the expensive call by a wide margin (log_cost's meeting_total line reports
    transcription_usd and embedding_usd separately, so the split is measured
    rather than assumed), and a crash-looping meeting would re-bill it on
    every attempt.

    Two resumable states:
      - completed with an embedding_provider -> the whole job is done.
      - a transcript already stored -> skip transcription, index only.
    """
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if meeting is None or not meeting.transcript:
        return _NOT_RESUMABLE

    if meeting.status == "completed" and meeting.embedding_provider:
        print(f"[transcription] meeting {meeting_id} is already transcribed and indexed - nothing to do")
        return meeting.transcript

    print(f"[transcription] meeting {meeting_id} already has a transcript - resuming at indexing")
    transcript = meeting.transcript
    try:
        index_transcript(db, meeting_id, transcript)
    except Exception as e:
        raise IndexingFailed(str(e)) from e

    if meeting.status != "completed":
        meeting.status = "completed"
        db.commit()
    return transcript


def _mark_failed(db, meeting_id: str, message: str) -> None:
    print(f"[transcription] Marking failed: {message}")
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if meeting:
        meeting.status = "failed"
        meeting.error_message = message
        db.commit()


def record_terminal_failure(meeting_id: str, exc: BaseException) -> None:
    """
    The last word on a job the queue has given up retrying, called by the
    worker once max_tries is exhausted. Replaces what submit_transcription's
    _on_done callback does on the executor path - a crashed job must not leave
    a meeting sitting in a non-terminal status forever.

    Branches on whether a transcript survived, because "failed" is the wrong
    answer for half of these. A meeting that transcribed fine and only failed
    to index has a usable transcript, summary and action items; marking it
    failed would take a working recording away from the user over a RAG
    problem. That case keeps "completed" and records the note the old inline
    handler used to write immediately - and stays findable with the repair
    query in docs/scaling-plan.md:

        SELECT id FROM meetings WHERE status = 'completed'
                                  AND embedding_provider IS NULL;
    """
    db = SessionLocal()
    try:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if meeting is None:
            return
        if meeting.transcript:
            meeting.error_message = f"Transcript ready, but RAG indexing failed: {exc}"
            print(f"[transcription] meeting {meeting_id} kept as completed; indexing gave up: {exc}")
            db.commit()
            return
        meeting.status = "failed"
        meeting.error_message = f"Transcription failed after repeated attempts: {exc}"
        print(f"[transcription] meeting {meeting_id} marked failed after exhausted retries: {exc}")
        db.commit()
    finally:
        db.close()


async def _enqueue(meeting_id: str, storage_path: str):
    """
    One short-lived Redis connection per enqueue, rather than a module-level
    pool. Transcriptions are enqueued once per meeting, so the connection cost
    is irrelevant, and it sidesteps the real hazard: an asyncio Redis pool is
    bound to the event loop that created it, and submit_transcription is
    called from sync route handlers running on whichever threadpool thread
    FastAPI happened to pick.

    No fixed _job_id, deliberately. Deduplicating on meeting_id would make arq
    silently drop the user-initiated re-run behind POST /meetings/{id}/retry
    whenever an earlier job's result was still in Redis. A duplicate is cheap
    anyway - _resume_if_work_already_done returns immediately for a meeting
    that is already done.
    """
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        return await pool.enqueue_job("transcribe_job", meeting_id, storage_path)
    finally:
        await pool.aclose()


def _run_blocking(coro):
    """Runs a coroutine to completion from sync code, loop or no loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Called from the event loop thread: give the coroutine its own loop on a
    # worker thread instead of deadlocking on the running one.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _submit_to_executor(meeting_id: str, storage_path: str):
    """
    The pre-queue path. Wraps transcription_executor.submit() so an exception
    that escapes transcribe_recording's own try/except (e.g. the tempfile
    write at the top, which sits outside every handler - see
    docs/reliability-audit.md, finding #2) still marks the meeting failed
    instead of vanishing into an unchecked Future.
    """
    future = transcription_executor.submit(transcribe_recording, meeting_id, storage_path)

    def _on_done(f):
        exc = f.exception()
        if exc is None:
            return
        print(f"[transcription] Unhandled exception in background task for meeting {meeting_id}: {exc}")
        record_terminal_failure(meeting_id, exc)

    future.add_done_callback(_on_done)
    return future


def submit_transcription(meeting_id: str, storage_path: str):
    """
    The seam every call site goes through (meetings.py's retry endpoint and
    webhooks.py's recording-complete handler). Which side of the cutover we
    are on is decided here and nowhere else, so flipping
    TRANSCRIPTION_USE_QUEUE moves the whole pipeline without touching a single
    caller - and flipping it back is the rollback.
    """
    if settings.transcription_use_queue:
        job = _run_blocking(_enqueue(meeting_id, storage_path))
        print(f"[transcription] queued meeting {meeting_id} as job {getattr(job, 'job_id', None)}")
        return job
    return _submit_to_executor(meeting_id, storage_path)
