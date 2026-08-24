import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.db.models import CalendarConnection
from app.services import google_oauth_service as oauth
from app.services.platform_detector import detect_platform

GOOGLE_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

_URL_RE = re.compile(r'https?://[^\s<>"]+')


class CalendarNotConnected(Exception):
    """This user has no calendar_connections row."""


class CalendarTransientError(Exception):
    """Network/timeout/5xx talking to Google - not necessarily fatal, caller decides how to handle it."""


def get_valid_access_token(user_id: str, db: Session) -> str:
    """Exchanges the stored refresh token for a fresh, short-lived
    access token. Never persists the access token - it's derived on
    demand every time. Raises CalendarNotConnected if the user hasn't
    connected a calendar, or oauth.RevokedAccessError if Google has
    rejected the stored refresh token."""
    connection = db.query(CalendarConnection).filter(CalendarConnection.user_id == user_id).first()
    if not connection:
        raise CalendarNotConnected("Google Calendar is not connected.")
    refresh_token = oauth.decrypt_token(connection.refresh_token_encrypted)
    return oauth.refresh_access_token(refresh_token)


def extract_meeting_url(event: dict) -> Optional[tuple]:
    """Looks for a Google Meet/Zoom link on a calendar event, checking
    the structured fields Google itself populates before falling back
    to scanning free text. Returns (platform, url) for the first
    candidate that resolves through the existing detect_platform()
    allowlist, or None if nothing matches - reusing that allowlist
    means Teams links (and anything else not supported end to end)
    are excluded exactly like a manually-submitted URL would be."""
    candidates = []

    if event.get("hangoutLink"):
        candidates.append(event["hangoutLink"])

    for entry_point in event.get("conferenceData", {}).get("entryPoints", []):
        if entry_point.get("entryPointType") == "video" and entry_point.get("uri"):
            candidates.append(entry_point["uri"])

    if event.get("location"):
        candidates.extend(_URL_RE.findall(event["location"]))

    if event.get("description"):
        candidates.extend(_URL_RE.findall(event["description"]))

    for candidate in candidates:
        try:
            platform = detect_platform(candidate)
            return platform, candidate
        except ValueError:
            continue
    return None


def event_time_str(time_obj: dict) -> Optional[str]:
    """Raw display string for a Google event start/end object - an
    ISO datetime for a timed event, a "YYYY-MM-DD" date for an
    all-day one, or None if absent."""
    return time_obj.get("dateTime") or time_obj.get("date")


def parse_event_datetime(time_obj: dict) -> Optional[datetime]:
    """A timezone-aware datetime for a timed event, or None for an
    all-day event ("date" only, no "dateTime") - an all-day event has
    no single instant for the bot to join at, so callers that need to
    schedule a join should treat None here as "can't be scheduled"."""
    value = time_obj.get("dateTime")
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def list_upcoming_events(access_token: str, days_ahead: int) -> list:
    now = datetime.now(timezone.utc)
    response = httpx.get(
        GOOGLE_EVENTS_URL,
        params={
            "timeMin": now.isoformat(),
            "timeMax": (now + timedelta(days=days_ahead)).isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "50",
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("items", [])


def get_event(access_token: str, event_id: str) -> Optional[dict]:
    """Fetches a single event by id. Returns None if it was deleted or
    cancelled (Google represents a cancelled-but-still-listed event as
    a body with status == "cancelled", not always a 404). Raises
    oauth.RevokedAccessError for 401/403 and CalendarTransientError for
    anything else that isn't a clean 200, so callers - notably the
    scheduler's pre-join revalidation - can tell "this meeting is
    genuinely gone" apart from "we couldn't check right now"."""
    try:
        response = httpx.get(
            f"{GOOGLE_EVENTS_URL}/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
    except httpx.RequestError as e:
        raise CalendarTransientError(str(e)) from e

    if response.status_code == 404:
        return None
    if response.status_code in (401, 403):
        raise oauth.RevokedAccessError("Google Calendar access was revoked - please reconnect your calendar.")
    if response.status_code >= 400:
        raise CalendarTransientError(f"Google Calendar API returned {response.status_code}: {response.text}")

    event = response.json()
    if event.get("status") == "cancelled":
        return None
    return event
