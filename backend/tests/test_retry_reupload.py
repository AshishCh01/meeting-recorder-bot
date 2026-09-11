"""
Retry recovers a recording whose upload failed.

meeting-bot now keeps the local file when an upload fails, and exposes
POST /reupload to try again. retry_meeting falls back to it when storage has
no file. These pin the part that breaks silently: the meeting must be
"uploading" - not "transcribing" - when the bot's completed webhook arrives,
or the webhook's notin_(["transcribing", "completed"]) guard rejects it as
already processed and the recovered recording is never transcribed.

Postgres only. Storage, the bot and transcription are faked at the module
attributes the routes call through.
"""
import uuid

import httpx
import pytest
from fastapi import HTTPException

from app.api import meetings, webhooks
from app.db.database import SessionLocal
from app.db.models import Meeting
from app.models.meeting import RecordingCompleteWebhook


def make_failed_meeting(db, user_id):
    meeting = Meeting(
        id=uuid.uuid4(),
        user_id=user_id,
        meeting_url="https://zoom.us/j/123456789",
        title="Upload failed",
        platform="zoom",
        status="failed",
        error_message="Upload failed: fetch failed. The recording is preserved on the recorder and can be retried.",
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return meeting


def reread(meeting_id):
    other = SessionLocal()
    try:
        return other.query(Meeting).filter(Meeting.id == meeting_id).one_or_none()
    finally:
        other.close()


class FakeStorage:
    def __init__(self, has_recording):
        self.has_recording = has_recording

    @property
    def storage(self):
        return self

    def from_(self, bucket):
        return self

    def list(self, folder):
        return [{"name": "recording.m4a"}] if self.has_recording else []


@pytest.fixture
def transcriptions(monkeypatch):
    submitted = []
    record = lambda meeting_id, path: submitted.append((str(meeting_id), path))
    monkeypatch.setattr(meetings, "submit_transcription", record)
    monkeypatch.setattr(webhooks, "submit_transcription", record)
    monkeypatch.setattr(webhooks, "get_signed_recording_url", lambda path, *a, **k: f"https://signed/{path}")
    return submitted


def fake_bot(monkeypatch, respond):
    """respond(url, json) -> status code, or raises. Records every call."""
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "status_at_call": reread(json["meetingId"]).status})
        status = respond(url, json)
        return httpx.Response(status, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    return calls


def retry(db, user, meeting):
    return meetings.retry_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id))


def completed_webhook(db, user, meeting, **overrides):
    payload = {
        "user_id": str(user.id),
        "meeting_id": str(meeting.id),
        "status": "completed",
        "recording_path": f"{user.id}/{meeting.id}/recording.m4a",
        "duration_seconds": None,
        "error_message": None,
        **overrides,
    }
    return webhooks.recording_complete(payload=RecordingCompleteWebhook(**payload), db=db)


def test_a_kept_recording_is_reuploaded_and_its_completed_webhook_is_accepted(db, user, transcriptions, monkeypatch):
    meeting = make_failed_meeting(db, user.id)
    monkeypatch.setattr(meetings, "supabase", FakeStorage(has_recording=False))
    calls = fake_bot(monkeypatch, lambda url, body: 202)

    assert retry(db, user, meeting) == {"status": "reuploading"}

    assert len(calls) == 1
    assert calls[0]["url"].endswith("/reupload")
    assert calls[0]["json"] == {"meetingId": str(meeting.id), "userId": str(user.id)}
    assert calls[0]["status_at_call"] == "uploading", "uploading must be committed before the bot is called"
    row = reread(meeting.id)
    assert row.status == "uploading"
    assert row.error_message is None
    assert transcriptions == [], "nothing is in storage yet to transcribe"

    # The bot finishes the upload and reports the normal completion.
    assert completed_webhook(db, user, meeting) == {"status": "received"}

    assert reread(meeting.id).status == "transcribing"
    assert transcriptions == [(str(meeting.id), f"{user.id}/{meeting.id}/recording.m4a")]


def test_a_reupload_that_fails_again_lands_back_at_failed(db, user, transcriptions, monkeypatch):
    meeting = make_failed_meeting(db, user.id)
    monkeypatch.setattr(meetings, "supabase", FakeStorage(has_recording=False))
    fake_bot(monkeypatch, lambda url, body: 202)
    retry(db, user, meeting)

    preserved = "Upload failed: fetch failed. The recording is preserved on the recorder and can be retried."
    completed_webhook(db, user, meeting, status="failed", recording_path=None, error_message=preserved)

    row = reread(meeting.id)
    assert row.status == "failed"
    assert row.error_message == preserved
    assert transcriptions == []


def test_a_recording_on_neither_storage_nor_the_bot_is_not_found(db, user, transcriptions, monkeypatch):
    meeting = make_failed_meeting(db, user.id)
    monkeypatch.setattr(meetings, "supabase", FakeStorage(has_recording=False))
    fake_bot(monkeypatch, lambda url, body: 404)

    with pytest.raises(HTTPException) as exc:
        retry(db, user, meeting)

    assert exc.value.status_code == 404
    assert exc.value.detail == "Recording file not found in storage. Cannot retry."
    row = reread(meeting.id)
    assert row.status == "failed"
    assert row.error_message == "Recording file not found in storage. Cannot retry."


def test_an_unreachable_bot_puts_the_meeting_back_to_failed(db, user, transcriptions, monkeypatch):
    meeting = make_failed_meeting(db, user.id)
    monkeypatch.setattr(meetings, "supabase", FakeStorage(has_recording=False))

    def unreachable(url, body):
        raise httpx.ConnectError("connection refused")

    fake_bot(monkeypatch, unreachable)

    with pytest.raises(HTTPException) as exc:
        retry(db, user, meeting)

    assert exc.value.status_code == 502
    row = reread(meeting.id)
    assert row.status == "failed", "a failed bot call must not strand the meeting in uploading"
    assert "re-upload" in row.error_message


def test_a_recording_already_in_storage_still_retries_transcription_without_the_bot(db, user, transcriptions, monkeypatch):
    meeting = make_failed_meeting(db, user.id)
    monkeypatch.setattr(meetings, "supabase", FakeStorage(has_recording=True))
    calls = fake_bot(monkeypatch, lambda url, body: 202)

    assert retry(db, user, meeting) == {"status": "retrying"}

    assert calls == []
    assert reread(meeting.id).status == "transcribing"
    assert transcriptions == [(str(meeting.id), f"{user.id}/{meeting.id}/recording.m4a")]
