"""
Phase C2 - bot dispatch moves to a queue.

trigger_bot_join used to post to meeting-bot synchronously. A bot already
recording answers 409, the exception marked the meeting failed, and
POST /meetings returned 502 - so two meetings at 10:00 with one recorder meant
one user lost their recording outright. These tests cover the six properties
from docs/scaling-plan.md's Phase C2 gate:

  1. A meeting requested while the bot is full reaches "queued", not "failed",
     and dispatches once the recorder frees up.
  2. A queued meeting survives a watchdog sweep that would have swept it out
     of "joining" - the landmine this phase turns on.
  3. A meeting deleted or stopped while queued is never dispatched.
  4. A 409 from the bot re-queues rather than fails.
  5. Exhausted waiting writes a terminal failure naming the real cause.
  6. With the flag off, the old synchronous path is untouched.

Postgres is required. Redis only for the three tests that assert real
enqueue/defer behaviour - see tests/README.md.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import update

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting
from app.services import bot_dispatch, bot_service, watchdog
from app.worker import dispatch_bot_join_job

REDIS_URL = os.environ.get("TEST_REDIS_URL")
needs_redis = pytest.mark.skipif(
    not REDIS_URL,
    reason="TEST_REDIS_URL is not set - see tests/README.md",
)


def redis_settings():
    return RedisSettings.from_dsn(REDIS_URL)


@pytest.fixture
def test_redis(monkeypatch):
    """Every enqueue in a test using this goes to the throwaway Redis."""
    monkeypatch.setattr(settings, "redis_url", REDIS_URL)

    async def _flush():
        pool = await create_pool(redis_settings())
        try:
            await pool.flushdb()
        finally:
            await pool.aclose()

    asyncio.run(_flush())
    yield
    asyncio.run(_flush())


def make_meeting(db, user_id, *, status="queued", url="https://meet.google.com/abc-defg-hij"):
    meeting = Meeting(
        id=uuid.uuid4(),
        user_id=user_id,
        meeting_url=url,
        title="Quarterly planning",
        platform="google",
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


def age_meeting(meeting_id, minutes):
    """Backdates updated_at, which is what the watchdog measures against."""
    other = SessionLocal()
    try:
        other.execute(
            update(Meeting)
            .where(Meeting.id == meeting_id)
            .values(updated_at=datetime.now(timezone.utc) - timedelta(minutes=minutes))
        )
        other.commit()
    finally:
        other.close()


class FakeBot:
    """
    Stands in for meeting-bot's HTTP surface: GET /capacity (Phase C1) and
    POST /{platform}/join. Its admission rule is the real one - the bot
    refuses when active >= max, and C1 made /capacity report exactly that -
    so a test that drives it into 409 is exercising the same disagreement the
    dispatcher has to survive in production.
    """

    def __init__(self, max_concurrent=1, active=0, unreachable=False):
        self.max = max_concurrent
        self.active = active
        self.unreachable = unreachable
        self.joins = []
        self.capacity_calls = 0

    def capacity(self):
        self.capacity_calls += 1
        if self.unreachable:
            raise httpx.ConnectError("connection refused")
        return {
            "active": self.active,
            "max": self.max,
            "available": max(0, self.max - self.active),
            "meetingIds": [f"meeting-{i}" for i in range(self.active)],
        }

    def join(self, platform, url, meeting_id, user_id, bot_display_name):
        if self.active >= self.max:
            raise httpx.HTTPStatusError(
                "409 Conflict",
                request=httpx.Request("POST", "http://bot/join"),
                response=httpx.Response(409, json={"error": "Bot is currently busy with another meeting"}),
            )
        self.active += 1
        self.joins.append(meeting_id)
        return {"status": "accepted", "meetingId": meeting_id}

    def finish_one(self):
        """A recording ends and the recorder frees up."""
        self.active = max(0, self.active - 1)


@pytest.fixture
def bot(monkeypatch):
    fake = FakeBot()
    monkeypatch.setattr(bot_service, "get_bot_capacity", fake.capacity)
    monkeypatch.setattr(bot_service, "post_join", fake.join)
    return fake


@pytest.fixture
def queue_on(monkeypatch):
    monkeypatch.setattr(settings, "bot_dispatch_use_queue", True)


# ---------------------------------------------------------------------------
# Gate 1 - a full bot queues the meeting, and it joins when capacity frees
# ---------------------------------------------------------------------------

def test_meeting_requested_while_the_bot_is_full_waits_then_joins(db, user, bot, queue_on):
    """
    The phase, in one test. The recorder is at 1/1 - the exact state that used
    to produce a 409, a "failed" meeting and a lost recording.
    """
    bot.max = 1
    bot.active = 1
    meeting = make_meeting(db, user.id, status=bot_service.initial_status())
    assert meeting.status == "queued", "the queued path must not park a meeting in 'joining'"

    # Attempt 1: no room. The meeting waits - it is not failed, and no join
    # was posted.
    result = bot_dispatch.dispatch_queued_meeting(str(meeting.id))
    assert result.outcome == "waiting"
    assert "busy" in result.reason
    assert bot.joins == []
    assert reread(meeting.id).status == "queued"

    # The first recording ends.
    bot.finish_one()

    # Attempt 2: the same job, later. Now it goes.
    result = bot_dispatch.dispatch_queued_meeting(str(meeting.id))
    assert result.outcome == "dispatched"
    assert bot.joins == [str(meeting.id)]
    assert reread(meeting.id).status == "joining"


def test_an_idle_bot_dispatches_on_the_first_attempt(db, user, bot, queue_on):
    """The queue must not add a delay when there was never any contention."""
    bot.max = 2
    bot.active = 0
    meeting = make_meeting(db, user.id)

    assert bot_dispatch.dispatch_queued_meeting(str(meeting.id)).outcome == "dispatched"
    assert bot.joins == [str(meeting.id)]
    assert reread(meeting.id).status == "joining"


@needs_redis
def test_the_wait_loop_defers_a_real_job_and_survives_the_worker(db, user, bot, queue_on, test_redis):
    """
    The same wait, driven through the arq task rather than the service, so the
    deferral is a real job in Redis: what makes the waiting outlive a worker
    restart instead of living in one process's memory.
    """
    bot.max = 1
    bot.active = 1
    meeting = make_meeting(db, user.id)
    meeting_id = str(meeting.id)

    async def run_and_inspect():
        pool = await create_pool(redis_settings())
        try:
            outcome = await dispatch_bot_join_job({"redis": pool}, meeting_id, 1)
            # A deferred job is not in queued_jobs() until its defer elapses,
            # so ask Redis for the job itself.
            deferred = await pool.zcard(b"arq:queue")
            return outcome, deferred
        finally:
            await pool.aclose()

    outcome, deferred = asyncio.run(run_and_inspect())

    assert outcome == "waiting"
    assert deferred == 1, "the next attempt was not left in Redis - the wait would die with the worker"
    assert reread(meeting.id).status == "queued"


@needs_redis
def test_trigger_bot_join_enqueues_instead_of_posting(db, user, queue_on, test_redis, monkeypatch):
    """
    The seam. With the flag on, no HTTP request is made from the request
    thread at all - a tripwire on post_join proves it.
    """
    def boom(*args, **kwargs):
        raise AssertionError("posted to the bot synchronously with the queue on")

    monkeypatch.setattr(bot_service, "post_join", boom)
    meeting = make_meeting(db, user.id)

    result = bot_service.trigger_bot_join(
        "google", meeting.meeting_url, str(meeting.id), str(user.id), "Test Notetaker",
    )
    assert result["status"] == "queued"

    async def queued():
        pool = await create_pool(redis_settings())
        try:
            return await pool.queued_jobs()
        finally:
            await pool.aclose()

    pending = asyncio.run(queued())
    assert len(pending) == 1
    assert pending[0].function == bot_service.DISPATCH_JOB
    assert pending[0].args == (str(meeting.id), 1)


# ---------------------------------------------------------------------------
# Gate 2 - the watchdog must not sweep a meeting that is legitimately waiting
# ---------------------------------------------------------------------------

def test_a_queued_meeting_survives_the_sweep_that_would_have_killed_it_in_joining(db, user, monkeypatch):
    """
    The landmine. watchdog_joining_ttl_minutes is 10, so reusing "joining" for
    a waiting meeting would fail it after 11 minutes of doing exactly what it
    was asked to do - the same lost recording this phase exists to prevent,
    just slower. Both meetings below are backdated identically; only the
    status differs.
    """
    monkeypatch.setattr(settings, "watchdog_enabled", True)
    monkeypatch.setattr(settings, "watchdog_joining_ttl_minutes", 10)
    monkeypatch.setattr(settings, "watchdog_queued_ttl_minutes", 30)

    waiting = make_meeting(db, user.id, status="queued")
    stuck = make_meeting(db, user.id, status="joining")
    age_meeting(waiting.id, minutes=15)
    age_meeting(stuck.id, minutes=15)

    swept = watchdog.sweep_stale_meetings(db)

    assert swept == 1
    assert reread(waiting.id).status == "queued", "a meeting waiting for a recorder was swept"
    assert reread(stuck.id).status == "failed"


def test_a_queued_meeting_is_still_swept_once_its_own_ttl_passes(db, user, monkeypatch):
    """
    Bounded, not unlimited. This is the backstop for a meeting whose dispatch
    job was lost entirely (a flushed Redis), where nothing else will ever
    revisit the row.
    """
    monkeypatch.setattr(settings, "watchdog_enabled", True)
    monkeypatch.setattr(settings, "watchdog_queued_ttl_minutes", 30)

    forgotten = make_meeting(db, user.id, status="queued")
    age_meeting(forgotten.id, minutes=45)

    assert watchdog.sweep_stale_meetings(db) == 1
    row = reread(forgotten.id)
    assert row.status == "failed"
    assert "queued" in row.error_message


def test_the_dispatcher_gives_up_before_the_watchdog_would_sweep(monkeypatch):
    """
    The ordering that decides which error message the user sees. If the
    watchdog fired first they would get "Timed out while 'queued' - swept by
    watchdog"; the dispatcher's own message names the actual cause instead.
    Asserted on the shipped defaults, because this is a config relationship,
    not a code path.
    """
    dispatcher_window = (
        settings.bot_dispatch_max_attempts * settings.bot_dispatch_retry_delay_seconds
    ) / 60
    assert dispatcher_window < settings.watchdog_queued_ttl_minutes, (
        "the watchdog would sweep a queued meeting before the dispatcher gives up, "
        "replacing a specific failure message with a generic one"
    )


# ---------------------------------------------------------------------------
# Gate 3 - a cancelled meeting is never dispatched
# ---------------------------------------------------------------------------

def test_a_deleted_meeting_is_dropped_not_joined(db, user, bot, queue_on):
    """
    delete_meeting removes the row and never calls stop_bot for a queued
    meeting - correctly, since there is no session to stop. The queued job
    still exists, and this is what it must do when it fires.
    """
    meeting = make_meeting(db, user.id)
    meeting_id = str(meeting.id)
    db.delete(meeting)
    db.commit()

    result = bot_dispatch.dispatch_queued_meeting(meeting_id)

    assert result.outcome == "dropped"
    assert bot.joins == [], "joined a meeting the user had deleted"
    assert bot.capacity_calls == 0, "the bot was contacted at all for a deleted meeting"


@pytest.mark.parametrize("status", ["failed", "completed", "recording", "scheduled"])
def test_a_meeting_that_left_the_queue_is_dropped(db, user, bot, queue_on, status):
    """
    Anything other than "queued" means someone else already decided this
    meeting's fate - the user stopped it, a watchdog swept it, or another
    dispatch job won the claim. None of them should end in a bot join.
    """
    meeting = make_meeting(db, user.id, status=status)

    result = bot_dispatch.dispatch_queued_meeting(str(meeting.id))

    assert result.outcome == "dropped"
    assert bot.joins == []
    assert reread(meeting.id).status == status, "a dropped job changed the meeting's status"


def test_a_dropped_job_stops_the_retry_loop(db, user, bot, queue_on):
    """
    The task's half of gate 3: a drop must not re-enqueue, or a deleted
    meeting would keep a job alive for the full 20-minute window.
    """
    meeting = make_meeting(db, user.id)
    meeting_id = str(meeting.id)
    db.delete(meeting)
    db.commit()

    requeued = []

    class Pool:
        async def enqueue_job(self, *args, **kwargs):
            requeued.append((args, kwargs))

    outcome = asyncio.run(dispatch_bot_join_job({"redis": Pool()}, meeting_id, 1))

    assert outcome == "dropped"
    assert requeued == []


# ---------------------------------------------------------------------------
# Gate 4 - a 409 re-queues, it does not fail
# ---------------------------------------------------------------------------

def test_a_409_from_the_bot_requeues_rather_than_failing(db, user, bot, queue_on, monkeypatch):
    """
    /capacity is an optimisation, never a lock: the check and the join are not
    atomic, so two dispatch jobs can both read available: 1 and both post. The
    loser gets the bot's 409, which is the authority - and must be treated as
    "try again", not as a failed meeting.

    Staged by having the recorder fill up between the capacity read and the
    join, which is exactly what the losing job experiences.
    """
    bot.max = 1
    bot.active = 0
    meeting = make_meeting(db, user.id)

    real_capacity = bot.capacity

    def capacity_then_someone_else_takes_it():
        payload = real_capacity()   # honest at the time it was read: available 1
        bot.active = 1              # the other job's join lands here
        return payload

    monkeypatch.setattr(bot_service, "get_bot_capacity", capacity_then_someone_else_takes_it)

    result = bot_dispatch.dispatch_queued_meeting(str(meeting.id))

    assert result.outcome == "waiting", "a 409 failed the meeting instead of re-queueing it"
    assert "already full" in result.reason
    row = reread(meeting.id)
    assert row.status == "queued", "the meeting was left in 'joining' with no bot behind it"
    assert row.error_message is None


def test_an_unreachable_bot_waits_rather_than_failing(db, user, bot, queue_on):
    """A restarting recorder is a reason to wait, not to lose the recording."""
    bot.unreachable = True
    meeting = make_meeting(db, user.id)

    result = bot_dispatch.dispatch_queued_meeting(str(meeting.id))

    assert result.outcome == "waiting"
    assert "could not be reached" in result.reason
    assert reread(meeting.id).status == "queued"


def test_a_requeued_meeting_dispatches_on_a_later_attempt(db, user, bot, queue_on):
    """The re-queue has to actually be recoverable, not just non-fatal."""
    bot.max = 1
    bot.active = 1
    meeting = make_meeting(db, user.id)

    assert bot_dispatch.dispatch_queued_meeting(str(meeting.id)).outcome == "waiting"
    bot.finish_one()
    assert bot_dispatch.dispatch_queued_meeting(str(meeting.id)).outcome == "dispatched"
    assert reread(meeting.id).status == "joining"


# ---------------------------------------------------------------------------
# Gate 5 - exhausted waiting is terminal, and says why
# ---------------------------------------------------------------------------

def test_exhausted_waiting_fails_the_meeting_with_the_real_cause(db, user, bot, queue_on, monkeypatch):
    """
    Deferring forever would turn "meeting lost" into "meeting pending
    forever", which is worse because nothing surfaces it. The message must
    name the cause: "no free recorder" is actionable, "timed out" is not.
    """
    monkeypatch.setattr(settings, "bot_dispatch_max_attempts", 3)
    bot.max = 1
    bot.active = 1
    meeting = make_meeting(db, user.id)
    meeting_id = str(meeting.id)

    requeued = []

    class Pool:
        async def enqueue_job(self, *args, **kwargs):
            requeued.append(args)

    ctx = {"redis": Pool()}

    assert asyncio.run(dispatch_bot_join_job(ctx, meeting_id, 1)) == "waiting"
    assert asyncio.run(dispatch_bot_join_job(ctx, meeting_id, 2)) == "waiting"
    assert reread(meeting.id).status == "queued", "failed while attempts remained"

    # The last attempt must not leave the meeting non-terminal.
    assert asyncio.run(dispatch_bot_join_job(ctx, meeting_id, 3)) == "exhausted"

    row = reread(meeting.id)
    assert row.status == "failed"
    assert "free recorder" in row.error_message
    assert "busy" in row.error_message
    assert "No recording was made" in row.error_message
    assert len(requeued) == 2, "the final attempt re-enqueued itself as well as failing the meeting"


def test_the_attempt_counter_advances_on_each_requeue(db, user, bot, queue_on):
    """A defer that forgot to increment would wait forever at attempt 1."""
    bot.max = 1
    bot.active = 1
    meeting = make_meeting(db, user.id)

    requeued = []

    class Pool:
        async def enqueue_job(self, *args, **kwargs):
            requeued.append(args)

    asyncio.run(dispatch_bot_join_job({"redis": Pool()}, str(meeting.id), 7))

    assert requeued == [(bot_service.DISPATCH_JOB, str(meeting.id), 8)]


def test_exhaustion_does_not_stomp_a_meeting_that_moved_on(db, user):
    """
    The last attempt and a late dispatch can race. A meeting that is already
    recording must never be marked failed by a job that gave up on it.
    """
    meeting = make_meeting(db, user.id, status="recording")

    bot_dispatch.record_dispatch_exhausted(str(meeting.id), "the recorder was busy")

    row = reread(meeting.id)
    assert row.status == "recording"
    assert row.error_message is None


# ---------------------------------------------------------------------------
# Gate 6 - the flag off is the old behaviour, byte for byte
# ---------------------------------------------------------------------------

def test_flag_off_posts_synchronously(db, user, bot, monkeypatch):
    """Deploy 1: the dispatcher, the task and "queued" all exist and nothing uses them."""
    monkeypatch.setattr(settings, "bot_dispatch_use_queue", False)

    result = bot_service.trigger_bot_join(
        "google", "https://meet.google.com/abc-defg-hij", "m-1", "u-1", "Test Notetaker",
    )

    assert result == {"status": "accepted", "meetingId": "m-1"}
    assert bot.joins == ["m-1"], "the join was not posted from the request thread"


def test_flag_off_still_raises_on_a_busy_bot(db, user, bot, monkeypatch):
    """
    The 502 path, unchanged - this is what create_meeting's except block turns
    into "Could not start recording bot". Losing this would mean the revert
    does not actually revert.
    """
    monkeypatch.setattr(settings, "bot_dispatch_use_queue", False)
    bot.max = 1
    bot.active = 1

    with pytest.raises(httpx.HTTPStatusError) as exc:
        bot_service.trigger_bot_join(
            "google", "https://meet.google.com/abc-defg-hij", "m-1", "u-1", "Test Notetaker",
        )
    assert exc.value.response.status_code == 409


def test_flag_off_writes_joining_not_queued(monkeypatch):
    """
    The status both call sites write. Getting this wrong on the revert would
    park every meeting in "queued" with no dispatcher running to pick it up.
    """
    monkeypatch.setattr(settings, "bot_dispatch_use_queue", False)
    assert bot_service.initial_status() == "joining"
    monkeypatch.setattr(settings, "bot_dispatch_use_queue", True)
    assert bot_service.initial_status() == "queued"


@needs_redis
def test_flag_off_enqueues_nothing(db, user, bot, test_redis, monkeypatch):
    """Redis and the worker are running; nothing reaches them."""
    monkeypatch.setattr(settings, "bot_dispatch_use_queue", False)

    bot_service.trigger_bot_join(
        "google", "https://meet.google.com/abc-defg-hij", "m-1", "u-1", "Test Notetaker",
    )

    async def queued():
        pool = await create_pool(redis_settings())
        try:
            return await pool.queued_jobs()
        finally:
            await pool.aclose()

    assert asyncio.run(queued()) == [], "the flag is off but something was enqueued"


def test_an_unsupported_platform_fails_the_same_way_on_both_sides(monkeypatch):
    """
    The check has to stay in front of the flag. Deferring it would turn an
    immediate, loud ValueError into a meeting that sits queued for 30 seconds
    before a worker discovers the same thing.
    """
    for flag in (False, True):
        monkeypatch.setattr(settings, "bot_dispatch_use_queue", flag)
        with pytest.raises(ValueError, match="teams"):
            bot_service.trigger_bot_join("teams", "https://teams.microsoft.com/x", "m-1", "u-1", "Bot")


def test_the_dispatch_task_is_registered_under_the_name_the_enqueue_uses():
    """A typo here means jobs enqueue forever and never run."""
    from app.worker import WorkerSettings

    assert dispatch_bot_join_job in WorkerSettings.functions
    assert dispatch_bot_join_job.__name__ == bot_service.DISPATCH_JOB


# ---------------------------------------------------------------------------
# Follow-up - a queued meeting can be stopped, not only deleted
# ---------------------------------------------------------------------------

@pytest.fixture
def bot_stop_tripwire(monkeypatch):
    """A queued meeting has no bot session - reaching stop_bot is the bug."""
    from app.api import meetings

    def boom(meeting_id):
        raise AssertionError("called the bot to stop a meeting that never had a bot")

    monkeypatch.setattr(meetings, "stop_bot", boom)
    return boom


def test_stopping_a_queued_meeting_cancels_it_without_calling_the_bot(db, user, queue_on, bot_stop_tripwire):
    from app.api import meetings

    meeting = make_meeting(db, user.id)

    response = meetings.stop_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id))

    assert response["status"] == "stopped"
    # Returned so the page can show it at once - no webhook is coming.
    assert response["meeting"]["status"] == "failed"
    row = reread(meeting.id)
    assert row.status == "failed"
    assert "before a recorder became free" in row.error_message
    assert "No recording was made" in row.error_message


def test_a_stopped_queued_meeting_is_never_dispatched(db, user, bot, queue_on, bot_stop_tripwire):
    """
    The job is still in Redis after the stop. When it fires, it must drop
    without so much as asking the bot for capacity.
    """
    from app.api import meetings

    meeting = make_meeting(db, user.id)
    meetings.stop_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id))

    result = bot_dispatch.dispatch_queued_meeting(str(meeting.id))

    assert result.outcome == "dropped"
    assert bot.joins == [], "joined a meeting the user had stopped"
    assert bot.capacity_calls == 0


def test_a_stop_that_loses_the_race_to_the_dispatcher_stops_the_bot(db, user, queue_on, monkeypatch):
    """
    The user presses Stop on a queued meeting at the moment a recorder frees
    up, and the dispatcher's claim lands between the route's read and its
    cancel. The cancel must not match, and the meeting - now joining - must be
    stopped the ordinary way rather than 409ing as "not active".
    """
    from app.api import meetings

    meeting = make_meeting(db, user.id)
    real_cancel = bot_dispatch.cancel_queued_meeting

    def dispatcher_claims_first(session, meeting_id):
        other = SessionLocal()
        try:
            other.execute(
                update(Meeting)
                .where(Meeting.id == meeting_id, Meeting.status == "queued")
                .values(status="joining")
            )
            other.commit()
        finally:
            other.close()
        return real_cancel(session, meeting_id)

    stopped = []
    monkeypatch.setattr(meetings, "cancel_queued_meeting", dispatcher_claims_first)
    monkeypatch.setattr(meetings, "stop_bot", lambda mid: stopped.append(mid) or {"status": "stopping"})

    response = meetings.stop_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id))

    assert response == {"status": "stopping"}
    assert stopped == [str(meeting.id)]
    assert reread(meeting.id).status == "joining", "the cancel overwrote a meeting the dispatcher had claimed"


def test_cancel_leaves_a_meeting_that_already_left_the_queue_alone(db, user):
    """The guard that makes the race above safe, on its own."""
    meeting = make_meeting(db, user.id, status="joining")

    assert bot_dispatch.cancel_queued_meeting(db, meeting.id) is False
    row = reread(meeting.id)
    assert row.status == "joining"
    assert row.error_message is None


@pytest.mark.parametrize("status", ["completed", "failed", "transcribing"])
def test_stop_still_refuses_meetings_with_nothing_to_stop(db, user, bot_stop_tripwire, status):
    from fastapi import HTTPException
    from app.api import meetings

    meeting = make_meeting(db, user.id, status=status)

    with pytest.raises(HTTPException) as exc:
        meetings.stop_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id))
    assert exc.value.status_code == 409
    assert reread(meeting.id).status == status
