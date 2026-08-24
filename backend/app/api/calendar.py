import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_db
from app.db.models import CalendarConnection, Meeting
from app.api.auth import get_current_user
from app.api.meetings import meeting_to_dict
from app.models.calendar import CalendarConnectResponse, CalendarStatus, CalendarEventOut
from app.services import google_oauth_service as oauth
from app.services import calendar_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("/connect", response_model=CalendarConnectResponse)
def connect(user_id: str = Depends(get_current_user)):
    try:
        auth_url = oauth.build_auth_url(user_id)
    except oauth.GoogleCalendarNotConfigured as e:
        raise HTTPException(503, str(e))
    return CalendarConnectResponse(auth_url=auth_url)


@router.get("/oauth/callback")
def oauth_callback(code: str, state: str, db: Session = Depends(get_db)):
    # Hit directly by Google's redirect - the browser has no Supabase
    # Authorization header here, so the signed `state` param (see
    # google_oauth_service.sign_state) is what identifies the user.
    try:
        user_id = oauth.verify_state(state)
    except oauth.OAuthStateInvalid as e:
        raise HTTPException(400, str(e))
    except oauth.GoogleCalendarNotConfigured as e:
        raise HTTPException(503, str(e))

    try:
        tokens = oauth.exchange_code_for_tokens(code)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Failed to complete Google Calendar connection: {e}")

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        # Google only issues a refresh_token on first consent, or when
        # prompt=consent forces re-consent (which build_auth_url always
        # passes) - if it's still missing, something upstream is wrong
        # rather than this being a normal case to silently swallow.
        raise HTTPException(502, "Google did not return a refresh token. Try disconnecting and reconnecting.")

    try:
        email = oauth.fetch_google_email(tokens["access_token"])
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Failed to complete Google Calendar connection: {e}")

    encrypted = oauth.encrypt_token(refresh_token)

    connection = db.query(CalendarConnection).filter(CalendarConnection.user_id == user_id).first()
    if connection:
        connection.google_email = email
        connection.refresh_token_encrypted = encrypted
    else:
        connection = CalendarConnection(user_id=user_id, google_email=email, refresh_token_encrypted=encrypted)
        db.add(connection)
    db.commit()

    return RedirectResponse(f"{settings.frontend_origin}/settings?calendar=connected")


@router.get("/status", response_model=CalendarStatus)
def status(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    connection = db.query(CalendarConnection).filter(CalendarConnection.user_id == user_id).first()
    if not connection:
        return CalendarStatus(connected=False)
    return CalendarStatus(connected=True, google_email=connection.google_email)


@router.delete("/disconnect")
def disconnect(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    db.query(CalendarConnection).filter(CalendarConnection.user_id == user_id).delete()
    db.commit()
    return {"status": "disconnected"}


def _access_token_or_error(user_id: str, db: Session) -> str:
    try:
        return calendar_service.get_valid_access_token(user_id, db)
    except calendar_service.CalendarNotConnected as e:
        raise HTTPException(409, str(e))
    except oauth.RevokedAccessError as e:
        raise HTTPException(409, str(e))
    except oauth.GoogleCalendarNotConfigured as e:
        raise HTTPException(503, str(e))


@router.get("/events", response_model=list[CalendarEventOut])
def list_events(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    access_token = _access_token_or_error(user_id, db)

    try:
        raw_events = calendar_service.list_upcoming_events(access_token, settings.calendar_lookahead_days)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Could not fetch calendar events: {e}")

    already_scheduled = {
        m.calendar_event_id: m
        for m in db.query(Meeting).filter(
            Meeting.user_id == user_id, Meeting.calendar_event_id.isnot(None)
        ).all()
    }

    results = []
    for event in raw_events:
        event_id = event.get("id")
        if not event_id:
            continue
        detected = calendar_service.extract_meeting_url(event)
        platform, meeting_url = detected if detected else (None, None)
        scheduled_meeting = already_scheduled.get(event_id)
        results.append(CalendarEventOut(
            id=event_id,
            title=event.get("summary") or "(untitled event)",
            starts_at=calendar_service.event_time_str(event.get("start", {})),
            ends_at=calendar_service.event_time_str(event.get("end", {})),
            meeting_url=meeting_url,
            platform=platform,
            already_scheduled=scheduled_meeting is not None,
            meeting_id=str(scheduled_meeting.id) if scheduled_meeting else None,
        ))
    return results


@router.post("/events/{event_id}/schedule")
def schedule_event(
    event_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    access_token = _access_token_or_error(user_id, db)

    try:
        event = calendar_service.get_event(access_token, event_id)
    except calendar_service.CalendarTransientError as e:
        raise HTTPException(502, f"Could not fetch this event from Google Calendar: {e}")
    except oauth.RevokedAccessError as e:
        raise HTTPException(409, str(e))

    if event is None:
        raise HTTPException(404, "This calendar event no longer exists.")

    # Never trust a client-supplied URL/time for what gets scheduled -
    # always re-derive both from the event Google just returned.
    detected = calendar_service.extract_meeting_url(event)
    if not detected:
        raise HTTPException(400, "No Google Meet or Zoom link was found on this event.")
    platform, meeting_url = detected

    scheduled_at = calendar_service.parse_event_datetime(event.get("start", {}))
    if scheduled_at is None:
        raise HTTPException(400, "All-day events don't have a single start time to join at.")

    existing = db.query(Meeting).filter(
        Meeting.user_id == user_id, Meeting.calendar_event_id == event_id
    ).first()
    if existing:
        # Idempotent - a double-click or a retried request lands on
        # the same already-scheduled meeting rather than erroring.
        return meeting_to_dict(existing)

    meeting = Meeting(
        user_id=user_id,
        meeting_url=meeting_url,
        platform=platform,
        status="scheduled",
        scheduled_at=scheduled_at,
        calendar_event_id=event_id,
        title=event.get("summary"),
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return meeting_to_dict(meeting)
