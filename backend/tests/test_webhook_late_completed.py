"""
B3 finding 2 - a late "completed" report must not re-transcribe a meeting.

The completed branch claimed any meeting not already "transcribing" or
"completed" - "failed" included. Some of those claims are exactly right, and
one kind is a second paid transcription of audio that was already handed off.

How a "completed" report reaches a "failed" meeting:

  Legitimate - the recording was never handed to transcription:
    - The watchdog swept joining / waiting_for_admission / recording /
      uploading while the bot was still working, and its report came late.
    - The webhook's own signed-URL check failed ("file not found in storage"),
      and the report is redelivered once the file is there.
    - Finding 1's undo: the hand-off failed and the bot retries.
    - Dispatch or the scheduler wrote "failed" for a bot that did in fact start.

  Not legitimate - a report for this recording was already accepted:
    - Transcription failed after the hand-off (download error, Gemini gave up,
      the queue's retries ran out) and meeting-bot redelivers the same report -
      it retries on any error, including a lost response to a request the
      backend completed.
    - The watchdog swept "transcribing" and a redelivery arrives.

The two are told apart by recording_url. The completed claim is the only
writer that sets it, and finding 1's undo clears it again. So the rule is:

    A completed report may claim a "failed" meeting only if no completed
    report has ever been accepted for it (recording_url IS NULL).

A meeting blocked by the rule gets the same "already_processed" answer as any
other duplicate, keeps its failure message, and is recovered the way every
failed meeting is: POST /meetings/{id}/retry, which is unchanged.

Postgres only. The watchdog-swept cases use the real sweep.
"""
import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import meetings, webhooks
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting
from app.main import app
from app.services import transcription_service, watchdog

from tests.test_meeting_status_machine import age_meeting

WEBHOOK_HEADERS = {"Authorization": "Bearer stub-token"}  # conftest's MEETING_BOT_BEARER_TOKEN


def make_meeting(db, user_id, *, status, **columns):
    meeting = Meeting(
        id=uuid.uuid4(),
        user_id=user_id,
        meeting_url="https://meet.google.com/abc-defg-hij",
        title="Retro",
        platform="google",
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


def completed_payload(user, meeting):
    return {
        "user_id": str(user.id),
        "meeting_id": str(meeting.id),
        "status": "completed",
        "recording_path": f"{user.id}/{meeting.id}/recording.m4a",
        "duration_seconds": 900,
        "error_message": None,
    }


def deliver(payload, client=None):
    return (client or TestClient(app)).post("/webhooks/recording-complete", json=payload, headers=WEBHOOK_HEADERS)


class Pipeline:
    """Storage and transcription hand-off, faked where the webhook calls them."""

    def __init__(self):
        self.submitted = []
        self.signed_url_error = None
        self._lock = threading.Lock()

    def signed_url(self, path, *args, **kwargs):
        if self.signed_url_error is not None:
            raise self.signed_url_error
        return f"https://signed/{path}"

    def submit(self, meeting_id, storage_path):
        with self._lock:
            self.submitted.append(meeting_id)


@pytest.fixture
def pipeline(monkeypatch):
    fake = Pipeline()
    monkeypatch.setattr(webhooks, "get_signed_recording_url", fake.signed_url)
    monkeypatch.setattr(webhooks, "submit_transcription", fake.submit)
    return fake


@pytest.fixture
def watchdog_on(monkeypatch):
    monkeypatch.setattr(settings, "watchdog_enabled", True)
    monkeypatch.setattr(settings, "watchdog_joining_ttl_minutes", 10)
    monkeypatch.setattr(settings, "watchdog_admission_ttl_minutes", 10)
    monkeypatch.setattr(settings, "max_recording_duration_minutes", 90)
    monkeypatch.setattr(settings, "watchdog_recording_margin_minutes", 15)
    monkeypatch.setattr(settings, "watchdog_uploading_ttl_minutes", 20)
    monkeypatch.setattr(settings, "watchdog_transcribing_ttl_minutes", 30)


def fail_transcription(meeting_id, message):
    """What transcription_service writes when a handed-off job fails."""
    db = SessionLocal()
    try:
        transcription_service._mark_failed(db, meeting_id, message)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# a. completed -> duplicate completed
# ---------------------------------------------------------------------------

def test_a_duplicate_after_transcription_finished_is_already_processed(db, user, pipeline):
    meeting = make_meeting(db, user.id, status="recording")
    payload = completed_payload(user, meeting)

    assert deliver(payload).json() == {"status": "received"}
    db.query(Meeting).filter(Meeting.id == meeting.id).update({"status": "completed", "transcript": {"summary": "done"}})
    db.commit()

    assert deliver(payload).json() == {"status": "already_processed"}
    assert pipeline.submitted == [str(meeting.id)]
    assert reread(meeting.id).status == "completed"


# ---------------------------------------------------------------------------
# b. failed -> completed, after a hand-off: not a recovery
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("failure", [
    "Failed to download recording: 404",
    "Transcription failed after repeated attempts: 429 RESOURCE_EXHAUSTED",
])
def test_a_redelivery_after_transcription_failed_does_not_transcribe_again(db, user, pipeline, failure):
    """The finding: redelivered inside the bot's retry window, after a fast failure."""
    meeting = make_meeting(db, user.id, status="recording")
    payload = completed_payload(user, meeting)

    assert deliver(payload).json() == {"status": "received"}
    fail_transcription(str(meeting.id), failure)

    response = deliver(payload)

    assert response.status_code == 200, "a redelivery must not be an error, or the bot retries it again"
    assert response.json() == {"status": "already_processed"}
    assert pipeline.submitted == [str(meeting.id)], "the same recording was handed to transcription twice"
    row = reread(meeting.id)
    assert row.status == "failed"
    assert row.error_message == failure, "the real failure was overwritten"


def test_a_redelivery_after_the_watchdog_swept_transcribing_does_not_transcribe_again(db, user, pipeline, watchdog_on):
    meeting = make_meeting(db, user.id, status="recording")
    payload = completed_payload(user, meeting)

    deliver(payload)
    age_meeting(meeting.id, minutes=31)
    assert watchdog.sweep_stale_meetings(db) == 1
    assert reread(meeting.id).status == "failed"

    assert deliver(payload).json() == {"status": "already_processed"}
    assert pipeline.submitted == [str(meeting.id)]
    assert reread(meeting.id).status == "failed"


def test_retry_still_recovers_a_meeting_the_rule_blocked(db, user, pipeline, monkeypatch):
    """The recovery path for a handed-off failure is the user's Retry, and it is untouched."""
    meeting = make_meeting(db, user.id, status="recording")
    payload = completed_payload(user, meeting)
    deliver(payload)
    fail_transcription(str(meeting.id), "Failed to download recording: timeout")
    assert deliver(payload).json() == {"status": "already_processed"}

    retried = []
    monkeypatch.setattr(meetings, "submit_transcription", lambda mid, path: retried.append(mid))
    monkeypatch.setattr(meetings, "supabase", _StorageWithRecording())

    assert meetings.retry_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id)) == {"status": "retrying"}
    assert retried == [str(meeting.id)]
    assert reread(meeting.id).status == "transcribing"


class _StorageWithRecording:
    @property
    def storage(self):
        return self

    def from_(self, bucket):
        return self

    def list(self, folder):
        return [{"name": "recording.m4a"}]


# ---------------------------------------------------------------------------
# d. failed -> completed, never handed off: a recovery, and still allowed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("swept_status, age", [
    ("joining", 11),
    ("waiting_for_admission", 11),
    ("recording", 106),
    ("uploading", 21),
])
def test_a_late_report_recovers_a_meeting_the_watchdog_failed_before_any_handoff(db, user, pipeline, watchdog_on, swept_status, age):
    meeting = make_meeting(db, user.id, status=swept_status)
    age_meeting(meeting.id, minutes=age)
    assert watchdog.sweep_stale_meetings(db) == 1
    assert reread(meeting.id).status == "failed"

    response = deliver(completed_payload(user, meeting))

    assert response.json() == {"status": "received"}
    assert pipeline.submitted == [str(meeting.id)]
    row = reread(meeting.id)
    assert row.status == "transcribing"
    assert row.error_message is None, "a recovered meeting still says it timed out"


def test_a_redelivery_recovers_a_meeting_whose_file_was_not_in_storage_yet(db, user, pipeline):
    meeting = make_meeting(db, user.id, status="recording")
    payload = completed_payload(user, meeting)

    pipeline.signed_url_error = RuntimeError("Object not found")
    assert deliver(payload).json() == {"status": "received"}
    assert reread(meeting.id).status == "failed"

    pipeline.signed_url_error = None
    assert deliver(payload).json() == {"status": "received"}
    assert pipeline.submitted == [str(meeting.id)]
    assert reread(meeting.id).status == "transcribing"


@pytest.mark.parametrize("message", [
    "Failed to start bot: timed out",
    "Cancelled while waiting for a recorder",
])
def test_a_report_from_a_bot_the_backend_thought_never_started_is_recovered(db, user, pipeline, message):
    meeting = make_meeting(db, user.id, status="failed", error_message=message)

    assert deliver(completed_payload(user, meeting)).json() == {"status": "received"}
    assert pipeline.submitted == [str(meeting.id)]
    assert reread(meeting.id).status == "transcribing"


def test_a_recovered_meeting_is_protected_by_the_rule_like_any_other(db, user, pipeline, watchdog_on):
    """Once the late report is accepted, *its* redeliveries after a failure are blocked."""
    meeting = make_meeting(db, user.id, status="recording")
    age_meeting(meeting.id, minutes=106)
    watchdog.sweep_stale_meetings(db)
    payload = completed_payload(user, meeting)

    assert deliver(payload).json() == {"status": "received"}
    fail_transcription(str(meeting.id), "Failed to download recording: 500")
    assert deliver(payload).json() == {"status": "already_processed"}

    assert pipeline.submitted == [str(meeting.id)]


# ---------------------------------------------------------------------------
# c. concurrent completed reports
# ---------------------------------------------------------------------------

def _race(payload, monkeypatch, pipeline):
    barrier = threading.Barrier(2, timeout=15)

    def held_signed_url(path, *args, **kwargs):
        barrier.wait()
        return pipeline.signed_url(path)

    monkeypatch.setattr(webhooks, "get_signed_recording_url", held_signed_url)
    bodies, errors = [], []
    lock = threading.Lock()

    def one():
        try:
            body = deliver(payload, TestClient(app)).json()
            with lock:
                bodies.append(body["status"])
        except BaseException as e:  # noqa: BLE001 - re-raised below
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=one) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a delivery deadlocked"
    if errors:
        raise errors[0]
    return sorted(bodies)


def test_two_late_reports_racing_to_recover_a_failed_meeting_transcribe_once(db, user, pipeline, monkeypatch):
    meeting = make_meeting(db, user.id, status="failed", error_message="Timed out while 'recording'")

    assert _race(completed_payload(user, meeting), monkeypatch, pipeline) == ["already_processed", "received"]
    assert pipeline.submitted == [str(meeting.id)]
    assert reread(meeting.id).status == "transcribing"


def test_two_redeliveries_racing_after_a_transcription_failure_transcribe_neither(db, user, pipeline, monkeypatch):
    meeting = make_meeting(db, user.id, status="recording")
    payload = completed_payload(user, meeting)
    deliver(payload)
    fail_transcription(str(meeting.id), "Failed to download recording: 404")

    assert _race(payload, monkeypatch, pipeline) == ["already_processed", "already_processed"]
    assert pipeline.submitted == [str(meeting.id)]
    assert reread(meeting.id).status == "failed"
