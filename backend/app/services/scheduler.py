import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Meeting, User
from app.services import calendar_service
from app.services import google_oauth_service as oauth
from app.services.bot_service import trigger_bot_join

logger = logging.getLogger(__name__)

# How far in the past scheduled_at can be before we give up on joining
# a meeting at all - the app may have been down, or the loop otherwise
# missed the window - rather than joining a meeting that's already over.
_MISSED_GRACE_MINUTES = 15


def _claim(db: Session, meeting: Meeting, **values) -> bool:
    """
    Atomically moves a meeting out of "scheduled" - the single rule every
    transition out of that status in this sweep goes through.

    The SELECT in trigger_due_meetings is advisory: with more than one
    backend replica, two schedulers sweep the same table and both read the
    same row as "scheduled". Whoever wins this conditional UPDATE owns the
    meeting; the loser gets rowcount 0 and leaves it alone, so the bot is
    dispatched exactly once. Same in-house pattern as the status guards in
    app/api/webhooks.py.
    """
    result = db.execute(
        update(Meeting)
        .where(Meeting.id == meeting.id, Meeting.status == "scheduled")
        .values(**values)
    )
    db.commit()
    return result.rowcount == 1


def _mark_failed(db: Session, meeting: Meeting, message: str) -> None:
    """
    Unconditional, and deliberately so - only call this on a meeting this
    sweep has already claimed via _claim(), where no other replica can be
    writing the same row.
    """
    meeting.status = "failed"
    meeting.error_message = message
    db.commit()
    logger.warning("[scheduler] meeting %s marked failed: %s", meeting.id, message)


def _revalidate_calendar_meeting(db: Session, meeting: Meeting, now: datetime) -> bool:
    """
    Re-checks a calendar-sourced meeting against Google right before
    joining, since the event may have changed since the user opted in
    (see docs/google-calendar-integration-plan.md). Returns True if
    the meeting is still good to join right now; False means it was
    already handled here (failed, or pushed to a later scheduled_at)
    and trigger_due_meetings should skip it this cycle.

    Runs on a meeting trigger_due_meetings has already claimed, so the
    writes below don't need to be conditional - no other replica can be
    touching this row.
    """
    try:
        access_token = calendar_service.get_valid_access_token(str(meeting.user_id), db)
    except calendar_service.CalendarNotConnected:
        _mark_failed(
            db, meeting,
            "Google Calendar is no longer connected - could not verify this meeting before joining.",
        )
        return False
    except oauth.RevokedAccessError as e:
        _mark_failed(db, meeting, f"Could not verify meeting details before joining - {e}")
        return False

    try:
        event = calendar_service.get_event(access_token, meeting.calendar_event_id)
    except calendar_service.CalendarTransientError as e:
        # A momentary Google API blip shouldn't cost the user their
        # recording - proceed with the last-known details rather than
        # silently skipping the join.
        logger.warning(
            "[scheduler] could not verify meeting %s before joining (%s) - proceeding with last-known details.",
            meeting.id, e,
        )
        return True
    except oauth.RevokedAccessError as e:
        _mark_failed(db, meeting, f"Could not verify meeting details before joining - {e}")
        return False

    if event is None:
        _mark_failed(db, meeting, "Calendar event was cancelled before the bot could join.")
        return False

    new_start = calendar_service.parse_event_datetime(event.get("start", {}))
    if new_start is None:
        _mark_failed(db, meeting, "Calendar event no longer has a specific start time.")
        return False

    changed = False
    if new_start != meeting.scheduled_at:
        if new_start > now:
            # Give the claim back: this meeting isn't due after all, so it
            # has to return to "scheduled" or no later sweep will pick it
            # up (and the watchdog would eventually fail it out of
            # "joining" for being stuck).
            meeting.scheduled_at = new_start
            meeting.status = "scheduled"
            db.commit()
            logger.info("[scheduler] meeting %s was rescheduled to %s - will join then.", meeting.id, new_start)
            return False
        if new_start < now - timedelta(minutes=_MISSED_GRACE_MINUTES):
            _mark_failed(db, meeting, "Meeting was rescheduled earlier and has already ended.")
            return False
        # Moved slightly earlier but still within the grace window -
        # update the record and fall through to join now.
        meeting.scheduled_at = new_start
        changed = True

    detected = calendar_service.extract_meeting_url(event)
    if detected:
        platform, meeting_url = detected
        if meeting_url != meeting.meeting_url or platform != meeting.platform:
            logger.info("[scheduler] meeting %s's link changed - updating before joining.", meeting.id)
            meeting.meeting_url = meeting_url
            meeting.platform = platform
            changed = True
    # If no link is found anymore, keep joining with the last-known
    # link rather than failing outright - a fetch-by-id occasionally
    # omits conferenceData even when the event still has it.

    if changed:
        db.commit()
    return True


def trigger_due_meetings(db: Session) -> int:
    """
    Joins every scheduled meeting whose time has arrived - the
    future-dated counterpart to the immediate trigger_bot_join call in
    POST /meetings. Returns how many bots were triggered this sweep.
    """
    if not settings.calendar_scheduler_enabled:
        return 0

    now = datetime.now(timezone.utc)
    due_cutoff = now + timedelta(minutes=settings.calendar_join_lead_minutes)
    missed_cutoff = now - timedelta(minutes=_MISSED_GRACE_MINUTES)

    due_meetings = db.query(Meeting).filter(
        Meeting.status == "scheduled",
        Meeting.scheduled_at.isnot(None),
        Meeting.scheduled_at <= due_cutoff,
    ).all()

    triggered = 0
    for meeting in due_meetings:
        # The query above is only a candidate list - another replica may
        # already own this row by now, so nothing below touches the
        # meeting until _claim() confirms it's ours.
        if meeting.scheduled_at < missed_cutoff:
            message = (
                f"Missed its scheduled join time (was due at {meeting.scheduled_at.isoformat()}) - "
                "the app may have been down. Not joining a meeting that's already over."
            )
            if _claim(db, meeting, status="failed", error_message=message):
                logger.warning("[scheduler] meeting %s marked failed: %s", meeting.id, message)
            continue

        # Claim before revalidating: _revalidate_calendar_meeting makes a
        # network call to Google, and there is no point spending that
        # request - or the quota - on a meeting another replica is already
        # joining.
        if not _claim(db, meeting, status="joining"):
            continue

        if meeting.calendar_event_id and not _revalidate_calendar_meeting(db, meeting, now):
            continue

        try:
            db_user = db.query(User).filter(User.id == meeting.user_id).first()
            trigger_bot_join(
                meeting.platform, meeting.meeting_url, str(meeting.id),
                str(meeting.user_id), db_user.bot_display_name,
            )
            triggered += 1
        except Exception as e:
            meeting.status = "failed"
            meeting.error_message = f"Failed to start bot: {e}"
            db.commit()
            logger.exception("[scheduler] failed to trigger bot join for meeting %s", meeting.id)

    return triggered
