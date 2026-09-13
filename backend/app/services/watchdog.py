import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Meeting

logger = logging.getLogger(__name__)


def _ttl_minutes_for(status: str) -> int:
    if status == "queued":
        return settings.watchdog_queued_ttl_minutes
    if status == "joining":
        return settings.watchdog_joining_ttl_minutes
    if status == "waiting_for_admission":
        return settings.watchdog_admission_ttl_minutes
    if status == "recording":
        return settings.max_recording_duration_minutes + settings.watchdog_recording_margin_minutes
    if status == "uploading":
        return settings.watchdog_uploading_ttl_minutes
    if status == "transcribing":
        return settings.watchdog_transcribing_ttl_minutes
    raise ValueError(f"No TTL defined for status: {status}")


# "queued" (Phase C2) is here for completeness, not because it is expected to
# fire. A queued meeting has a dispatch job behind it that gives up after
# bot_dispatch_max_attempts and writes its own, far more specific failure -
# this only catches a meeting whose job was lost entirely (a Redis flush, an
# enqueue that never landed), which is exactly the invisible-forever case the
# watchdog exists for. Its TTL is deliberately the longest of the five:
# waiting for a recorder is legitimate, unlike being stuck in any of the
# others. See watchdog_queued_ttl_minutes in config.py.
_NON_TERMINAL_STATUSES = ("queued", "joining", "waiting_for_admission", "recording", "uploading", "transcribing")


def _sweep_scheduled_without_a_time(db: Session, now: datetime) -> int:
    """
    "scheduled" has no TTL, deliberately - a meeting booked for next week is
    supposed to wait, and the scheduler is its way out. But the scheduler only
    reads rows with a scheduled_at, so a "scheduled" row without one is waiting
    for nothing. Calendar bookings always carry a time; the only row that ever
    lacked one was POST /meetings' insert, a moment before it moved to its
    dispatch status - and a crash between the two left it there for good.

    Such a row was always about to be dispatched, so it gets the joining TTL.
    Rows with a scheduled_at are left entirely to the scheduler, including when
    CALENDAR_SCHEDULER_ENABLED is off.
    """
    ttl_minutes = settings.watchdog_joining_ttl_minutes
    result = db.execute(
        update(Meeting)
        .where(
            Meeting.status == "scheduled",
            Meeting.scheduled_at.is_(None),
            Meeting.updated_at < now - timedelta(minutes=ttl_minutes),
        )
        .values(
            status="failed",
            error_message=(
                f"Never started: left 'scheduled' with no scheduled time (no update for over "
                f"{ttl_minutes} minutes) - swept by watchdog"
            ),
        )
    )
    if result.rowcount:
        logger.warning(
            "[watchdog] swept %d meeting(s) stuck in 'scheduled' with no scheduled_at past the %d-minute TTL",
            result.rowcount, ttl_minutes,
        )
    return result.rowcount


def sweep_stale_meetings(db: Session) -> int:
    """
    Fails any meeting that's been sitting in a non-terminal status
    (queued/joining/recording/uploading/transcribing) with no update since
    longer than that status's TTL.

    This is the backstop for two gaps that otherwise leave a meeting
    frozen forever with nothing left to ever revisit it (see
    docs/reliability-audit.md, findings #1 and #2): meeting-bot's
    notifyBackend webhook exhausting its 3 retries with no persistence,
    and a transcription task whose exception never gets surfaced from
    inside a bare ThreadPoolExecutor Future. Swept meetings land at
    "failed", where the existing POST /meetings/{id}/retry endpoint can
    already recover them - this only exists to make sure they get there.
    """
    if not settings.watchdog_enabled:
        return 0

    now = datetime.now(timezone.utc)
    total = 0

    for status in _NON_TERMINAL_STATUSES:
        ttl_minutes = _ttl_minutes_for(status)
        cutoff = now - timedelta(minutes=ttl_minutes)
        result = db.execute(
            update(Meeting)
            .where(Meeting.status == status, Meeting.updated_at < cutoff)
            .values(
                status="failed",
                error_message=(
                    f"Timed out while '{status}' (no update for over "
                    f"{ttl_minutes} minutes) - swept by watchdog"
                ),
            )
        )
        if result.rowcount:
            logger.warning(
                "[watchdog] swept %d meeting(s) stuck in '%s' past its %d-minute TTL",
                result.rowcount, status, ttl_minutes,
            )
        total += result.rowcount

    total += _sweep_scheduled_without_a_time(db, now)

    if total:
        db.commit()

    return total
