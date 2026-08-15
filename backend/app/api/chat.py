from fastapi import APIRouter, HTTPException

from app.db.supabase import supabase
from app.models.meeting import ChatRequest, ChatResponse
from app.rag.chat_service import ask_question

router = APIRouter(prefix="/meetings", tags=["chat"])


@router.post("/{meeting_id}/chat", response_model=ChatResponse)
async def chat_with_meeting(meeting_id: str, payload: ChatRequest):
    meeting = (
        supabase.table("meetings")
        .select("id, status")
        .eq("id", meeting_id)
        .single()
        .execute()
        .data
    )
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    if meeting["status"] != "completed":
        raise HTTPException(
            409, f"Meeting transcript isn't ready yet (status: {meeting['status']})"
        )

    result = await ask_question(meeting_id, payload.question, payload.session_id)
    return ChatResponse(**result)
