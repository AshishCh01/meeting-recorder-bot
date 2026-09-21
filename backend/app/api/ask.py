"""
Ask AI routes: chats across all of a user's meetings.

Every route requires auth, and every conversation id is checked against the
current user. A chat that doesn't exist and one that belongs to someone else
both return 404, so a chat's existence is never revealed.

The storage calls are blocking (SQLAlchemy, their own short sessions - see
app/ask_ai/agent/history.py), so the sync routes run in FastAPI's threadpool
and the async ones go through asyncio.to_thread.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.api.chat import _release_request_session
from app.ask_ai.agent import history
from app.ask_ai.agent.service import ask_stream, evict_session
from app.ask_ai.tools.dates import resolve_tz
from app.db.database import get_db
from app.db.models import AskAiConversation
from app.models.ask import (
    AskConversationDetail, AskConversationOut, AskDeletedAllResponse, AskDeletedResponse,
    AskMessageOut, AskRenameRequest, AskRequest,
)
from app.services import rate_limit
from app.billing import quota

router = APIRouter(prefix="/ask", tags=["ask-ai"])

logger = logging.getLogger(__name__)

# Like CHAT_HISTORY_LIMIT in chat.py: the page only needs the recent tail.
MESSAGES_LIMIT = 200

NOT_FOUND = "Chat not found"


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _conversation_out(row: AskAiConversation) -> AskConversationOut:
    return AskConversationOut(id=str(row.id), title=row.title, last_message_at=_iso(row.last_message_at))


@router.post("/conversations", response_model=AskConversationOut)
def new_conversation(user_id: str = Depends(get_current_user)):
    """
    "New chat": the user's empty chat, created if there isn't one. Clicking
    it again while that chat is still empty returns the same chat, so it
    needs no rate limit - it can never make more than one row.
    """
    return _conversation_out(history.get_or_create_empty_conversation(user_id))


@router.get("/conversations", response_model=list[AskConversationOut])
def list_conversations(
    limit: int = Query(default=30, ge=1, le=100),
    before: Optional[datetime] = Query(
        default=None, description="last_message_at of the last chat on the previous page",
    ),
    user_id: str = Depends(get_current_user),
):
    """The user's chats, most recently used first. Empty chats are never listed."""
    return [_conversation_out(row) for row in history.list_conversations(user_id, limit=limit, before=before)]


@router.get("/conversations/{conversation_id}/messages", response_model=AskConversationDetail)
def get_messages(conversation_id: UUID, user_id: str = Depends(get_current_user)):
    conversation = history.get_owned_conversation(conversation_id, user_id)
    if conversation is None:
        raise HTTPException(404, NOT_FOUND)
    rows = history.load_messages(conversation_id, user_id, limit=MESSAGES_LIMIT)
    return AskConversationDetail(
        id=str(conversation.id),
        title=conversation.title,
        messages=[
            AskMessageOut(
                id=str(row.id),
                role=row.role,
                content=row.content,
                tools_used=row.tools_used or [],
                created_at=_iso(row.created_at),
            )
            for row in rows
        ],
    )


@router.patch("/conversations/{conversation_id}", response_model=AskConversationOut)
def rename_conversation(conversation_id: UUID, payload: AskRenameRequest, user_id: str = Depends(get_current_user)):
    if not history.set_title(conversation_id, user_id, payload.title):
        raise HTTPException(404, NOT_FOUND)
    conversation = history.get_owned_conversation(conversation_id, user_id)
    if conversation is None:  # deleted in between
        raise HTTPException(404, NOT_FOUND)
    return _conversation_out(conversation)


@router.delete("/conversations/{conversation_id}", response_model=AskDeletedResponse)
async def delete_conversation(conversation_id: UUID, user_id: str = Depends(get_current_user)):
    """
    Deletes the chat (its messages cascade) and drops its cached history, so
    nothing of it can reach a later answer. A stream still running for it
    finishes, but its save finds no chat and stores nothing.
    """
    if not await asyncio.to_thread(history.delete_conversation, conversation_id, user_id):
        raise HTTPException(404, NOT_FOUND)
    await evict_session(user_id, conversation_id)
    return AskDeletedResponse()


@router.delete("/conversations", response_model=AskDeletedAllResponse)
async def delete_all_conversations(user_id: str = Depends(get_current_user)):
    """Deletes every one of the user's chats, and only theirs."""
    ids = await asyncio.to_thread(history.delete_all_conversations, user_id)
    for conversation_id in ids:
        await evict_session(user_id, conversation_id)
    return AskDeletedAllResponse(count=len(ids))


@router.post("/conversations/{conversation_id}/stream")
async def ask_in_conversation(
    conversation_id: UUID,
    payload: AskRequest,
    db: Session = Depends(get_db),
    # Rate limited, and resolved before the ownership check below takes a
    # pooled connection - the same ordering, for the same reason, as
    # chat.chat_with_meeting. Its own budget: see rate_limit.ASK_AI.
    user_id: str = Depends(rate_limit.limited(rate_limit.ASK_AI)),
):
    """
    Server-sent events: `tool` while a tool runs, `delta` as the answer
    streams, `reset` when the answer starts over, `title` once on a chat's
    first answered question, then `done` - or `error`. The same shapes as
    the per-meeting stream, plus `title`.

    The ownership check runs before the response starts, so a chat the user
    can't see is a real 404 rather than a 200 stream carrying an error.
    """
    owned = (
        db.query(AskAiConversation.id)
        .filter(AskAiConversation.id == conversation_id, AskAiConversation.user_id == user_id)
        .first()
    )
    if owned is None:
        raise HTTPException(404, NOT_FOUND)
    # Billing Phase 2, and it has to sit exactly here: after the ownership
    # check (a chat you cannot see is a 404, not a bill) and before the
    # session is released, because it queries. A 402 raised now is a real
    # status code; raised inside event_source it would be a 200 stream
    # carrying an error the upgrade prompt could not branch on.
    quota.enforce_ai_question_quota(db, user_id)
    # In the route body, not in event_source: see chat._release_request_session.
    _release_request_session(db)
    tz_name = resolve_tz(payload.timezone).key

    async def event_source():
        try:
            async for event in ask_stream(payload.question, conversation_id, user_id, tz_name):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            # The status line is already sent, so a failure can only be
            # reported inside the stream.
            logger.exception("[ask_ai] stream failed: %s", e, extra={"user_id": user_id})
            yield f"data: {json.dumps({'type': 'error', 'message': 'Something went wrong generating the answer.'})}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops nginx (the frontend's production image) from buffering the stream.
            "X-Accel-Buffering": "no",
        },
    )
