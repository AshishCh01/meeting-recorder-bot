"""
Phase B3, item 2 - the recording-complete webhook is safe to deliver twice.

meeting-bot's notifyBackend retries the final report up to three times (3s,
then 6s) when a request errors - including when the backend processed it and
the response was lost on the way back. So the same "completed" payload can
genuinely arrive twice, and the second copy must not start a second
transcription: that is a second paid Gemini run over the same audio, racing
the first to write the same transcript.

The guard is the conditional UPDATE in the completed branch, excluding
"transcribing" and "completed", and its rowcount == 0 -> "already_processed"
return. These tests pin that branch, the ownership check that runs before it,
and what a duplicate *progress ping* does, which has no rowcount branch and
so is asserted as it actually behaves rather than assumed to match.

Postgres only. submit_transcription and get_signed_recording_url are replaced
at the attributes webhooks.py calls through, with spies that count.
"""
import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import webhooks
from app.db.database import SessionLocal
from app.db.models import Meeting, User
from app.main import app

WEBHOOK_HEADERS = {"Authorization": "Bearer stub-token"}  # conftest's MEETING_BOT_BEARER_TOKEN


def make_meeting(db, user_id, *, status, **columns):
    meeting = Meeting(
        id=uuid.uuid4(),
        user_id=user_id,
        meeting_url="https://zoom.us/j/123456789",
        title="Weekly sync",
        platform="zoom",
        status=status,
        **columns,
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return meeting


def reread(meeting_id):
    """Always on a fresh connection - never the session under test."""
    other = SessionLocal()
    try:
        return other.query(Meeting).filter(Meeting.id == meeting_id).one_or_none()
    finally:
        other.close()


class Spy:
    """Thread-safe call recorder."""

    def __init__(self, returns=None):
        self.calls = []
        self._returns = returns
        self._lock = threading.Lock()

    def __call__(self, *args, **kwargs):
        with self._lock:
            self.calls.append(args)
        return self._returns(*args) if self._returns else None


@pytest.fixture
def transcription(monkeypatch):
    spy = Spy()
    monkeypatch.setattr(webhooks, "submit_transcription", spy)
    return spy


@pytest.fixture
def signed_url(monkeypatch):
    spy = Spy(returns=lambda path, *rest: f"https://signed/{path}")
    monkeypatch.setattr(webhooks, "get_signed_recording_url", spy)
    return spy


def completed_payload(user_id, meeting):
    return {
        "user_id": str(user_id),
        "meeting_id": str(meeting.id),
        "status": "completed",
        "recording_path": f"{user_id}/{meeting.id}/recording.m4a",
        "duration_seconds": 2400,
        "error_message": None,
    }


def post(payload, client=None):
    return (client or TestClient(app)).post("/webhooks/recording-complete", json=payload, headers=WEBHOOK_HEADERS)


# ---------------------------------------------------------------------------
# The completed report, delivered twice
# ---------------------------------------------------------------------------

def test_a_redelivered_completed_report_transcribes_once(db, user, transcription, signed_url):
    meeting = make_meeting(db, user.id, status="recording")
    payload = completed_payload(user.id, meeting)

    first = post(payload)
    assert first.status_code == 200
    assert first.json() == {"status": "received"}
    assert transcription.calls == [(str(meeting.id), payload["recording_path"])]
    after_first = reread(meeting.id)
    assert after_first.status == "transcribing"

    second = post(payload)
    assert second.status_code == 200, "a retried delivery must not be an error, or the bot retries again"
    assert second.json() == {"status": "already_processed"}
    assert len(transcription.calls) == 1, "the duplicate started a second transcription"

    after_second = reread(meeting.id)
    assert after_second.status == "transcribing"
    assert after_second.recording_url == after_first.recording_url
    assert after_second.duration_seconds == 2400


def test_two_deliveries_racing_each_other_transcribe_once(db, user, transcription, monkeypatch):
    """
    The retry lands while the first request is still inside the handler - a
    slow first response is exactly what makes the bot retry. Both requests are
    held at the signed-URL call, which comes before the UPDATE, and released
    together, so both have already read the meeting as "recording".

    Postgres serialises the two conditional UPDATEs on the row lock and the
    loser re-evaluates the WHERE against the winner's committed "transcribing",
    so exactly one gets rowcount 1.
    """
    barrier = threading.Barrier(2, timeout=15)

    def held_signed_url(path, *rest):
        barrier.wait()
        return f"https://signed/{path}"

    monkeypatch.setattr(webhooks, "get_signed_recording_url", held_signed_url)

    meeting = make_meeting(db, user.id, status="recording")
    payload = completed_payload(user.id, meeting)

    results = []
    errors = []
    lock = threading.Lock()

    def deliver():
        try:
            response = post(payload, TestClient(app))
            with lock:
                results.append((response.status_code, response.json()["status"]))
        except BaseException as e:  # noqa: BLE001 - re-raised below
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=deliver) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a delivery deadlocked"
    if errors:
        raise errors[0]

    assert sorted(results) == [(200, "already_processed"), (200, "received")]
    assert transcription.calls == [(str(meeting.id), payload["recording_path"])]
    assert reread(meeting.id).status == "transcribing"


def test_a_completed_report_for_a_finished_meeting_is_not_transcribed_again(db, user, transcription, signed_url):
    """
    The third delivery, arriving after transcription already finished. Same
    idempotent branch - and the transcript that is already there survives.
    """
    transcript = {"segments": [{"speaker": "A", "text": "hello"}]}
    meeting = make_meeting(
        db, user.id, status="completed",
        transcript=transcript, recording_url="https://signed/original", duration_seconds=2400,
    )

    response = post(completed_payload(user.id, meeting))

    assert response.json() == {"status": "already_processed"}
    assert transcription.calls == []
    row = reread(meeting.id)
    assert row.status == "completed"
    assert row.transcript == transcript
    assert row.recording_url == "https://signed/original"


# ---------------------------------------------------------------------------
# Ownership is checked before anything is written
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["completed", "failed", "waiting_for_admission", "recording"])
def test_a_mismatched_user_id_is_refused_before_any_state_changes(db, user, transcription, signed_url, status):
    other = User(email="someone-else@example.com", bot_display_name="Other")
    db.add(other)
    db.commit()

    meeting = make_meeting(db, user.id, status="joining")
    before = reread(meeting.id)

    payload = completed_payload(other.id, meeting)
    payload["status"] = status
    response = post(payload)

    assert response.status_code == 409
    assert transcription.calls == []
    assert signed_url.calls == [], "storage was reached for a meeting that is not this user's"
    row = reread(meeting.id)
    assert row.status == "joining"
    assert row.error_message is None
    assert row.recording_url is None
    assert row.updated_at == before.updated_at, "the row was written before the 409"


# ---------------------------------------------------------------------------
# Duplicate progress pings - no rowcount branch, so asserted as they behave
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ping", ["waiting_for_admission", "recording"])
def test_a_duplicate_progress_ping_is_a_no_op_not_an_error(db, user, transcription, signed_url, ping):
    """
    What actually happens on the second copy: the UPDATE's exclusion list does
    not exclude the status it writes, so it matches the row again and writes
    the same value. The response is "received" both times - there is no
    "already_processed" shape here, unlike the completed branch - and the only
    visible effect is that updated_at moves, restarting that status's watchdog
    clock. Bounded: the bot sends each ping once, with no retry.
    """
    meeting = make_meeting(db, user.id, status="joining")
    payload = {"user_id": str(user.id), "meeting_id": str(meeting.id), "status": ping}

    first = post(payload)
    after_first = reread(meeting.id)

    second = post(payload)
    after_second = reread(meeting.id)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == {"status": "received"}
    assert after_first.status == after_second.status == ping
    assert after_second.error_message is None
    assert after_second.updated_at >= after_first.updated_at
    assert transcription.calls == []
    assert signed_url.calls == []


def test_a_duplicate_failed_report_keeps_the_meeting_failed_with_its_message(db, user, transcription, signed_url):
    """The failure path retries the same way; the second copy rewrites the same row, harmlessly."""
    meeting = make_meeting(db, user.id, status="recording")
    payload = {
        "user_id": str(user.id),
        "meeting_id": str(meeting.id),
        "status": "failed",
        "error_message": "Removed from meeting by host",
    }

    assert post(payload).json() == {"status": "received"}
    assert post(payload).json() == {"status": "received"}

    row = reread(meeting.id)
    assert row.status == "failed"
    assert row.error_message == "Removed from meeting by host"
    assert transcription.calls == []
