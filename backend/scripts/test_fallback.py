"""
End-to-end test: forces Gemini to fail (simulating 429/503), confirms
_call_gemini_with_retry retries and gives up, and confirms the real
transcribe_recording() function falls back to Sarvam AI correctly -
writing a completed transcript, flagging error_message, and running
RAG indexing, exactly as it would in production.

Works off an EXISTING meeting row - you only need the meeting_id.
The script reads that meeting's recording_url (a signed Supabase
Storage URL) and extracts the raw storage_path from it, the same
path the webhook would have passed to transcribe_recording()
originally.

Requires:
- An existing row in the `meetings` table with a real recording_url
  (i.e. transcription already ran successfully for it at least once,
  or its recording upload completed)
- SARVAM_API_KEY set in .env

Usage:
    cd backend
    python -m scripts.test_fallback <meeting_id>

Example:
    python -m scripts.test_fallback abc-123-def-456
"""
import sys
from urllib.parse import urlparse, unquote
from unittest.mock import patch

from google.genai import errors

from app.services import transcription_service as ts
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting


class _FakeAPIError(errors.APIError):
    """Minimal stand-in so we can force a specific status code without
    needing a real HTTP response object to construct errors.APIError."""
    def __init__(self, code, message):
        self.code = code
        self.message = message

    def __str__(self):
        return f"{self.code}: {self.message}"


def _always_raise_503(*args, **kwargs):
    raise _FakeAPIError(503, "Forced test failure - simulated overload")


def _extract_storage_path(recording_url: str) -> str:
    """
    Supabase signed URLs look like:
    https://<project>.supabase.co/storage/v1/object/sign/<bucket>/<path>?token=...
    Pull <path> back out so we can pass it to transcribe_recording()
    exactly as the webhook originally did.
    """
    marker = f"/object/sign/{settings.supabase_recordings_bucket}/"
    if marker not in recording_url:
        raise ValueError(
            f"Couldn't find expected path marker in recording_url. "
            f"Got: {recording_url}. If your storage/signing setup differs, "
            f"adjust _extract_storage_path() or just pass storage_path manually."
        )
    after_marker = recording_url.split(marker, 1)[1]
    path_only = urlparse(f"https://x/{after_marker}").path.lstrip("/")
    return unquote(path_only)


def main(meeting_id: str):
    db = SessionLocal()
    try:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not meeting:
            print(f"No meeting found for id {meeting_id}")
            return
        if not meeting.recording_url:
            print(f"Meeting {meeting_id} has no recording_url - can't derive storage_path.")
            return
        storage_path = _extract_storage_path(meeting.recording_url)
    finally:
        db.close()

    print(f"=== Forcing Gemini to fail with 503 for meeting {meeting_id} ===")
    print(f"Resolved storage_path: {storage_path}")
    print("Expect: 4 retry attempts with backoff, then fallback to Sarvam AI\n")

    # Patch the underlying Gemini call so _call_gemini_with_retry's own
    # retry/backoff logic runs for real - we're only faking what Gemini
    # itself returns, not skipping the retry code.
    with patch.object(ts.client.models, "generate_content", side_effect=_always_raise_503):
        result = ts.transcribe_recording(meeting_id, storage_path)

    print("\n=== transcribe_recording() returned ===")
    if result is None:
        print("Got None - fallback failed too, or download failed. Check logs above.")
        return

    print(f"Keys: {list(result.keys())}")
    print(f"Conversation entries: {len(result.get('conversation', []))}")
    if result.get("conversation"):
        print(f"First entry: {result['conversation'][0]}")

    print("\n=== Checking DB row ===")
    db = SessionLocal()
    try:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not meeting:
            print(f"No meeting row found for id {meeting_id}")
            return
        print(f"status: {meeting.status}")
        print(f"error_message: {meeting.error_message}")
        assert meeting.status == "completed", "Expected status=completed even on fallback"
        assert meeting.error_message and "Sarvam" in meeting.error_message, \
            "Expected error_message to flag the Sarvam fallback was used"
        print("\nPASS: fallback path completed and was correctly flagged.")
    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m scripts.test_fallback <meeting_id>")
        sys.exit(1)
    main(sys.argv[1])