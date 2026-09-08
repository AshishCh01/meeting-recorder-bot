"""
Phase A1 - the scheduler must claim a meeting atomically before acting on it.

trigger_due_meetings() used to SELECT rows with status "scheduled" and then,
separately, write status "joining". Two backend replicas sweeping at the same
time both read the same row and both called trigger_bot_join(): one meeting,
two bots. The fix is a conditional UPDATE ... WHERE id = :id AND status =
'scheduled', skipping the row when rowcount == 0.

The tests below are the proof, per docs/scaling-plan.md "Gate":
  1. two connections both SELECT the same row, both claim, exactly one wins
  2. two concurrent sweeps over one due meeting trigger exactly one bot
  3. the missed-window and revalidation-skip paths still behave - both are
     easy to break when reordering claim vs. revalidate
"""
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db.database import SessionLocal
from app.db.models import Meeting
from app.services import calendar_service, scheduler

MEET_URL = "https://meet.google.com/abc-defg-hij"
OTHER_MEET_URL = "https://meet.google.com/zzz-yyyy-xxx"


def make_meeting(db, user_id, *, due_minutes=-1, status="scheduled", calendar_event_id=None):
    """A meeting due `due_minutes` from now (negative = already due)."""
    meeting = Meeting(
        id=uuid.uuid4(),
        user_id=user_id,
        meeting_url=MEET_URL,
        title="Standup",
        platform="google",
        status=status,
        scheduled_at=datetime.now(timezone.utc) + timedelta(minutes=due_minutes),
        calendar_event_id=calendar_event_id,
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return meeting


def status_of(db, meeting_id):
    """Re-reads a meeting from the database, ignoring anything cached."""
    db.expire_all()
    return db.query(Meeting).filter(Meeting.id == meeting_id).one()


def sweep_in_new_session():
    """One replica's sweep, on its own connection."""
    session = SessionLocal()
    try:
        return scheduler.trigger_due_meetings(session)
    finally:
        session.close()


class Recorder:
    """Thread-safe stand-in for trigger_bot_join."""

    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, platform, meeting_url, meeting_id, user_id, bot_display_name):
        with self._lock:
            self.calls.append({
                "platform": platform,
                "meeting_url": meeting_url,
                "meeting_id": meeting_id,
                "bot_display_name": bot_display_name,
            })
        return {"status": "ok"}


def run_in_parallel(*targets):
    """Runs each callable on its own thread; re-raises whatever they raised."""
    out = {}
    errors = []
    lock = threading.Lock()

    def wrap(index, fn):
        def inner():
            try:
                value = fn()
            except BaseException as e:  # noqa: BLE001 - re-raised below
                with lock:
                    errors.append(e)
            else:
                with lock:
                    out[index] = value
        return inner

    threads = [threading.Thread(target=wrap(i, fn)) for i, fn in enumerate(targets)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "a worker thread deadlocked"
    if errors:
        raise errors[0]
    return [out[i] for i in range(len(targets))]


@pytest.fixture
def sync_claims(monkeypatch):
    """
    Forces the interleaving the race needs: both sweeps must have finished
    their SELECT and be sitting on the claim before either is let through.
    The real _claim still does the work - the barrier only removes the timing
    luck, so a pass means the conditional UPDATE closed the race, not that the
    two threads happened not to overlap.
    """
    real_claim = scheduler._claim
    barrier = threading.Barrier(2, timeout=15)

    def synced_claim(db, meeting, **values):
        barrier.wait()
        return real_claim(db, meeting, **values)

    monkeypatch.setattr(scheduler, "_claim", synced_claim)
    return barrier


# ---------------------------------------------------------------------------
# Gate 1 - two connections, one row, one winner
# ---------------------------------------------------------------------------

def test_two_connections_both_select_then_both_claim_exactly_one_wins(db, user):
    """
    The gate's first requirement as directly as it can be stated: two real
    SessionLocal() connections both read the row as "scheduled", then both
    attempt the claim. Exactly one gets rowcount == 1.
    """
    meeting_id = make_meeting(db, user.id).id

    session_a, session_b = SessionLocal(), SessionLocal()
    try:
        row_a = session_a.query(Meeting).filter(Meeting.id == meeting_id).one()
        row_b = session_b.query(Meeting).filter(Meeting.id == meeting_id).one()
        # Both connections genuinely saw it as claimable - without this the
        # test could pass for the wrong reason.
        assert row_a.status == "scheduled"
        assert row_b.status == "scheduled"

        barrier = threading.Barrier(2, timeout=15)

        def claimer(session, row):
            def go():
                barrier.wait()
                return scheduler._claim(session, row, status="joining")
            return go

        outcomes = run_in_parallel(claimer(session_a, row_a), claimer(session_b, row_b))
        assert sorted(outcomes) == [False, True], f"expected one winner, got {outcomes}"
    finally:
        session_a.close()
        session_b.close()

    assert status_of(db, meeting_id).status == "joining"


def test_claim_ignores_a_meeting_that_already_moved_on(db, user):
    """A row that is no longer 'scheduled' cannot be claimed at all."""
    meeting = make_meeting(db, user.id, status="joining")
    assert scheduler._claim(db, meeting, status="joining") is False
    assert status_of(db, meeting.id).status == "joining"


# ---------------------------------------------------------------------------
# Gate 2 - two concurrent sweeps, one bot
# ---------------------------------------------------------------------------

def test_concurrent_sweeps_trigger_exactly_one_bot(db, user, monkeypatch, sync_claims):
    """
    The bug as a user would have hit it: two backend replicas, one due
    meeting, two bots dialling into the same call. Exactly one trigger now.
    """
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)

    meeting_id = make_meeting(db, user.id).id

    counts = run_in_parallel(sweep_in_new_session, sweep_in_new_session)

    assert len(recorder.calls) == 1, f"bot dispatched {len(recorder.calls)} times"
    assert sorted(counts) == [0, 1], f"sweeps reported {counts} triggers"
    assert recorder.calls[0]["meeting_id"] == str(meeting_id)
    assert recorder.calls[0]["bot_display_name"] == "Test Notetaker"
    assert status_of(db, meeting_id).status == "joining"


def test_single_sweep_still_joins_a_due_meeting(db, user, monkeypatch):
    """Baseline: with one replica the claim always wins, behaviour unchanged."""
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)

    meeting = make_meeting(db, user.id)

    assert scheduler.trigger_due_meetings(db) == 1
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["meeting_url"] == MEET_URL
    assert status_of(db, meeting.id).status == "joining"


def test_sweep_skips_a_meeting_another_replica_claimed(db, user, monkeypatch):
    """
    The loser's path in isolation: the row moves to 'joining' between this
    sweep's SELECT and its claim, so it must be skipped, not re-dispatched.
    """
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)

    meeting_id = make_meeting(db, user.id).id
    real_claim = scheduler._claim

    def claim_after_someone_else(session, row, **values):
        # The other replica wins in the gap between SELECT and claim.
        other = SessionLocal()
        try:
            other.query(Meeting).filter(Meeting.id == meeting_id).update({"status": "joining"})
            other.commit()
        finally:
            other.close()
        return real_claim(session, row, **values)

    monkeypatch.setattr(scheduler, "_claim", claim_after_someone_else)

    assert scheduler.trigger_due_meetings(db) == 0
    assert recorder.calls == []


# ---------------------------------------------------------------------------
# Gate 3a - the missed-window path
# ---------------------------------------------------------------------------

def test_missed_window_marks_failed_without_triggering(db, user, monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)

    # Past the 15-minute grace window.
    meeting = make_meeting(db, user.id, due_minutes=-40)

    assert scheduler.trigger_due_meetings(db) == 0
    assert recorder.calls == []

    row = status_of(db, meeting.id)
    assert row.status == "failed"
    assert "Missed its scheduled join time" in row.error_message


def test_missed_window_does_not_overwrite_a_claimed_meeting(db, user, monkeypatch):
    """
    The missed-window write is conditional too, so a replica arriving late
    cannot stamp 'failed' over a meeting another replica is already joining.
    """
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)

    meeting_id = make_meeting(db, user.id, due_minutes=-40).id

    other = SessionLocal()
    try:
        other.query(Meeting).filter(Meeting.id == meeting_id).update({"status": "joining"})
        other.commit()
    finally:
        other.close()

    assert scheduler.trigger_due_meetings(db) == 0
    row = status_of(db, meeting_id)
    assert row.status == "joining", "a late sweep overwrote a claimed meeting"
    assert row.error_message is None


def test_concurrent_sweeps_fail_a_missed_meeting_without_triggering(db, user, monkeypatch, sync_claims):
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)

    meeting = make_meeting(db, user.id, due_minutes=-40)

    counts = run_in_parallel(sweep_in_new_session, sweep_in_new_session)

    assert counts == [0, 0]
    assert recorder.calls == []
    assert status_of(db, meeting.id).status == "failed"


# ---------------------------------------------------------------------------
# Gate 3b - the revalidation-skip paths, now that the claim comes first
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_calendar(monkeypatch):
    """Stubs the two Google calls _revalidate_calendar_meeting makes."""
    monkeypatch.setattr(calendar_service, "get_valid_access_token", lambda user_id, db: "token")

    state = {"event": None, "raises": None, "get_event_calls": []}
    lock = threading.Lock()

    def get_event(access_token, event_id):
        with lock:
            state["get_event_calls"].append(event_id)
        if state["raises"] is not None:
            raise state["raises"]
        return state["event"]

    monkeypatch.setattr(calendar_service, "get_event", get_event)
    return state


def event_at(start: datetime, url: str = MEET_URL) -> dict:
    return {
        "id": "evt-1",
        "status": "confirmed",
        "start": {"dateTime": start.isoformat()},
        "hangoutLink": url,
    }


def test_revalidation_cancelled_event_fails_without_triggering(db, user, monkeypatch, stub_calendar):
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)
    stub_calendar["event"] = None  # Google says it is gone

    meeting = make_meeting(db, user.id, calendar_event_id="evt-1")

    assert scheduler.trigger_due_meetings(db) == 0
    assert recorder.calls == []

    row = status_of(db, meeting.id)
    assert row.status == "failed"
    assert "cancelled" in row.error_message


def test_revalidation_rescheduled_later_releases_the_claim(db, user, monkeypatch, stub_calendar):
    """
    The regression this reorder invites. The claim now happens *before*
    revalidation, so a meeting that turns out to have moved to a later time
    must be handed back to 'scheduled' - otherwise it sits in 'joining'
    forever, invisible to every later sweep, until the watchdog fails it.
    """
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)

    new_start = (datetime.now(timezone.utc) + timedelta(minutes=45)).replace(microsecond=0)
    stub_calendar["event"] = event_at(new_start)

    meeting = make_meeting(db, user.id, calendar_event_id="evt-1")

    assert scheduler.trigger_due_meetings(db) == 0
    assert recorder.calls == []

    row = status_of(db, meeting.id)
    assert row.status == "scheduled", "rescheduled meeting was left stuck in 'joining'"
    assert row.scheduled_at == new_start
    assert row.error_message is None


def test_a_released_meeting_is_joinable_again_on_a_later_sweep(db, user, monkeypatch, stub_calendar):
    """The release is only worth anything if a later sweep picks it back up."""
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)

    later = (datetime.now(timezone.utc) + timedelta(minutes=45)).replace(microsecond=0)
    stub_calendar["event"] = event_at(later)
    meeting = make_meeting(db, user.id, calendar_event_id="evt-1")

    assert scheduler.trigger_due_meetings(db) == 0
    assert status_of(db, meeting.id).status == "scheduled"

    # Time passes; the event is now due, and unchanged since.
    due_now = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0)
    db.query(Meeting).filter(Meeting.id == meeting.id).update({"scheduled_at": due_now})
    db.commit()
    stub_calendar["event"] = event_at(due_now)

    assert scheduler.trigger_due_meetings(db) == 1
    assert len(recorder.calls) == 1
    assert status_of(db, meeting.id).status == "joining"


def test_revalidation_rescheduled_earlier_and_already_over_fails(db, user, monkeypatch, stub_calendar):
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)

    stub_calendar["event"] = event_at(datetime.now(timezone.utc) - timedelta(minutes=90))
    meeting = make_meeting(db, user.id, calendar_event_id="evt-1")

    assert scheduler.trigger_due_meetings(db) == 0
    assert recorder.calls == []
    row = status_of(db, meeting.id)
    assert row.status == "failed"
    assert "already ended" in row.error_message


def test_revalidation_transient_google_error_still_joins(db, user, monkeypatch, stub_calendar):
    """A momentary Google blip must not cost the user their recording."""
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)
    stub_calendar["raises"] = calendar_service.CalendarTransientError("503 from Google")

    meeting = make_meeting(db, user.id, calendar_event_id="evt-1")

    assert scheduler.trigger_due_meetings(db) == 1
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["meeting_url"] == MEET_URL
    assert status_of(db, meeting.id).status == "joining"


def test_revalidation_calendar_disconnected_fails(db, user, monkeypatch, stub_calendar):
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)

    def boom(user_id, db):
        raise calendar_service.CalendarNotConnected()

    monkeypatch.setattr(calendar_service, "get_valid_access_token", boom)

    meeting = make_meeting(db, user.id, calendar_event_id="evt-1")

    assert scheduler.trigger_due_meetings(db) == 0
    assert recorder.calls == []
    row = status_of(db, meeting.id)
    assert row.status == "failed"
    assert "no longer connected" in row.error_message


def test_revalidation_picks_up_a_changed_link_before_joining(db, user, monkeypatch, stub_calendar):
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)

    meeting = make_meeting(db, user.id, calendar_event_id="evt-1")
    stub_calendar["event"] = event_at(meeting.scheduled_at, url=OTHER_MEET_URL)

    assert scheduler.trigger_due_meetings(db) == 1
    assert recorder.calls[0]["meeting_url"] == OTHER_MEET_URL
    assert status_of(db, meeting.id).meeting_url == OTHER_MEET_URL


def test_concurrent_sweeps_call_google_only_once(db, user, monkeypatch, stub_calendar, sync_claims):
    """
    Ordering, asserted rather than eyeballed: the claim happens *before*
    _revalidate_calendar_meeting, so the replica that loses never spends a
    Google API request on a meeting it was never going to join.
    """
    recorder = Recorder()
    monkeypatch.setattr(scheduler, "trigger_bot_join", recorder)

    meeting = make_meeting(db, user.id, calendar_event_id="evt-1")
    stub_calendar["event"] = event_at(meeting.scheduled_at)

    counts = run_in_parallel(sweep_in_new_session, sweep_in_new_session)

    assert sorted(counts) == [0, 1]
    assert len(recorder.calls) == 1
    assert stub_calendar["get_event_calls"] == ["evt-1"], (
        f"the losing replica also called Google: {stub_calendar['get_event_calls']}"
    )
