"""
B3 item 3 - the transcription fallback ladder: Gemini -> Sarvam.

_call_gemini_with_retry retries generate_content up to 4 times on a transient
failure (gemini_errors.is_transient: 429, 503, 504, httpx connection errors)
and re-raises; transcribe_recording then switches to Sarvam if a Sarvam key is
configured. A non-transient error is raised on the first attempt and fails
the meeting without calling Sarvam. With no Sarvam key, a transient error
fails the meeting with a message naming what happened.

Already covered in test_transcription_queue.py and not repeated here:
Gemini 503 -> Sarvam succeeds (and indexes), Sarvam itself failing, and
Sarvam -> indexing failure (B3 bug 2). This file adds the rest of the ladder:
429, 504 and the connection errors reaching Sarvam; 400/401/404 not reaching
it; and the no-key messages.

Gemini fails at client.models.generate_content, through the real retry loop
(backoff sleeps skipped). Sarvam is the same SDK-level fake the queue tests
use. Storage, the File API and ffmpeg are stubbed. Postgres only.
"""
import json
import os
import uuid

import pytest

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting
from app.services import transcription_fallback_sarvam, transcription_service

from tests.fake_ai_providers import NON_TRANSIENT, TRANSIENT, install_fast_sleeps
from tests.test_transcription_queue import _FakeSarvamClient, _FakeUploadedFile


def make_meeting(db, user_id):
    meeting = Meeting(
        id=uuid.uuid4(), user_id=user_id, meeting_url="https://meet.google.com/abc-defg-hij",
        platform="google", status="transcribing",
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return meeting


def reread(meeting_id):
    other = SessionLocal()
    try:
        return other.query(Meeting).filter(Meeting.id == meeting_id).one()
    finally:
        other.close()


@pytest.fixture
def ladder(monkeypatch):
    """
    Everything around the two providers stubbed, and a shared call log:
    "gemini:n" per generate_content, "sarvam" per Sarvam SDK client use,
    "index" per index_transcript, "file-delete" per Gemini File API cleanup.
    """
    log = []
    state = {"log": log, "gemini_failures": [], "tmp_paths": []}

    class _Storage:
        def from_(self, bucket):
            return self

        def download(self, path):
            return b"fake audio bytes"

    monkeypatch.setattr(transcription_service, "supabase", type("C", (), {"storage": _Storage()})())
    monkeypatch.setattr(transcription_service, "sentry_enabled", lambda: False)
    monkeypatch.setattr(transcription_service, "_is_audio_silent", lambda path: False)

    def duration(path):
        state["tmp_paths"].append(path)
        return 42.0

    monkeypatch.setattr(transcription_service, "_get_audio_duration_seconds", duration)

    uploaded = _FakeUploadedFile()
    monkeypatch.setattr(transcription_service.client.files, "upload", lambda **kw: uploaded)
    monkeypatch.setattr(transcription_service.client.files, "get", lambda **kw: uploaded)
    monkeypatch.setattr(transcription_service.client.files, "delete", lambda **kw: log.append("file-delete"))

    def generate_content(**kwargs):
        log.append(f"gemini:{sum(1 for e in log if e.startswith('gemini:')) + 1}")
        if state["gemini_failures"]:
            raise state["gemini_failures"].pop(0)()
        return type("R", (), {"text": json.dumps({"title": "Via Gemini", "conversation": []}), "usage_metadata": None})()

    monkeypatch.setattr(transcription_service.client.models, "generate_content", generate_content)

    sarvam = _FakeSarvamClient()

    def sarvam_client():
        log.append("sarvam")
        return sarvam

    monkeypatch.setattr(transcription_fallback_sarvam, "get_sarvam_client", sarvam_client)
    monkeypatch.setattr(transcription_service, "index_transcript", lambda db, mid, t: (log.append("index"), 0.0)[1])
    monkeypatch.setattr(settings, "sarvam_api_key", "stub-sarvam-key")
    state["slept"] = install_fast_sleeps(monkeypatch)
    state["sarvam"] = sarvam
    return state


def gemini_calls(log):
    return [e for e in log if e.startswith("gemini:")]


@pytest.mark.parametrize("failure", ["429", "504", "connect-error", "timeout", "dropped-connection"])
def test_a_transient_gemini_failure_falls_back_to_sarvam_after_all_four_attempts(db, user, ladder, failure):
    """503 is the case test_transcription_queue.py already covers; these are the rest."""
    meeting = make_meeting(db, user.id)
    ladder["gemini_failures"] = [TRANSIENT[failure]] * 4

    result = transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    # The STT job and the analysis each use the Sarvam client once, then the
    # transcript is indexed once, then the File API upload is cleaned up once.
    assert ladder["log"] == ["gemini:1", "gemini:2", "gemini:3", "gemini:4", "sarvam", "sarvam", "index", "file-delete"]
    assert len(ladder["slept"]) == 3
    assert ladder["sarvam"].stt_jobs == 1
    assert result["title"] == "Roadmap via Sarvam"
    row = reread(meeting.id)
    assert row.status == "completed"
    label = failure if failure.isdigit() else type(TRANSIENT[failure]()).__name__
    assert row.error_message == f"Transcribed via Sarvam AI fallback (Gemini {label} unavailable)"
    assert not os.path.exists(ladder["tmp_paths"][0]), "the downloaded recording was left on disk"


def test_a_transient_failure_that_clears_within_the_retries_never_reaches_sarvam(db, user, ladder):
    meeting = make_meeting(db, user.id)
    ladder["gemini_failures"] = [TRANSIENT["504"]] * 3

    result = transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    assert ladder["log"] == ["gemini:1", "gemini:2", "gemini:3", "gemini:4", "index", "file-delete"]
    assert ladder["sarvam"].stt_jobs == 0
    assert result["title"] == "Via Gemini"
    assert reread(meeting.id).error_message is None


@pytest.mark.parametrize("failure", list(NON_TRANSIENT))
def test_a_non_transient_gemini_error_fails_the_meeting_without_retrying_or_sarvam(db, user, ladder, failure):
    meeting = make_meeting(db, user.id)
    ladder["gemini_failures"] = [NON_TRANSIENT[failure]]

    assert transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a") is None

    assert ladder["log"] == ["gemini:1", "file-delete"]
    assert ladder["slept"] == []
    row = reread(meeting.id)
    assert row.status == "failed"
    assert row.error_message == f"Gemini Error: {NON_TRANSIENT[failure]().message}"
    assert row.transcript is None
    assert not os.path.exists(ladder["tmp_paths"][0])


@pytest.mark.parametrize("failure, message", [
    ("503", "Transcription failed: Gemini servers are currently overloaded (503). Please try again later."),
    ("504", "Transcription failed: Gemini servers are currently overloaded (504). Please try again later."),
    ("429", "Gemini Error: stub RESOURCE_EXHAUSTED"),
    ("connect-error", "Transcription failed: network error communicating with Gemini (ConnectError: connection refused)"),
])
def test_with_no_sarvam_key_a_transient_failure_fails_the_meeting_with_a_clear_message(db, user, ladder, monkeypatch, failure, message):
    monkeypatch.setattr(settings, "sarvam_api_key", "")
    meeting = make_meeting(db, user.id)
    ladder["gemini_failures"] = [TRANSIENT[failure]] * 4

    assert transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a") is None

    assert ladder["log"] == ["gemini:1", "gemini:2", "gemini:3", "gemini:4", "file-delete"]
    assert ladder["sarvam"].stt_jobs == 0
    row = reread(meeting.id)
    assert row.status == "failed"
    assert row.error_message == message
