"""
B3 finding 4 - progress pings and "uploading": checked, and deliberately unchanged.

Neither ping's exclusion list in webhooks.py names "uploading", so a
"waiting_for_admission" or "recording" ping will overwrite it. The finding
asked whether that can move a meeting backwards in practice. Traced:

  - "uploading" is written in exactly one place: retry's _reupload_from_bot,
    on a "failed" meeting, immediately before it asks meeting-bot to
    re-upload a recording the bot kept.
  - Pings are sent only from inside a live runMeetingLifecycle session, once
    each at admission and at recording start - single attempt, no retry.
  - meeting-bot's POST /reupload answers 409 while that meeting's session is
    still active (server.js: `activeMeetings.has(meetingId)`), and a
    re-upload session never pings.

So a re-upload the bot *accepts* can never be reached by a ping: there is no
session left to send one. The one interleaving that can happen is a meeting
the backend failed while its bot was in fact still running - the watchdog
swept "joining" because both single-attempt pings were lost - where the user
presses Retry, the backend writes "uploading", and a ping from that live
session lands before the bot's 409. That ping is not stale. It is the only
signal that the meeting is being recorded right now, "recording" is the true
state, and the failure write that follows the 409 is guarded on "uploading" so
it does not overwrite it. The session's final report then closes the meeting.

Adding "uploading" to the exclusion lists would change only that case, and for
the worse: the ping would be dropped and the meeting shown as failed while it
records. So there is no code change here - this test pins the traced
behaviour, so a future exclusion change has to argue with it.

Not covered from this side: the 409-while-active guard itself lives in
meeting-bot, whose suite does not currently test it.

Postgres only. The bot and storage are faked at the attributes the routes call.
"""
import uuid

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import meetings, webhooks
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting
from app.main import app
from app.services import watchdog

from tests.test_meeting_status_machine import age_meeting

WEBHOOK_HEADERS = {"Authorization": "Bearer stub-token"}  # conftest's MEETING_BOT_BEARER_TOKEN


def reread(meeting_id):
    other = SessionLocal()
    try:
        return other.query(Meeting).filter(Meeting.id == meeting_id).one_or_none()
    finally:
        other.close()


class _EmptyStorage:
    @property
    def storage(self):
        return self

    def from_(self, bucket):
        return self

    def list(self, folder):
        return []


def ping(user, meeting, status, **extra):
    body = {"user_id": str(user.id), "meeting_id": str(meeting.id), "status": status, **extra}
    return TestClient(app).post("/webhooks/recording-complete", json=body, headers=WEBHOOK_HEADERS)


@pytest.mark.parametrize("live_ping", ["recording", "waiting_for_admission"])
def test_a_ping_from_a_live_session_during_a_refused_reupload_is_kept_and_the_meeting_still_closes(db, user, monkeypatch, live_ping):
    monkeypatch.setattr(settings, "watchdog_enabled", True)
    monkeypatch.setattr(settings, "watchdog_joining_ttl_minutes", 10)
    submitted = []
    monkeypatch.setattr(webhooks, "submit_transcription", lambda mid, path: submitted.append(mid))
    monkeypatch.setattr(webhooks, "get_signed_recording_url", lambda path, *a, **k: f"https://signed/{path}")
    monkeypatch.setattr(meetings, "supabase", _EmptyStorage())

    # Both pings were lost; the bot is really in the call.
    meeting = Meeting(
        id=uuid.uuid4(), user_id=user.id, meeting_url="https://zoom.us/j/123456789",
        title="Lost pings", platform="zoom", status="joining",
    )
    db.add(meeting)
    db.commit()
    age_meeting(meeting.id, minutes=11)
    assert watchdog.sweep_stale_meetings(db) == 1
    assert reread(meeting.id).status == "failed"

    status_seen_by_ping = []

    def bot(url, json=None, headers=None, timeout=None):
        # The live session's ping lands while Retry is waiting on the bot.
        status_seen_by_ping.append(reread(meeting.id).status)
        assert ping(user, meeting, live_ping).status_code == 200
        return httpx.Response(409, json={"error": "still active"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", bot)

    with pytest.raises(HTTPException):
        meetings.retry_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id))

    assert status_seen_by_ping == ["uploading"], "the window was not staged"
    row = reread(meeting.id)
    assert row.status == live_ping, "the live session's ping was dropped, or overwritten by the 409's failure write"

    # The session ends and reports as usual.
    body = {"recording_path": f"{user.id}/{meeting.id}/recording.m4a", "duration_seconds": 600}
    assert ping(user, meeting, "completed", **body).json() == {"status": "received"}
    assert reread(meeting.id).status == "transcribing"
    assert submitted == [str(meeting.id)]
