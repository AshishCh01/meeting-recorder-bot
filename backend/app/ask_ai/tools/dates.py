"""
Dates for the Ask AI tools.

A meeting's date is coalesce(scheduled_at, created_at): the scheduled time for
a calendar-scheduled meeting, the creation time for an ad-hoc one. Build it
with meeting_date_expr() and nowhere else - ix_meetings_user_meeting_date is
an index on exactly that expression, and Postgres only uses an expression
index for a query that repeats the expression exactly.

The model sends dates as YYYY-MM-DD in the user's local calendar, and
local_range_to_utc turns them into the UTC instants the column is compared
against, so a meeting at 23:30 in Kolkata lands on the day the user lived it,
not on the UTC day.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func

from app.db.models import Meeting


class InvalidDate(ValueError):
    """A date the model sent that can't be used; str() is the note for the model."""


def meeting_date_expr():
    return func.coalesce(Meeting.scheduled_at, Meeting.created_at)


def resolve_tz(tz_name: str | None) -> ZoneInfo:
    """
    The user's timezone, or UTC if it is missing or not a real IANA name.
    The API validates it before it gets here; this only keeps a tool from
    failing on one that slipped through.
    """
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo("UTC")


def _parse(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except (AttributeError, ValueError):
        raise InvalidDate(
            f"{name} {value!r} is not a valid date. Use YYYY-MM-DD, e.g. 2026-09-15."
        ) from None


def local_range_to_utc(start_date: str | None, end_date: str | None, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    """
    [start 00:00 local, (end + 1 day) 00:00 local) as UTC datetimes - end is
    inclusive of its whole day. None when neither date is given.

    A lone end_date is treated like a lone start_date: that one day. Swapped
    dates are reordered rather than rejected. Raises InvalidDate for anything
    that doesn't parse.
    """
    if not start_date and not end_date:
        return None
    start = _parse(start_date, "start_date") if start_date else None
    end = _parse(end_date, "end_date") if end_date else None
    start = start or end
    end = end or start
    if end < start:
        start, end = end, start

    start_local = datetime.combine(start, time.min, tzinfo=tz)
    end_local = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def describe_range(start_date: str | None, end_date: str | None) -> str:
    """
    The range as the user would say it, for notes: "15 Sep 2026" or
    "1 Aug 2026 to 31 Aug 2026". Only call it after local_range_to_utc has
    accepted the same dates.
    """
    start = date.fromisoformat((start_date or end_date).strip())
    end = date.fromisoformat((end_date or start_date).strip())
    if end < start:
        start, end = end, start
    if start == end:
        return _day(start)
    return f"{_day(start)} to {_day(end)}"


def _day(d: date) -> str:
    # %-d isn't portable (Windows), so the day number is formatted by hand.
    return f"{d.day} {d:%b %Y}"


def format_local(dt: datetime | None, tz: ZoneInfo) -> str | None:
    """e.g. "Mon 15 Sep 2026, 14:30", in the user's timezone."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(tz)
    return f"{local:%a} {local.day} {local:%b %Y, %H:%M}"
