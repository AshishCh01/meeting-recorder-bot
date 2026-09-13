"""
Phase B3, item 1 - the meeting status machine closes.

The vocabulary, read off the code rather than invented:

    scheduled -> queued -> joining -> waiting_for_admission -> recording
              -> transcribing -> completed
    uploading (retry's re-upload) -> transcribing
    anything -> failed

completed and failed are terminal. The claim this file proves is *closure*:
every non-terminal status has a real, tested way out to one of them. Nothing
here re-derives a state diagram - it tests the places the status is written:

  1. The TTL table. Every non-terminal status except "scheduled" has a
     watchdog TTL, and no status is written anywhere in app/ that is outside
     the vocabulary - so a new status cannot be added without a TTL decision.
  2. The watchdog. A meeting past its TTL in each of the six statuses is
     failed; one inside it is not; terminal and "scheduled" rows never are.
  3. The scheduler. Its claim restarts the watchdog clock rather than
     inheriting the age of a meeting scheduled days ago, and a claim that
     crashes afterwards still ends somewhere terminal.
  4. The webhook's three status branches. Each UPDATE excludes a different set
     of "already past this point" statuses; a late ping must not move a
     meeting backwards.

Closure, status by status, and where each exit is tested:

  scheduled             scheduler claim / missed window     test_scheduler_claim.py
                        watchdog, only if scheduled_at is NULL  test_scheduled_without_time.py
  queued                dispatcher -> joining / failed      test_bot_dispatch_queue.py
                        watchdog                            here, and test_bot_dispatch_queue.py
  joining               webhook, watchdog                   here
  waiting_for_admission webhook, watchdog                   here
  recording             webhook, watchdog                   here, and test_bot_pool.py
  uploading             webhook, watchdog                   here, and test_retry_reupload.py
  transcribing          transcription -> completed/failed   test_transcription_queue.py
                        watchdog                            here

Postgres only. The webhook is driven over HTTP with TestClient so the bearer
token and payload validation are real; storage and transcription are faked at
the module attributes webhooks.py calls through.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.api import webhooks
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting
from app.main import app
from app.services import calendar_service, scheduler, watchdog

WEBHOOK_HEADERS = {"Authorization": "Bearer stub-token"}  # conftest's MEETING_BOT_BEARER_TOKEN

TERMINAL = ("completed", "failed")
NON_TERMINAL_WITH_TTL = ("queued", "joining", "waiting_for_admission", "recording", "uploading", "transcribing")
VOCABULARY = {"scheduled", *NON_TERMINAL_WITH_TTL, *TERMINAL}


def make_meeting(db, user_id, *, status, scheduled_at=None, calendar_event_id=None):
    meeting = Meeting(
        id=uuid.uuid4(),
        user_id=user_id,
        meeting_url="https://meet.google.com/abc-defg-hij",
        title="Design review",
        platform="google",
        status=status,
        scheduled_at=scheduled_at,
        calendar_event_id=calendar_event_id,
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


@pytest.fixture
def distinct_ttls(monkeypatch):
    """
    Every TTL pinned, and pinned to a *different* value. The shipped defaults
    give joining and waiting_for_admission the same 10 minutes, so a
    _ttl_minutes_for that returned one for the other would pass any test run
    on defaults. With these, a crossed wire sweeps too early or too late.
    """
    monkeypatch.setattr(settings, "watchdog_enabled", True)
    monkeypatch.setattr(settings, "watchdog_queued_ttl_minutes", 31)
    monkeypatch.setattr(settings, "watchdog_joining_ttl_minutes", 11)
    monkeypatch.setattr(settings, "watchdog_admission_ttl_minutes", 13)
    monkeypatch.setattr(settings, "max_recording_duration_minutes", 90)
    monkeypatch.setattr(settings, "watchdog_recording_margin_minutes", 17)
    monkeypatch.setattr(settings, "watchdog_uploading_ttl_minutes", 23)
    monkeypatch.setattr(settings, "watchdog_transcribing_ttl_minutes", 37)
    return {
        "queued": 31,
        "joining": 11,
        "waiting_for_admission": 13,
        "recording": 107,
        "uploading": 23,
        "transcribing": 37,
    }


@pytest.fixture
def pipeline(monkeypatch):
    """Storage and transcription, faked where the webhook calls them."""
    state = {"submitted": [], "signed_url_error": None}

    def signed_url(path, *args, **kwargs):
        if state["signed_url_error"] is not None:
            raise state["signed_url_error"]
        return f"https://signed/{path}"

    monkeypatch.setattr(webhooks, "get_signed_recording_url", signed_url)
    monkeypatch.setattr(webhooks, "submit_transcription", lambda meeting_id, path: state["submitted"].append(meeting_id))
    return state


def post_webhook(user, meeting, status, **extra):
    body = {"user_id": str(user.id), "meeting_id": str(meeting.id), "status": status, **extra}
    return TestClient(app).post("/webhooks/recording-complete", json=body, headers=WEBHOOK_HEADERS)


def completed_body(user, meeting):
    return {"recording_path": f"{user.id}/{meeting.id}/recording.m4a", "duration_seconds": 1800}


# ---------------------------------------------------------------------------
# 1. The TTL table
# ---------------------------------------------------------------------------

def test_every_non_terminal_status_but_scheduled_has_a_positive_ttl(distinct_ttls):
    assert set(watchdog._NON_TERMINAL_STATUSES) == set(NON_TERMINAL_WITH_TTL)
    for status in NON_TERMINAL_WITH_TTL:
        assert watchdog._ttl_minutes_for(status) == distinct_ttls[status], status


@pytest.mark.parametrize("status", ["scheduled", *TERMINAL])
def test_scheduled_and_terminal_statuses_have_no_ttl(status):
    """
    Terminal rows are done. "scheduled" is waiting for a time that may be days
    away, so a TTL on updated_at would fail every meeting booked in advance -
    its way out is the scheduler's missed-window check instead.
    """
    with pytest.raises(ValueError):
        watchdog._ttl_minutes_for(status)


def test_no_status_is_written_outside_the_vocabulary():
    """
    The table above is only complete if nothing writes a status it does not
    list. Scans app/ for every `status = "..."` / `status="..."` literal - a
    Meeting(...) constructor, an ORM assignment or a Core .values(). A new
    status lands here first, which is where the question "what is its TTL?"
    belongs.

    Not exhaustive by construction: a status arriving through a variable (the
    webhook's payload.status is never written directly) is not a literal. The
    webhook only writes the literals it branches on, which this does see.
    """
    app_dir = Path(__file__).resolve().parent.parent / "app"
    written = {}
    for path in app_dir.rglob("*.py"):
        for match in re.finditer(r'\bstatus\s*=\s*"([a-z_]+)"', path.read_text(encoding="utf-8")):
            written.setdefault(match.group(1), set()).add(path.name)

    assert written, "the scan found nothing - the pattern is broken, not the code clean"
    unknown = {s: sorted(files) for s, files in written.items() if s not in VOCABULARY}
    assert unknown == {}, f"statuses written with no place in the machine: {unknown}"


def test_recording_ttl_outlasts_the_bots_own_hard_cap():
    """
    meeting-bot ends a recording at MAX_RECORDING_DURATION_MINUTES on its own.
    The watchdog must not fail a meeting the bot could still legitimately be
    recording; asserted on the shipped defaults, as a config relationship.

    (The matching relationship for "queued" - the dispatcher gives up before
    the watchdog would sweep - is test_bot_dispatch_queue.py's
    test_the_dispatcher_gives_up_before_the_watchdog_would_sweep.)
    """
    assert watchdog._ttl_minutes_for("recording") > settings.max_recording_duration_minutes
    assert settings.watchdog_recording_margin_minutes > 0


# ---------------------------------------------------------------------------
# 2. The watchdog, per status
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", NON_TERMINAL_WITH_TTL)
def test_a_meeting_stuck_past_its_ttl_is_failed(db, user, distinct_ttls, status):
    stuck = make_meeting(db, user.id, status=status)
    age_meeting(stuck.id, minutes=distinct_ttls[status] + 1)

    assert watchdog.sweep_stale_meetings(db) == 1

    row = reread(stuck.id)
    assert row.status == "failed"
    assert f"'{status}'" in row.error_message
    assert f"{distinct_ttls[status]} minutes" in row.error_message


@pytest.mark.parametrize("status", NON_TERMINAL_WITH_TTL)
def test_a_meeting_inside_its_ttl_is_left_alone(db, user, distinct_ttls, status):
    """One minute short of the TTL - close enough that a crossed TTL would sweep it."""
    fresh = make_meeting(db, user.id, status=status)
    age_meeting(fresh.id, minutes=distinct_ttls[status] - 1)

    assert watchdog.sweep_stale_meetings(db) == 0
    row = reread(fresh.id)
    assert row.status == status
    assert row.error_message is None


@pytest.mark.parametrize("status", ["scheduled", *TERMINAL])
def test_the_watchdog_never_touches_scheduled_or_terminal_meetings(db, user, distinct_ttls, status):
    """
    A *valid* scheduled row, with a time to wait for. One with no scheduled_at
    is swept - test_scheduled_without_time.py.
    """
    ancient = make_meeting(
        db, user.id, status=status,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=7) if status == "scheduled" else None,
    )
    age_meeting(ancient.id, minutes=60 * 24 * 30)

    assert watchdog.sweep_stale_meetings(db) == 0
    row = reread(ancient.id)
    assert row.status == status
    assert row.error_message is None


# ---------------------------------------------------------------------------
# 3. The scheduler's transition points
# ---------------------------------------------------------------------------

@pytest.fixture
def scheduler_flags(monkeypatch):
    """Same two flags test_scheduler_claim.py pins, for the same reasons."""
    monkeypatch.setattr(settings, "calendar_scheduler_enabled", True)
    monkeypatch.setattr(settings, "bot_dispatch_use_queue", False)


def test_the_claim_restarts_the_watchdog_clock(db, user, distinct_ttls, scheduler_flags, monkeypatch):
    """
    A meeting booked three days ahead has an updated_at three days old by the
    time it is due. The claim's conditional UPDATE must bump it; otherwise the
    very next sweep sees a "joining" meeting far past its 11-minute TTL and
    fails it before the bot has had a chance to connect.
    """
    monkeypatch.setattr(scheduler, "trigger_bot_join", lambda *args: {"status": "ok"})

    booked = make_meeting(
        db, user.id, status="scheduled",
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    age_meeting(booked.id, minutes=60 * 24 * 3)

    assert scheduler.trigger_due_meetings(db) == 1
    assert reread(booked.id).status == "joining"

    assert watchdog.sweep_stale_meetings(db) == 0
    assert reread(booked.id).status == "joining", "the claim inherited the booking's age"


def test_a_bot_that_will_not_start_fails_the_claimed_meeting(db, user, scheduler_flags, monkeypatch):
    """joining is written by the claim; a trigger that raises must not leave it there."""
    def refuse(*args):
        raise RuntimeError("recorder unreachable")

    monkeypatch.setattr(scheduler, "trigger_bot_join", refuse)
    due = make_meeting(db, user.id, status="scheduled", scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1))

    assert scheduler.trigger_due_meetings(db) == 0

    row = reread(due.id)
    assert row.status == "failed"
    assert "recorder unreachable" in row.error_message


def test_a_claim_that_crashes_mid_revalidation_is_still_swept(db, user, distinct_ttls, scheduler_flags, monkeypatch):
    """
    _revalidate_calendar_meeting only catches the errors it expects. Anything
    else propagates out of the sweep (main.py's loop logs it) with the meeting
    already claimed into "joining" and no bot behind it. Nothing retries that
    row - the scheduler only looks at "scheduled" - so the watchdog is its only
    way out, and this shows it is one.
    """
    trigger_calls = []
    monkeypatch.setattr(scheduler, "trigger_bot_join", lambda *args: trigger_calls.append(args))

    def unexpected(user_id, db):
        raise KeyError("token cache corrupted")

    monkeypatch.setattr(calendar_service, "get_valid_access_token", unexpected)

    claimed = make_meeting(
        db, user.id, status="scheduled",
        scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        calendar_event_id="evt-1",
    )

    with pytest.raises(KeyError):
        scheduler.trigger_due_meetings(db)
    db.rollback()

    assert trigger_calls == []
    assert reread(claimed.id).status == "joining"

    # A later scheduler sweep cannot see it...
    assert scheduler.trigger_due_meetings(db) == 0
    assert reread(claimed.id).status == "joining"

    # ...but the watchdog does, once the joining TTL passes.
    age_meeting(claimed.id, minutes=distinct_ttls["joining"] + 1)
    assert watchdog.sweep_stale_meetings(db) == 1
    assert reread(claimed.id).status == "failed"


# ---------------------------------------------------------------------------
# 4. The webhook's status branches
# ---------------------------------------------------------------------------

def test_the_happy_path_walks_forward_one_webhook_at_a_time(db, user, pipeline):
    meeting = make_meeting(db, user.id, status="joining")

    assert post_webhook(user, meeting, "waiting_for_admission").json() == {"status": "received"}
    assert reread(meeting.id).status == "waiting_for_admission"

    assert post_webhook(user, meeting, "recording").json() == {"status": "received"}
    assert reread(meeting.id).status == "recording"

    assert post_webhook(user, meeting, "completed", **completed_body(user, meeting)).json() == {"status": "received"}
    row = reread(meeting.id)
    assert row.status == "transcribing"
    assert row.recording_url == f"https://signed/{user.id}/{meeting.id}/recording.m4a"
    assert row.duration_seconds == 1800
    assert pipeline["submitted"] == [str(meeting.id)]


def test_a_lost_admission_ping_does_not_stop_recording_from_landing(db, user, pipeline):
    """Both pings are fire-and-forget, single attempt. Either can go missing."""
    meeting = make_meeting(db, user.id, status="joining")

    post_webhook(user, meeting, "recording")

    assert reread(meeting.id).status == "recording"


@pytest.mark.parametrize("already", ["recording", "transcribing", "completed", "failed"])
def test_a_late_admission_ping_never_moves_a_meeting_backwards(db, user, pipeline, already):
    meeting = make_meeting(db, user.id, status=already)

    assert post_webhook(user, meeting, "waiting_for_admission").status_code == 200

    assert reread(meeting.id).status == already


@pytest.mark.parametrize("already", ["transcribing", "completed", "failed"])
def test_a_late_recording_ping_never_moves_a_meeting_backwards(db, user, pipeline, already):
    """The one the plan names: a recording ping after completed must not un-complete it."""
    meeting = make_meeting(db, user.id, status=already)

    assert post_webhook(user, meeting, "recording").status_code == 200

    assert reread(meeting.id).status == already


def test_a_ping_restarts_the_watchdog_clock_for_its_new_status(db, user, distinct_ttls, pipeline):
    """
    The recording TTL is measured from the recording ping, not from whatever
    last wrote the row. The row is aged past even the recording TTL, so a
    webhook write that failed to bump updated_at would have the very next
    sweep fail a meeting that started recording a moment ago.
    """
    meeting = make_meeting(db, user.id, status="waiting_for_admission")
    age_meeting(meeting.id, minutes=distinct_ttls["recording"] + 1)

    post_webhook(user, meeting, "recording")

    assert watchdog.sweep_stale_meetings(db) == 0
    assert reread(meeting.id).status == "recording"


@pytest.mark.parametrize("from_status", ["joining", "waiting_for_admission", "recording", "uploading"])
def test_a_completed_report_moves_any_in_flight_meeting_to_transcribing(db, user, pipeline, from_status):
    """
    joining and waiting_for_admission included: both pings can be lost, and the
    final report retries where they do not. uploading is retry's re-upload.
    """
    meeting = make_meeting(db, user.id, status=from_status)

    assert post_webhook(user, meeting, "completed", **completed_body(user, meeting)).json() == {"status": "received"}

    assert reread(meeting.id).status == "transcribing"
    assert pipeline["submitted"] == [str(meeting.id)]


@pytest.mark.parametrize("from_status", ["joining", "waiting_for_admission", "recording", "uploading"])
def test_a_failed_report_ends_any_in_flight_meeting(db, user, pipeline, from_status):
    meeting = make_meeting(db, user.id, status=from_status)

    post_webhook(user, meeting, "failed", error_message="Not admitted within 5 minutes")

    row = reread(meeting.id)
    assert row.status == "failed"
    assert row.error_message == "Not admitted within 5 minutes"
    assert pipeline["submitted"] == []


@pytest.mark.parametrize("already", ["transcribing", "completed"])
def test_a_late_failed_report_never_undoes_a_recording_that_arrived(db, user, pipeline, already):
    meeting = make_meeting(db, user.id, status=already)

    assert post_webhook(user, meeting, "failed", error_message="Cancelled by user").status_code == 200

    row = reread(meeting.id)
    assert row.status == already
    assert row.error_message is None


def test_a_session_that_produced_no_file_still_ends_failed(db, user, pipeline):
    """
    meeting-bot's lifecycle marks a naturally-ended session "uploading" and
    only moves it on inside uploadRecording, which it skips when ffmpeg left no
    file. notifyBackend then reports status "uploading" with no recording_path.
    The webhook has no branch for that, and must not leave the meeting where it
    was: it falls through to the failure write.
    """
    meeting = make_meeting(db, user.id, status="recording")

    assert post_webhook(user, meeting, "uploading", recording_path=None).json() == {"status": "received"}

    row = reread(meeting.id)
    assert row.status == "failed"
    assert row.error_message == "Recording failed"
    assert pipeline["submitted"] == []


def test_a_completed_report_whose_file_is_missing_fails_instead_of_transcribing(db, user, pipeline):
    meeting = make_meeting(db, user.id, status="recording")
    pipeline["signed_url_error"] = RuntimeError("Object not found")

    assert post_webhook(user, meeting, "completed", **completed_body(user, meeting)).json() == {"status": "received"}

    row = reread(meeting.id)
    assert row.status == "failed"
    assert "file not found in storage" in row.error_message
    assert pipeline["submitted"] == []
