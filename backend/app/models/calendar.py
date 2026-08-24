from pydantic import BaseModel
from typing import Optional


class CalendarConnectResponse(BaseModel):
    auth_url: str


class CalendarStatus(BaseModel):
    connected: bool
    google_email: Optional[str] = None


class CalendarEventOut(BaseModel):
    id: str
    title: str
    # Raw Google event start/end - an ISO datetime string for a timed
    # event, a "YYYY-MM-DD" date string for an all-day event, or None
    # if Google omitted it. Only timed events can be scheduled (see
    # POST /calendar/events/{event_id}/schedule).
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    meeting_url: Optional[str] = None
    platform: Optional[str] = None
    already_scheduled: bool = False
    meeting_id: Optional[str] = None
