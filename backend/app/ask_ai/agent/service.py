"""
Ask AI's chat turn: the shared agent loop (rag/agent_runner.py) run with Ask
AI's tools, instruction and storage.

ask_stream yields the per-meeting chat's events (tool, delta, reset, done,
error), plus {"type": "title", "title": ...} right before "done" on a chat's
first answered question.
"""

import asyncio
import contextlib
import logging
import uuid
from datetime import datetime
from typing import Any, Callable

from google.genai import types

from app.ask_ai import tools as ask_tools
from app.ask_ai.agent import history
from app.ask_ai.agent.instruction import GROQ_INSTRUCTION_SUFFIX, build_instruction
from app.ask_ai.agent.titles import fallback_title, generate_title
from app.ask_ai.tools import ASK_AI_TOOLS
from app.ask_ai.tools.dates import resolve_tz
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import AskAiConversation, User
from app.observability import log_context
from app.rag.agent_runner import run_agent_stream
from app.rag.session_cache import LRUSessionCache

logger = logging.getLogger(__name__)

# Its own instance, so an Ask AI chat and a meeting's chat never share
# history even for the same user.
_session_cache = LRUSessionCache(capacity=500)

# How long the finished answer waits on a title that is still being written
# before settling for the truncated question.
TITLE_WAIT_SECONDS = 10

CHAT_NOT_FOUND = "Chat not found."

_TOOL_FUNCTIONS = {
    "list_meetings": ask_tools.list_meetings,
    "find_meetings_by_topic": ask_tools.find_meetings_by_topic,
    "search_across_meetings": ask_tools.search_across_meetings,
    "get_meeting_details": ask_tools.get_meeting_details,
    "get_meeting_action_items": ask_tools.get_meeting_action_items,
}

# Gemini gets the same declarations as Groq, from schemas.py - not Python
# callables the SDK would introspect (the per-meeting chat's approach) - so
# each tool's description is written once. Declarations aren't callables, so
# the SDK couldn't run them itself even if automatic function calling were
# on; agent_runner disables it regardless.
_GEMINI_TOOLS = [
    types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=schema["function"]["name"],
            description=schema["function"]["description"],
            parameters_json_schema=schema["function"]["parameters"],
        )
        for schema in ASK_AI_TOOLS
    ])
]


def _session_parts(user_id, conversation_id) -> tuple[str, str, str]:
    return str(user_id), "ask", str(conversation_id)


def session_key(user_id, conversation_id) -> str:
    """The session cache key for a chat - LRUSessionCache.get_session's own format."""
    return ":".join(_session_parts(user_id, conversation_id))


async def evict_session(user_id, conversation_id) -> None:
    """Drops a chat's cached history - after the chat is deleted."""
    await _session_cache.evict(session_key(user_id, conversation_id))


def _tool_map(user_id: str, tz_name: str) -> dict[str, Callable[[dict], Any]]:
    """
    Each tool bound to this user and timezone, taking the model's argument
    dict. Used for both providers - Gemini's function names are the same bare
    names, since its declarations come from ASK_AI_TOOLS too. Arguments the
    schema doesn't declare are dropped, and a missing required one is a note
    rather than a TypeError.
    """
    def bind(name: str) -> Callable[[dict], Any]:
        function = _TOOL_FUNCTIONS[name]
        parameters = next(s["function"]["parameters"] for s in ASK_AI_TOOLS if s["function"]["name"] == name)
        allowed = set(parameters["properties"])
        required = parameters["required"]

        def call(args: dict) -> Any:
            kwargs = {k: v for k, v in (args or {}).items() if k in allowed}
            missing = [r for r in required if not kwargs.get(r)]
            if missing:
                return {"note": f"Missing required argument: {', '.join(missing)}."}
            return function(user_id=user_id, tz_name=tz_name, **kwargs)

        return call

    return {name: bind(name) for name in _TOOL_FUNCTIONS}


def _load_turn_context(conversation_id, user_id: str) -> tuple[AskAiConversation | None, str | None]:
    """The chat (None if it isn't this user's) and the user's custom instructions. Blocking."""
    conversation = history.get_owned_conversation(conversation_id, user_id)
    if conversation is None:
        return None, None
    db = SessionLocal()
    try:
        custom = db.query(User.ask_ai_instructions).filter(User.id == user_id).scalar()
    finally:
        db.close()
    return conversation, custom


async def ask_stream(question: str, conversation_id, user_id: str, tz_name: str | None):
    """
    Every line logged during the turn - here, in the tools' threads, in the
    Groq fallback and in the embedding calls - carries the user_id. (The log
    context has only meeting_id and user_id fields; the conversation id is
    logged once when the turn starts.)

    aclosing, as in chat_service.ask_question_stream: a client disconnecting
    mid-stream closes the inner generators right away, releasing the session
    lock, rather than whenever they are garbage-collected.
    """
    with log_context(user_id=user_id):
        async with contextlib.aclosing(_ask_stream(question, conversation_id, user_id, tz_name)) as events:
            async for event in events:
                yield event


async def _ask_stream(question: str, conversation_id, user_id: str, tz_name: str | None):
    if not user_id:
        raise ValueError("user_id must be provided to scope the agent's context.")
    try:
        conversation_id = uuid.UUID(str(conversation_id))
    except ValueError:
        yield {"type": "error", "message": CHAT_NOT_FOUND}
        return

    conversation, custom_instructions = await asyncio.to_thread(_load_turn_context, conversation_id, user_id)
    if conversation is None:
        yield {"type": "error", "message": CHAT_NOT_FOUND}
        return
    logger.info("[ask_ai] question in conversation %s", conversation_id)

    tz = resolve_tz(tz_name)
    instruction = build_instruction(datetime.now(tz), tz.key, custom_instructions)
    session_lock, session_history = await _session_cache.get_session(*_session_parts(user_id, conversation_id))
    tool_map = _tool_map(user_id, tz.key)

    # Set by the runner (in a worker thread) once the exchange is stored.
    # A title is only saved for a chat that has an answer in it.
    saved = False

    def save(q: str, answer: str, tools_used: list[str]) -> None:
        nonlocal saved
        saved = history.save_exchange(conversation_id, user_id, q, answer, tools_used)

    # The first question names the chat. The title needs only the question,
    # so it is written while the answer streams and costs no extra wait.
    title_task = (
        asyncio.create_task(generate_title(question, user_id=user_id))
        if conversation.last_message_at is None else None
    )

    try:
        async with contextlib.aclosing(run_agent_stream(
            history=session_history,
            session_lock=session_lock,
            question=question,
            instruction=instruction,
            gemini_tools=_GEMINI_TOOLS,
            gemini_tool_dispatch=tool_map,
            groq_tool_map=tool_map,
            groq_tool_schemas=ASK_AI_TOOLS,
            load_history=lambda: history.load_thread(conversation_id, user_id),
            history_turn_limit=settings.ask_ai_history_turn_limit,
            save_exchange=save,
            usage_operation="ask_ai",
            usage_user_id=user_id,
            usage_meeting_id=None,
            done_session_id=str(conversation_id),
            max_tool_iterations=settings.ask_ai_max_tool_iterations,
            logger=logger,
            log_prefix="[ask_ai]",
            groq_instruction_suffix=GROQ_INSTRUCTION_SUFFIX,
        )) as events:
            async for event in events:
                if event["type"] == "done" and title_task is not None:
                    task, title_task = title_task, None
                    if saved:
                        title = await _finish_title(task, question)
                        if await asyncio.to_thread(history.set_title, conversation_id, user_id, title):
                            yield {"type": "title", "title": title}
                    else:
                        task.cancel()
                yield event
    finally:
        # An error, a disconnect, or an answer that was never stored.
        if title_task is not None:
            title_task.cancel()


async def _finish_title(task: asyncio.Task, question: str) -> str:
    try:
        return await asyncio.wait_for(task, timeout=TITLE_WAIT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("[ask_ai] title took longer than %ss, using the question", TITLE_WAIT_SECONDS)
    except Exception as e:
        logger.warning("[ask_ai] title generation failed: %s", e)
    return fallback_title(question)
