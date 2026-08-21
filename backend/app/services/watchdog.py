import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Meeting

logger = logging.getLogger(__name__)


def _ttl_minutes_for(status: str) -> int:
    if status == "joining":
        return settings.watchdog_joining_ttl_minutes
    if status == "recording":
        return settings.max_recording_duration_minutes + settings.watchdog_recording_margin_minutes
    if status == "uploading":
        return settings.watchdog_uploading_ttl_minutes
    if status == "transcribing":
        return settings.watchdog_transcribing_ttl_minutes
    raise ValueError(f"No TTL defined for status: {status}")


_NON_TERMINAL_STATUSES = ("joining", "recording", "uploading", "transcribing")


def sweep_stale_meetings(db: Session) -> int:
    """
    Fails any meeting that's been sitting in a non-terminal status
    (joining/recording/uploading/transcribing) with no update since
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

    if total:
        db.commit()

    return total
