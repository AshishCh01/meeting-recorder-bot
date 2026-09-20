"""
B3 finding 3 - a "scheduled" meeting with no scheduled_at must not sit forever.

"scheduled" has no watchdog TTL, on purpose: a meeting booked for next week
legitimately waits, and its way out is the scheduler (joined when due, failed
once past the missed-join grace window). But the scheduler only looks at rows
with a scheduled_at, and every calendar-booked row has one - calendar.py
refuses an event without a start time.

The only writer of scheduled_at = NULL is POST /meetings, which inserted the
row as "scheduled" and moved it to its dispatch status ("joining", or
"queued" with the queue on) in a second commit. A process that died between
the two, or a second commit that failed along with the "failed" write in its
error handler, left a row that nothing would ever revisit.

Two changes, tested separately:

  1. The watchdog fails "scheduled" rows whose scheduled_at is NULL once they
     are older than the joining TTL. Such a row is invalid by construction -
     it was always a moment away from being dispatched - so this is the
     guarantee, and it also cleans up any row already stranded before the fix.
     Valid scheduled rows (scheduled_at set) are still never touched, whatever
     their age and whether or not the scheduler is enabled.
  2. POST /meetings inserts the row with its dispatch status directly, so it
     no longer produces the invalid state at all.

Postgres only.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.api import meetings
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting
from app.models.meeting import MeetingCreate
from app.services import scheduler, watchdog

from tests.test_meeting_status_machine import age_meeting

MEET_URL = "https://meet.google.com/abc-defg-hij"


def make_meeting(db, user_id, *, scheduled_at, status="scheduled"):
    meeting = Meeting(
        id=uuid.uuid4(),
        user_id=user_id,
        meeting_url=MEET_URL,
        title="1:1",
        platform="google",
        status=status,
        scheduled_at=scheduled_at,
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


def rows_for(user_id):
    other = SessionLocal()
    try:
        return other.query(Meeting).filter(Meeting.user_id == user_id).all()
    finally:
        other.close()


@pytest.fixture
def flags(monkeypatch):
    monkeypatch.setattr(settings, "watchdog_enabled", True)
    monkeypatch.setattr(settings, "watchdog_joining_ttl_minutes", 11)
    monkeypatch.setattr(settings, "calendar_scheduler_enabled", True)
    monkeypatch.setattr(settings, "bot_dispatch_use_queue", False)


# ---------------------------------------------------------------------------
# 1. The watchdog
# ---------------------------------------------------------------------------

def test_a_scheduled_row_with_no_time_is_failed_once_past_the_ttl(db, user, flags):
    orphan = make_meeting(db, user.id, scheduled_at=None)
    age_meeting(orphan.id, minutes=12)

    assert scheduler.trigger_due_meetings(db) == 0, "the scheduler was never going to see it"
    assert watchdog.sweep_stale_meetings(db) == 1

    row = reread(orphan.id)
    assert row.status == "failed"
    assert "'scheduled'" in row.error_message
    assert "no scheduled time" in row.error_message
    assert "11 minutes" in row.error_message


def test_one_still_inside_the_ttl_is_left_alone(db, user, flags):
    """
    The legitimate lifetime of this state is the gap between two commits in
    POST /meetings - on code from before the fix, which may still be serving
    during a rolling deploy.
    """
    fresh = make_meeting(db, user.id, scheduled_at=None)
    age_meeting(fresh.id, minutes=10)

    assert watchdog.sweep_stale_meetings(db) == 0
    assert reread(fresh.id).status == "scheduled"


@pytest.mark.parametrize("due_in_minutes", [60 * 24 * 7, -1, -60])
def test_valid_scheduled_meetings_are_never_swept_whatever_their_age(db, user, flags, due_in_minutes):
    """
    Booked a week out, due now, or past the grace window (the scheduler's to
    fail, with its own message). All created a month ago.
    """
    booked = make_meeting(db, user.id, scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=due_in_minutes))
    age_meeting(booked.id, minutes=60 * 24 * 30)

    assert watchdog.sweep_stale_meetings(db) == 0
    row = reread(booked.id)
    assert row.status == "scheduled"
    assert row.error_message is None


def test_a_valid_scheduled_meeting_is_still_joined_normally(db, user, flags, monkeypatch):
    joins = []
    monkeypatch.setattr(scheduler, "trigger_bot_join", lambda *args, **kwargs: joins.append(args[2]))
    due = make_meeting(db, user.id, scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    age_meeting(due.id, minutes=60 * 24 * 3)

    assert watchdog.sweep_stale_meetings(db) == 0
    assert scheduler.trigger_due_meetings(db) == 1
    assert joins == [str(due.id)]
    assert reread(due.id).status == "joining"


def test_with_the_scheduler_disabled_valid_meetings_wait_and_only_invalid_ones_fail(db, user, flags, monkeypatch):
    """
    CALENDAR_SCHEDULER_ENABLED=false parks every booked meeting - intentional,
    and not the watchdog's to second-guess, so none of them is failed. The
    invalid row is failed regardless: the watchdog does not depend on the
    scheduler running.
    """
    monkeypatch.setattr(settings, "calendar_scheduler_enabled", False)
    overdue = make_meeting(db, user.id, scheduled_at=datetime.now(timezone.utc) - timedelta(hours=2))
    future = make_meeting(db, user.id, scheduled_at=datetime.now(timezone.utc) + timedelta(days=1))
    orphan = make_meeting(db, user.id, scheduled_at=None)
    for m in (overdue, future, orphan):
        age_meeting(m.id, minutes=60 * 24)

    assert scheduler.trigger_due_meetings(db) == 0
    assert watchdog.sweep_stale_meetings(db) == 1

    assert reread(overdue.id).status == "scheduled"
    assert reread(future.id).status == "scheduled"
    assert reread(orphan.id).status == "failed"


def test_the_watchdog_switch_still_turns_the_new_sweep_off_too(db, user, flags, monkeypatch):
    monkeypatch.setattr(settings, "watchdog_enabled", False)
    orphan = make_meeting(db, user.id, scheduled_at=None)
    age_meeting(orphan.id, minutes=60)

    assert watchdog.sweep_stale_meetings(db) == 0
    assert reread(orphan.id).status == "scheduled"


# ---------------------------------------------------------------------------
# 2. POST /meetings no longer produces the state
# ---------------------------------------------------------------------------

class _ProcessKilled(BaseException):
    """Not an Exception: stands in for the process dying mid-request."""


def create(db, user):
    return meetings.create_meeting(payload=MeetingCreate(meeting_url=MEET_URL), db=db, user_id=str(user.id))


def test_a_crash_while_creating_a_meeting_leaves_no_row_stuck_in_scheduled(db, user, flags, monkeypatch):
    """
    The partial commit, staged: the process dies while working out the
    dispatch status, after the old code had already committed "scheduled".
    """
    def killed():
        raise _ProcessKilled()

    monkeypatch.setattr(meetings, "initial_status", killed)
    monkeypatch.setattr(meetings, "trigger_bot_join", lambda *args, **kwargs: None)

    with pytest.raises(_ProcessKilled):
        create(db, user)
    db.rollback()

    stuck = [m for m in rows_for(user.id) if m.status == "scheduled"]
    assert stuck == [], "a crash mid-create left a 'scheduled' row with no scheduled time"


@pytest.mark.parametrize("use_queue, expected", [(False, "joining"), (True, "queued")])
def test_the_row_is_already_at_its_dispatch_status_when_the_bot_is_triggered(db, user, flags, monkeypatch, use_queue, expected):
    """
    Unchanged from before the fix, and the C2 property worth keeping: the row
    the trigger (or the dispatch job it enqueues) reads is committed with its
    dispatch status - never "scheduled", never uncommitted.
    """
    monkeypatch.setattr(settings, "bot_dispatch_use_queue", use_queue)
    seen = []
    monkeypatch.setattr(meetings, "trigger_bot_join", lambda platform, url, meeting_id, *rest, **kwargs: seen.append(reread(meeting_id).status))

    result = create(db, user)

    assert seen == [expected]
    assert result["status"] == expected
    assert [m.status for m in rows_for(user.id)] == [expected]


def test_a_trigger_that_fails_still_ends_the_meeting_failed(db, user, flags, monkeypatch):
    def refuse(*args, **kwargs):
        raise RuntimeError("bot unreachable")

    monkeypatch.setattr(meetings, "trigger_bot_join", refuse)

    with pytest.raises(Exception) as exc:
        create(db, user)

    assert getattr(exc.value, "status_code", None) == 502
    rows = rows_for(user.id)
    assert [m.status for m in rows] == ["failed"]
    assert "bot unreachable" in rows[0].error_message
