from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Meeting
from app.api.auth import get_current_user
from app.models.meeting import ChatRequest, ChatResponse
from app.rag.chat_service import ask_question

router = APIRouter(prefix="/meetings", tags=["chat"])

@router.post("/{meeting_id}/chat", response_model=ChatResponse)
async def chat_with_meeting(
    meeting_id: str,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    if meeting.status != "completed":
        raise HTTPException(
            409, f"Meeting transcript isn't ready yet (status: {meeting.status})"
        )

    result = await ask_question(meeting_id, payload.question, payload.session_id, user_id)
    return ChatResponse(**result)
