import json
from uuid import UUID
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import ChatMessage, Meeting
from app.api.auth import get_current_user
from app.models.meeting import ChatHistoryResponse, ChatMessageOut, ChatRequest, ChatResponse
from app.rag.chat_service import ask_question, ask_question_stream

router = APIRouter(prefix="/meetings", tags=["chat"])

# A thread is append-only and unbounded, but the panel only ever needs
# the recent tail - and returning all of it would make the endpoint get
# slower the more a meeting is used.
CHAT_HISTORY_LIMIT = 200


def _assert_chattable(db: Session, meeting_id: UUID, user_id: str) -> Meeting:
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    if meeting.status != "completed":
        raise HTTPException(
            409, f"Meeting transcript isn't ready yet (status: {meeting.status})"
        )
    return meeting


def _release_request_session(db: Session) -> None:
    """
    Returns the request's pooled connection before any model work starts.

    The checks above leave a transaction open, and SQLAlchemy keeps its
    connection checked out until that transaction ends - through the whole
    answer, and for the streaming route until the last chunk, since FastAPI
    runs get_db's cleanup only after the response finishes. With the backend's
    pool at 8 (db_pool_size), eight concurrent chats would take all of it and
    every other request would 503.

    It is this session, not a separate one for the checks, because
    get_current_user shares it: on a user-row cache miss, _ensure_user_row's
    SELECT checks out the same connection and does not commit.

    chat_service opens its own short sessions for history, tools and saving,
    so nothing after this needs the request's. The Meeting returned by
    _assert_chattable is detached by this - don't read attributes off it
    afterwards. get_db's own close() is then a no-op.
    """
    db.close()


@router.get("/{meeting_id}/chat", response_model=ChatHistoryResponse)
def get_chat_history(
    meeting_id: UUID,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    """
    Returns the meeting's stored Q&A thread, oldest first, so the chat
    panel shows the same conversation after a refresh, a logout or a
    backend restart instead of starting over every time.

    Gated exactly like posting a question, so a meeting you cannot chat
    with is a meeting whose thread you cannot read either.
    """
    _assert_chattable(db, meeting_id, user_id)

    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.meeting_id == meeting_id, ChatMessage.user_id == user_id)
        .order_by(ChatMessage.id.desc())
        .limit(CHAT_HISTORY_LIMIT)
        .all()
    )
    # Queried newest-first so the LIMIT keeps the most recent turns;
    # the panel renders them oldest-first.
    rows.reverse()

    return ChatHistoryResponse(
        messages=[
            ChatMessageOut(
                id=str(row.id),
                role=row.role,
                content=row.content,
                tools_used=row.tools_used or [],
                created_at=row.created_at.isoformat() if row.created_at else None,
            )
            for row in rows
        ]
    )


@router.post("/{meeting_id}/chat", response_model=ChatResponse)
async def chat_with_meeting(
    meeting_id: UUID,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    _assert_chattable(db, meeting_id, user_id)
    _release_request_session(db)
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
    # Here in the route body, not in event_source: the generator only runs
    # after this returns, and the checks above must still fail as a real
    # 404/409 rather than inside a 200 stream.
    _release_request_session(db)

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
