"""
Ask AI tools that read meetings directly: list_meetings, get_meeting_details
and get_meeting_action_items. Each opens one short session and makes no
network call.
"""

from app.ask_ai.tools.common import NOT_FOUND, clip, owned_completed, parse_meeting_id, title_of
from app.ask_ai.tools.dates import (
    InvalidDate, describe_range, format_local, local_range_to_utc, meeting_date_expr, resolve_tz,
)
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting

DETAILED = "detailed"
COMPACT = "compact"
TITLES = "titles"

SUMMARY_CHARS = 300
KEY_POINTS_IN_COMPACT = 3
KEY_POINT_CHARS = 80
TITLE_QUERY_CHARS = 200


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_meetings(
    *,
    user_id: str,
    tz_name: str | None,
    start_date: str | None = None,
    end_date: str | None = None,
    title_query: str | None = None,
) -> dict:
    """
    The user's completed meetings, newest first, optionally within a local
    date range and/or with a title containing `title_query`.

    How much comes back per meeting depends on how many match (settings
    ask_ai_*_limit): a summary each for a handful, the first key points for
    a month's worth, titles only beyond that, and past ask_ai_titles_limit
    the most recent ones with truncated=true. Every mode includes id, title
    and date.
    """
    tz = resolve_tz(tz_name)
    try:
        date_range = local_range_to_utc(start_date, end_date, tz)
    except InvalidDate as e:
        return {"note": str(e)}
    title_query = (title_query or "").strip()[:TITLE_QUERY_CHARS]

    meeting_date = meeting_date_expr()
    db = SessionLocal()
    try:
        query = owned_completed(db.query(Meeting), user_id)
        if date_range:
            query = query.filter(meeting_date >= date_range[0], meeting_date < date_range[1])
        if title_query:
            query = query.filter(Meeting.title.ilike(f"%{_escape_like(title_query)}%", escape="\\"))

        total_count = query.with_entities(Meeting.id).count()
        if total_count == 0:
            return {
                "mode": None,
                "total_count": 0,
                "returned": 0,
                "truncated": False,
                "meetings": [],
                "note": _empty_note(start_date, end_date, title_query),
            }

        if total_count <= settings.ask_ai_detailed_limit:
            mode = DETAILED
        elif total_count <= settings.ask_ai_compact_limit:
            mode = COMPACT
        else:
            mode = TITLES

        columns = [Meeting.id, Meeting.title, meeting_date.label("meeting_date")]
        if mode == DETAILED:
            columns += [
                Meeting.platform,
                Meeting.duration_seconds,
                # Single JSON fields rather than the whole transcript, which
                # also carries the full conversation.
                Meeting.transcript["summary"].astext.label("summary"),
            ]
        elif mode == COMPACT:
            columns.append(Meeting.transcript["key_points"].label("key_points"))

        rows = (
            query.with_entities(*columns)
            .order_by(meeting_date.desc(), Meeting.id.desc())
            .limit(settings.ask_ai_titles_limit)
            .all()
        )
    finally:
        db.close()

    meetings = [_meeting_entry(row, mode, tz) for row in rows]
    truncated = total_count > len(meetings)
    result = {
        "mode": mode,
        "total_count": total_count,
        "returned": len(meetings),
        "truncated": truncated,
        "meetings": meetings,
    }
    if truncated:
        result["note"] = (
            f"Showing the most recent {len(meetings)} of {total_count} meetings. "
            "Ask the user to narrow the date range to see the rest."
        )
    return result


def _meeting_entry(row, mode: str, tz) -> dict:
    entry = {"id": str(row.id), "title": title_of(row.title), "date": format_local(row.meeting_date, tz)}
    if mode == DETAILED:
        entry["platform"] = row.platform
        entry["duration_minutes"] = round(row.duration_seconds / 60) if row.duration_seconds else None
        entry["summary"] = clip(row.summary, SUMMARY_CHARS)
    elif mode == COMPACT:
        key_points = row.key_points if isinstance(row.key_points, list) else []
        entry["key_points"] = [
            clip(point, KEY_POINT_CHARS) for point in key_points if isinstance(point, str) and point.strip()
        ][:KEY_POINTS_IN_COMPACT]
    return entry


def _empty_note(start_date, end_date, title_query: str) -> str:
    """Names what was searched, so the model can tell the user exactly which range came up empty."""
    note = "No completed meetings found"
    if start_date or end_date:
        described = describe_range(start_date, end_date)
        if " to " in described:
            note += " between " + described.replace(" to ", " and ")
        else:
            note += " on " + described
    if title_query:
        note += f" with a title containing {title_query!r}"
    return note + "."


def get_meeting_details(*, user_id: str, tz_name: str | None, meeting_id: str) -> dict:
    """One meeting's summary, key points and conclusion, with its title, date, platform and duration."""
    parsed = parse_meeting_id(meeting_id)
    if parsed is None:
        return NOT_FOUND

    db = SessionLocal()
    try:
        row = (
            owned_completed(db.query(Meeting), user_id)
            .filter(Meeting.id == parsed)
            .with_entities(
                Meeting.id, Meeting.title, meeting_date_expr().label("meeting_date"),
                Meeting.platform, Meeting.duration_seconds,
                Meeting.transcript["summary"].astext.label("summary"),
                Meeting.transcript["key_points"].label("key_points"),
                Meeting.transcript["conclusion"].astext.label("conclusion"),
            )
            .first()
        )
    finally:
        db.close()
    if row is None:
        return NOT_FOUND

    return {
        "id": str(row.id),
        "title": title_of(row.title),
        "date": format_local(row.meeting_date, resolve_tz(tz_name)),
        "platform": row.platform,
        "duration_minutes": round(row.duration_seconds / 60) if row.duration_seconds else None,
        "summary": row.summary,
        "key_points": row.key_points if isinstance(row.key_points, list) else [],
        "conclusion": row.conclusion,
    }


def get_meeting_action_items(*, user_id: str, tz_name: str | None, meeting_id: str) -> dict:
    """One meeting's action items (task, owner, due date, timestamp), with its title and date."""
    parsed = parse_meeting_id(meeting_id)
    if parsed is None:
        return NOT_FOUND

    db = SessionLocal()
    try:
        row = (
            owned_completed(db.query(Meeting), user_id)
            .filter(Meeting.id == parsed)
            .with_entities(
                Meeting.id, Meeting.title, meeting_date_expr().label("meeting_date"),
                Meeting.transcript["action_items"].label("action_items"),
            )
            .first()
        )
    finally:
        db.close()
    if row is None:
        return NOT_FOUND

    action_items = row.action_items if isinstance(row.action_items, list) else []
    result = {
        "id": str(row.id),
        "title": title_of(row.title),
        "date": format_local(row.meeting_date, resolve_tz(tz_name)),
        "action_items": action_items,
    }
    if not action_items:
        result["note"] = "No action items were recorded for this meeting."
    return result
