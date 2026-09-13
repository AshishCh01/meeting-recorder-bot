"""
B3 finding 1 - a failed transcription enqueue must not strand the meeting.

The completed branch of the recording-complete webhook claims the meeting with
a conditional UPDATE to "transcribing", commits, and only then hands the
recording to submit_transcription. With TRANSCRIPTION_USE_QUEUE on, that
hand-off is a Redis enqueue, and it can fail. Before the fix the exception
escaped as a 500 with the claim already committed: meeting-bot retried, as it
is built to, and every retry hit the rowcount == 0 "already_processed" branch.
Nothing was ever enqueued. The meeting sat in "transcribing" for the full
watchdog TTL and was then failed with a generic timeout.

The fix keeps a claim only if its hand-off succeeds. A failed hand-off undoes
the claim, in the same request that made it, to "failed" with recording_url
cleared, and answers 503 so the bot retries. The retry finds a meeting no
completed report has been accepted for and claims it normally. At most one
transcription is still guaranteed by the same conditional UPDATE: only one
request can hold the claim at a time, and the claim is undone only when no job
was handed off.

"failed" rather than whatever status came before the claim: if the bot's
retries run out while Redis is still down, the meeting ends terminal, with a
message naming the cause, and POST /meetings/{id}/retry can pick it up. Put
back in "recording" it would wait out that status's TTL (105 minutes by default)
before failing with a vaguer message.

What this does not cover, deliberately: the API process dying between the
claim's commit and the enqueue. Nothing runs to undo the claim, so that case is
still bounded by the watchdog's "transcribing" TTL and recovered by Retry - see
the last test here. Closing that window needs a durable "hand-off pending"
record, which is a state-machine change, not a fix to this branch.

Postgres only, except the one test that drives a real, unreachable Redis and
then a real, reachable one, and counts the jobs arq actually holds.
"""
import asyncio
import os
import threading
import uuid

import pytest
from arq import create_pool
from arq.connections import RedisSettings
from fastapi.testclient import TestClient

from app.api import meetings, webhooks
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting
from app.main import app
from app.services import watchdog

from tests.test_meeting_status_machine import age_meeting

REDIS_URL = os.environ.get("TEST_REDIS_URL")
needs_redis = pytest.mark.skipif(
    not REDIS_URL,
    reason="TEST_REDIS_URL is not set - see tests/README.md",
)
# Nothing listens on port 1, so a connection is refused immediately.
DEAD_REDIS = "redis://127.0.0.1:1"

WEBHOOK_HEADERS = {"Authorization": "Bearer stub-token"}  # conftest's MEETING_BOT_BEARER_TOKEN


def make_meeting(db, user_id, *, status="recording"):
    meeting = Meeting(
        id=uuid.uuid4(),
        user_id=user_id,
        meeting_url="https://zoom.us/j/123456789",
        title="Planning",
        platform="zoom",
        status=status,
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
        "duration_seconds": 1200,
        "error_message": None,
    }


def deliver(payload, client=None):
    # raise_server_exceptions=False: the bot sees a status code, not a
    # traceback, and a status code is what decides whether it retries.
    client = client or TestClient(app, raise_server_exceptions=False)
    return client.post("/webhooks/recording-complete", json=payload, headers=WEBHOOK_HEADERS)


class Handoff:
    """
    Stand-in for submit_transcription. `fail_first` calls raise, the rest
    succeed; `accepted` is what actually reached the queue.
    """

    def __init__(self, fail_first=0):
        self.fail_first = fail_first
        self.attempts = 0
        self.accepted = []
        self._lock = threading.Lock()

    def __call__(self, meeting_id, storage_path):
        with self._lock:
            self.attempts += 1
            if self.attempts <= self.fail_first:
                raise ConnectionError("Error connecting to Redis")
            self.accepted.append((meeting_id, storage_path))


@pytest.fixture
def signed_url(monkeypatch):
    monkeypatch.setattr(webhooks, "get_signed_recording_url", lambda path, *a, **k: f"https://signed/{path}")


def install(monkeypatch, handoff):
    monkeypatch.setattr(webhooks, "submit_transcription", handoff)
    return handoff


# ---------------------------------------------------------------------------
# a. the hand-off succeeds
# ---------------------------------------------------------------------------

def test_a_successful_handoff_submits_exactly_one_job(db, user, signed_url, monkeypatch):
    handoff = install(monkeypatch, Handoff())
    meeting = make_meeting(db, user.id)

    response = deliver(completed_payload(user, meeting))

    assert response.status_code == 200
    assert response.json() == {"status": "received"}
    assert handoff.accepted == [(str(meeting.id), f"{user.id}/{meeting.id}/recording.m4a")]
    row = reread(meeting.id)
    assert row.status == "transcribing"
    assert row.error_message is None


# ---------------------------------------------------------------------------
# b. the hand-off fails once
# ---------------------------------------------------------------------------

def test_a_failed_handoff_undoes_the_claim_and_asks_the_bot_to_retry(db, user, signed_url, monkeypatch):
    handoff = install(monkeypatch, Handoff(fail_first=1))
    meeting = make_meeting(db, user.id)

    response = deliver(completed_payload(user, meeting))

    assert handoff.accepted == []
    row = reread(meeting.id)
    assert row.status != "transcribing", "claimed with nothing handed off - the stranded state"
    assert response.status_code == 503, "the bot only retries on an error status"
    assert row.status == "failed"
    assert row.recording_url is None, "the claim's marker must be undone with it"
    assert "could not be queued" in row.error_message
    assert "Error connecting to Redis" in row.error_message


# ---------------------------------------------------------------------------
# c. the redelivery recovers
# ---------------------------------------------------------------------------

def test_the_bots_retry_after_a_failed_handoff_submits_the_job(db, user, signed_url, monkeypatch):
    handoff = install(monkeypatch, Handoff(fail_first=1))
    meeting = make_meeting(db, user.id)
    payload = completed_payload(user, meeting)

    assert deliver(payload).status_code >= 500
    retry = deliver(payload)

    assert retry.status_code == 200
    assert retry.json() == {"status": "received"}, "the retry was treated as already processed"
    assert handoff.accepted == [(str(meeting.id), payload["recording_path"])]
    row = reread(meeting.id)
    assert row.status == "transcribing"
    assert row.recording_url == f"https://signed/{payload['recording_path']}"
    assert row.duration_seconds == 1200
    assert row.error_message is None, "a recovered meeting still carries the failure note"


# ---------------------------------------------------------------------------
# e. recovery does not become a second job
# ---------------------------------------------------------------------------

def test_deliveries_after_the_recovery_are_already_processed(db, user, signed_url, monkeypatch):
    handoff = install(monkeypatch, Handoff(fail_first=1))
    meeting = make_meeting(db, user.id)
    payload = completed_payload(user, meeting)

    first = deliver(payload).status_code
    later = [deliver(payload).json()["status"] for _ in range(4)]

    assert first >= 500
    assert later == ["received", "already_processed", "already_processed", "already_processed"]
    assert len(handoff.accepted) == 1
    assert handoff.attempts == 2, "an already-processed delivery tried to hand off again"


def test_the_bot_running_out_of_retries_leaves_a_terminal_retryable_meeting(db, user, signed_url, monkeypatch):
    """
    Redis stays down through all three of meeting-bot's attempts. The meeting
    is failed, now, with the cause in its message - not "transcribing" until
    the watchdog times it out - and the watchdog leaves a terminal row alone.
    Retry, which finds the recording in storage, then transcribes it.
    """
    monkeypatch.setattr(settings, "watchdog_enabled", True)
    handoff = install(monkeypatch, Handoff(fail_first=3))
    meeting = make_meeting(db, user.id)
    payload = completed_payload(user, meeting)

    codes = [deliver(payload).status_code for _ in range(3)]
    assert handoff.accepted == []
    assert reread(meeting.id).status == "failed"
    assert codes == [503, 503, 503], "every attempt must be retryable, not only the first"
    assert watchdog.sweep_stale_meetings(db) == 0

    retried = []
    monkeypatch.setattr(meetings, "submit_transcription", lambda mid, path: retried.append((mid, path)))
    monkeypatch.setattr(meetings, "supabase", _StorageWithRecording())

    assert meetings.retry_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id)) == {"status": "retrying"}
    assert retried == [(str(meeting.id), payload["recording_path"])]
    assert reread(meeting.id).status == "transcribing"


class _StorageWithRecording:
    @property
    def storage(self):
        return self

    def from_(self, bucket):
        return self

    def list(self, folder):
        return [{"name": "recording.m4a"}]


def test_the_undo_never_overwrites_a_meeting_that_already_moved_on(db, user, signed_url, monkeypatch):
    """
    An enqueue can raise after Redis accepted the job - the reply lost on the
    way back. If a worker has already finished that job by the time the
    exception lands, the undo must not fail a completed meeting.
    """
    meeting = make_meeting(db, user.id)

    def accepted_then_raised(meeting_id, storage_path):
        other = SessionLocal()
        try:
            other.query(Meeting).filter(Meeting.id == meeting_id).update({"status": "completed"})
            other.commit()
        finally:
            other.close()
        raise ConnectionError("Connection closed by server")

    monkeypatch.setattr(webhooks, "submit_transcription", accepted_then_raised)

    assert deliver(completed_payload(user, meeting)).status_code == 503

    row = reread(meeting.id)
    assert row.status == "completed"
    assert row.error_message is None
    assert row.recording_url is not None


# ---------------------------------------------------------------------------
# d. concurrent deliveries, with the first hand-off failing
# ---------------------------------------------------------------------------

def test_concurrent_deliveries_around_a_failed_handoff_submit_at_most_once(db, user, monkeypatch):
    """
    Two deliveries released together from the signed-URL call, which comes
    before the claim. One claims; the first hand-off attempt fails. The other
    either sees the claim (already_processed) or, if it runs after the undo,
    claims and hands off itself - which one depends on scheduling, so both
    outcomes are allowed. What is not allowed is more than one accepted job,
    or ending "transcribing" with none. A final redelivery settles it either
    way.
    """
    barrier = threading.Barrier(2, timeout=15)

    def held_signed_url(path, *rest, **kwargs):
        barrier.wait()
        return f"https://signed/{path}"

    monkeypatch.setattr(webhooks, "get_signed_recording_url", held_signed_url)
    handoff = install(monkeypatch, Handoff(fail_first=1))
    meeting = make_meeting(db, user.id)
    payload = completed_payload(user, meeting)

    codes, errors = [], []
    lock = threading.Lock()

    def one():
        try:
            code = deliver(payload, TestClient(app, raise_server_exceptions=False)).status_code
            with lock:
                codes.append(code)
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

    assert len(handoff.accepted) <= 1
    row = reread(meeting.id)
    if handoff.accepted:
        assert row.status == "transcribing"
    else:
        assert row.status == "failed", "claimed with nothing handed off - the stranded state"
    assert 503 in codes

    # The bot retries the delivery that got the 503.
    monkeypatch.setattr(webhooks, "get_signed_recording_url", lambda path, *a, **k: f"https://signed/{path}")
    deliver(payload)

    assert len(handoff.accepted) == 1
    assert reread(meeting.id).status == "transcribing"


# ---------------------------------------------------------------------------
# The window this fix does not close
# ---------------------------------------------------------------------------

class _ProcessKilled(BaseException):
    """Not an Exception: stands in for the process dying mid-request."""


def test_a_crash_between_claim_and_handoff_is_still_bounded_by_the_watchdog(db, user, signed_url, monkeypatch):
    """
    A deploy kills the API after the claim's commit, before the enqueue. No
    code runs to undo the claim, so redeliveries are already_processed and the
    meeting stays "transcribing" with no job. Pinned so the residual is stated
    rather than assumed: the watchdog fails it at the transcribing TTL, and it
    lands where Retry can recover it.
    """
    monkeypatch.setattr(settings, "watchdog_enabled", True)
    monkeypatch.setattr(settings, "watchdog_transcribing_ttl_minutes", 30)

    def killed(meeting_id, storage_path):
        raise _ProcessKilled()

    monkeypatch.setattr(webhooks, "submit_transcription", killed)
    meeting = make_meeting(db, user.id)
    payload = completed_payload(user, meeting)

    with pytest.raises(_ProcessKilled):
        deliver(payload, TestClient(app))
    assert reread(meeting.id).status == "transcribing"

    handoff = install(monkeypatch, Handoff())
    assert deliver(payload).json() == {"status": "already_processed"}
    assert handoff.attempts == 0

    age_meeting(meeting.id, minutes=31)
    assert watchdog.sweep_stale_meetings(db) == 1
    row = reread(meeting.id)
    assert row.status == "failed"
    assert "'transcribing'" in row.error_message


# ---------------------------------------------------------------------------
# The real queue: an unreachable Redis, then a reachable one
# ---------------------------------------------------------------------------

@pytest.fixture
def test_redis(monkeypatch):
    async def _flush():
        pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
        try:
            await pool.flushdb()
        finally:
            await pool.aclose()

    asyncio.run(_flush())
    yield
    asyncio.run(_flush())


async def _queued_transcriptions():
    pool = await create_pool(RedisSettings.from_dsn(REDIS_URL))
    try:
        return [job for job in await pool.queued_jobs() if job.function == "transcribe_job"]
    finally:
        await pool.aclose()


@needs_redis
def test_a_real_redis_outage_then_recovery_leaves_exactly_one_queued_job(db, user, signed_url, test_redis, monkeypatch):
    """
    No stand-in for submit_transcription: the real queue path, against a Redis
    that refuses connections, then against the real one. The count is read out
    of arq, not out of a spy.
    """
    monkeypatch.setattr(settings, "transcription_use_queue", True)
    monkeypatch.setattr(settings, "redis_url", DEAD_REDIS)
    meeting = make_meeting(db, user.id)
    payload = completed_payload(user, meeting)

    first = deliver(payload)
    assert reread(meeting.id).status == "failed", "claimed with nothing handed off - the stranded state"
    assert first.status_code == 503
    assert asyncio.run(_queued_transcriptions()) == []

    # Redis comes back before the bot's next attempt.
    monkeypatch.setattr(settings, "redis_url", REDIS_URL)
    assert deliver(payload).json() == {"status": "received"}
    assert deliver(payload).json() == {"status": "already_processed"}

    jobs = asyncio.run(_queued_transcriptions())
    assert len(jobs) == 1, f"expected one transcription job, found {len(jobs)}"
    assert jobs[0].args == (str(meeting.id), payload["recording_path"])
    assert reread(meeting.id).status == "transcribing"
