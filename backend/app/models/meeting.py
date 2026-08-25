from pydantic import BaseModel, HttpUrl, Field
from typing import Optional, Any, Dict


class MeetingCreate(BaseModel):
    # HttpUrl only confirms it's a well-formed http(s) URL - the actual
    # platform/host allowlist enforcement happens in detect_platform().
    meeting_url: HttpUrl = Field(max_length=2048)


class Meeting(BaseModel):
    id: str
    meeting_url: str
    platform: str
    status: str
    recording_url: Optional[str] = None
    duration_seconds: Optional[int] = None
    error_message: Optional[str] = None
    transcript: Optional[Dict[str, Any]] = None


class ChatRequest(BaseModel):
    # max_length bounds cost/abuse against the Gemini agent - a request
    # with no cap here could forward an arbitrarily large prompt straight
    # through to ask_question().
    question: str = Field(min_length=1, max_length=4000)
    session_id: Optional[str] = Field(default=None, max_length=128)


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    tools_used: list[str] = []


class RecordingCompleteWebhook(BaseModel):
    user_id: str
    meeting_id: str
    status: str  # "completed" | "failed" | "waiting_for_admission"
    recording_path: Optional[str] = None  # path inside the Supabase bucket
    duration_seconds: Optional[int] = None
    error_message: Optional[str] = None


class UserSettingsUpdate(BaseModel):
    bot_display_name: str = Field(min_length=1, max_length=50)


class UserSettings(BaseModel):
    id: str
    email: str
    bot_display_name: str