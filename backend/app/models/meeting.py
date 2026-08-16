from pydantic import BaseModel
from typing import Optional, Any, Dict


class MeetingCreate(BaseModel):
    meeting_url: str


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
    question: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    session_id: str
    tools_used: list[str] = []


class RecordingCompleteWebhook(BaseModel):
    user_id: str
    meeting_id: str
    status: str  # "completed" | "failed"
    recording_path: Optional[str] = None  # path inside the Supabase bucket
    duration_seconds: Optional[int] = None
    error_message: Optional[str] = None