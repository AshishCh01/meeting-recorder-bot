import json
from uuid import UUID
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Meeting
from app.api.auth import get_current_user
from app.models.meeting import ChatRequest, ChatResponse
from app.rag.chat_service import ask_question, ask_question_stream

router = APIRouter(prefix="/meetings", tags=["chat"])


def _assert_chattable(db: Session, meeting_id: UUID, user_id: str) -> Meeting:
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    if meeting.status != "completed":
        raise HTTPException(
            409, f"Meeting transcript isn't ready yet (status: {meeting.status})"
        )
    return meeting

@router.post("/{meeting_id}/chat", response_model=ChatResponse)
async def chat_with_meeting(
    meeting_id: UUID,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    _assert_chattable(db, meeting_id, user_id)
    result = await ask_question(str(meeting_id), payload.question, payload.session_id, user_id)
    return ChatResponse(**result)


@router.post("/{meeting_id}/chat/stream")
async def chat_with_meeting_stream(
    meeting_id: UUID,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    """
    Server-sent-events variant of the chat endpoint. Emits the same answer as
    POST /chat, but incrementally: `tool` events while the agent is searching,
    `delta` events as the answer is generated, then a final `done` event
    carrying session_id and tools_used. The ownership/status checks run before
    the response starts, so an unauthorised or not-ready meeting still fails as
    a normal 404/409 rather than a 200 stream containing an error.
    """
    _assert_chattable(db, meeting_id, user_id)

    async def event_source():
        try:
            async for event in ask_question_stream(
                str(meeting_id), payload.question, payload.session_id, user_id
            ):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            # The status line is already sent by this point, so a failure can
            # only be reported inside the stream, not as an HTTP error code.
            print(f"[chat] stream failed for meeting {meeting_id}: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Something went wrong generating the answer.'})}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx (the frontend's production image) from buffering the
            # stream and defeating the point of sending it incrementally.
            "X-Accel-Buffering": "no",
        },
    )
