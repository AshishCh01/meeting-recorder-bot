import contextlib
import logging
from google.genai import types

from app.db.database import SessionLocal
from app.db.models import ChatMessage
from app.rag.tools import get_meeting_summary, get_action_items, search_by_speaker, search_transcript
from app.rag.chat_fallback_groq import GROQ_TOOLS
# `client` is re-exported: tests patch chat_service.client.aio.models, which
# is the same object agent_runner calls.
from app.rag.agent_runner import client, run_agent_stream  # noqa: F401
from app.rag.session_cache import LRUSessionCache
from app.observability import log_context

logger = logging.getLogger(__name__)

INSTRUCTION = """
You are an advanced Agentic RAG assistant dedicated to answering questions about ONE specific
meeting. Your tools automatically scope searches to the authenticated user's current meeting context.

You have four specialized tools at your disposal:
1. `get_meeting_summary`: Returns the overall meeting summary, key points, and conclusion. Use this for broad topic inquiries.
2. `get_action_items`: Returns a structured list of tasks and decisions. Use this specifically when the user asks about action items or follow-ups.
3. `search_by_speaker`: Retrieves transcript passages spoken by a specific individual. Use this when asked what a specific person said.
4. `search_transcript`: Performs a semantic vector search over the entire transcript. Use this for specific topics, details, or quotes.

Rules:
- ALWAYS evaluate the user's question and select the most appropriate tool.
- You MUST ground your answer entirely in the data returned by your tools. NEVER hallucinate or invent meeting content.
- If a tool returns no results, state clearly that the information is not available in the transcript.
- When `search_transcript` or `search_by_speaker` provide useful passages, cite the `timestamp_start` and `speaker` in your answer so the user can easily find the moment in the recording.
- Keep your answers concise, well-structured, and directly responsive to the user's inquiry.
"""

# Global session cache instance
_session_cache = LRUSessionCache(capacity=500)

# How many stored turns are replayed into a cold session, and the cap the
# warm in-memory history is truncated to. One constant for both, so a
# session rebuilt from the database sees exactly as much context as one
# that stayed in the cache.
HISTORY_TURN_LIMIT = 20

# Gemini turns (each one a model call, possibly followed by tool calls)
# a single question may take before the canned "rephrase" message; the
# Groq fallback gets the same budget.
MAX_TOOL_ITERATIONS = 6


def _load_thread(meeting_id: str, user_id: str) -> list[types.Content]:
    """
    Rebuilds a session's history from the meeting's stored thread.

    Blocking (SQLAlchemy) - call it through asyncio.to_thread. Only text
    turns come back; see the ChatMessage docstring for why tool round-trips
    are not stored.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(ChatMessage)
            .filter(ChatMessage.meeting_id == meeting_id, ChatMessage.user_id == user_id)
            .order_by(ChatMessage.id.desc())
            .limit(HISTORY_TURN_LIMIT)
            .all()
        )
    finally:
        db.close()

    # Queried newest-first so the LIMIT keeps the most recent turns; the
    # model needs them oldest-first.
    rows.reverse()
    return [
        types.Content(
            role="model" if row.role == "assistant" else "user",
            parts=[types.Part.from_text(text=row.content)],
        )
        for row in rows
    ]


def _save_exchange(meeting_id: str, user_id: str, question: str, answer: str, tools_used: list[str]) -> None:
    """
    Stores one completed question/answer pair.

    Blocking (SQLAlchemy) - call it through asyncio.to_thread. Both rows go
    in a single commit, so a thread can never be read back holding a
    question with no answer under it.
    """
    db = SessionLocal()
    try:
        db.add(ChatMessage(
            meeting_id=meeting_id,
            user_id=user_id,
            role="user",
            content=question,
        ))
        db.add(ChatMessage(
            meeting_id=meeting_id,
            user_id=user_id,
            role="assistant",
            content=answer,
            tools_used=tools_used or None,
        ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

class DummyToolContext:
    def __init__(self, meeting_id: str, user_id: str):
        self.state = {"meeting_id": meeting_id, "user_id": user_id}

def _session_id(meeting_id: str, session_id: str | None) -> str:
    return session_id or f"meeting-{meeting_id}"

async def ask_question_stream(meeting_id: str, question: str, session_id: str | None = None, user_id: str = None):
    """
    Every line logged during this turn - here, in the tools' threads (which
    asyncio.to_thread runs with a copy of this context), in the Groq fallback
    and in the embedding call a search makes - carries meeting_id and user_id
    (Phase B5). The fields are bound for as long as the turn runs and put back
    when it ends.

    aclosing, so that when a caller stops early (a client disconnecting
    mid-stream) the inner generator is closed right then - releasing the
    session lock it holds - rather than whenever it is garbage-collected.
    """
    with log_context(meeting_id=meeting_id, user_id=user_id):
        async with contextlib.aclosing(_ask_question_stream(meeting_id, question, session_id, user_id)) as events:
            async for event in events:
                yield event


async def _ask_question_stream(meeting_id: str, question: str, session_id: str | None = None, user_id: str = None):
    """
    The per-meeting chat: this meeting's four tools, INSTRUCTION, and its
    stored thread, run through the shared agent loop in agent_runner.py.
    See run_agent_stream for the events it yields.
    """
    if not user_id:
        raise ValueError("user_id must be provided to scope the agent's context.")

    sid = _session_id(meeting_id, session_id)
    session_lock, history = await _session_cache.get_session(user_id, meeting_id, sid)

    # Define tools without ToolContext for Gemini
    def _get_meeting_summary() -> dict:
        """
        Returns the high-level summary of the meeting, key points discussed, 
        and the final conclusion/resolution.

        Use this FIRST for broad questions like "what was this meeting about",
        "what were the main topics", or "how did it conclude".
        """
        return get_meeting_summary(DummyToolContext(meeting_id, user_id))

    def _get_action_items() -> list[dict]:
        """
        Returns the concrete tasks, decisions, or follow-ups mentioned in the meeting,
        including the owner and timestamp if available.

        Use this specifically when asked about action items, tasks, or follow-ups.
        """
        return get_action_items(DummyToolContext(meeting_id, user_id))

    def _search_by_speaker(speaker_name: str) -> dict:
        """
        Searches the transcript for everything said by a specific person.

        Use this when the user asks "what did Alice say", "find quotes by Bob",
        or "did John mention anything?".
        
        Args:
            speaker_name: The name of the speaker to search for.
        """
        return search_by_speaker(speaker_name, DummyToolContext(meeting_id, user_id))

    def _search_transcript(query: str) -> dict:
        """
        Semantically searches this meeting's full transcript for passages
        relevant to `query`, and returns the matching passages with their
        speakers and MM:SS timestamps.

        Use this for specific questions the summary can't answer: exact wording,
        or anything tied to a specific moment or topic in the conversation.
        Call it more than once with reworded queries if the first results don't
        fully answer the question.

        Args:
            query: A focused natural-language description of what to find,
                e.g. "budget concerns raised about the Q3 launch".
        """
        return search_transcript(query, DummyToolContext(meeting_id, user_id))
        
    available_tools = [_get_meeting_summary, _get_action_items, _search_by_speaker, _search_transcript]

    # How the runner calls each tool when Gemini asks for it: keyed by the
    # function name Gemini reports (the closure's own name, leading
    # underscore included) and taking the call's argument dict.
    gemini_tool_dispatch = {
        "_get_meeting_summary": lambda args: _get_meeting_summary(),
        "_get_action_items": lambda args: _get_action_items(),
        "_search_by_speaker": lambda args: _search_by_speaker(args.get("speaker_name", "")),
        "_search_transcript": lambda args: _search_transcript(args.get("query", "")),
    }

    # The same four tools for the Groq fallback. Keyed by their bare names
    # (the schemas in chat_fallback_groq.py declare them without the
    # leading underscore these closures carry) and taking a decoded
    # argument dict, because Groq hands back tool arguments as a JSON
    # object rather than as kwargs the way the google-genai SDK does.
    groq_tool_map = {
        "get_meeting_summary": lambda args: _get_meeting_summary(),
        "get_action_items": lambda args: _get_action_items(),
        "search_by_speaker": lambda args: _search_by_speaker(args.get("speaker_name", "")),
        "search_transcript": lambda args: _search_transcript(args.get("query", "")),
    }

    def _save(question: str, answer: str, tools_used: list[str]) -> None:
        _save_exchange(meeting_id, user_id, question, answer, tools_used)

    async with contextlib.aclosing(run_agent_stream(
        history=history,
        session_lock=session_lock,
        question=question,
        instruction=INSTRUCTION,
        gemini_tools=available_tools,
        gemini_tool_dispatch=gemini_tool_dispatch,
        groq_tool_map=groq_tool_map,
        groq_tool_schemas=GROQ_TOOLS,
        load_history=lambda: _load_thread(meeting_id, user_id),
        history_turn_limit=HISTORY_TURN_LIMIT,
        save_exchange=_save,
        usage_operation="chat",
        usage_user_id=user_id,
        usage_meeting_id=meeting_id,
        done_session_id=sid,
        max_tool_iterations=MAX_TOOL_ITERATIONS,
        logger=logger,
        log_prefix="[chat]",
    )) as events:
        async for event in events:
            yield event


async def ask_question(meeting_id: str, question: str, session_id: str | None = None, user_id: str = None) -> dict:
    """
    Non-streaming wrapper kept for callers that just want the finished answer.
    It drains ask_question_stream and joins the deltas, so both paths share one
    implementation of the agent loop.
    """
    sid = _session_id(meeting_id, session_id)
    answer_parts: list[str] = []
    tools_used: list[str] = []

    async for event in ask_question_stream(meeting_id, question, session_id, user_id):
        kind = event["type"]
        if kind == "delta":
            answer_parts.append(event["text"])
        elif kind == "reset":
            # The deltas so far were superseded by a retry or by the
            # provider fallback; keeping them would prefix the answer with
            # an abandoned fragment of itself.
            answer_parts.clear()
        elif kind == "done":
            sid = event["session_id"]
            tools_used = event["tools_used"]
        elif kind == "error":
            # Mirrors the pre-streaming behaviour, where this surfaced as a 500
            raise ValueError(event["message"])

    return {
        "session_id": sid,
        "answer": "".join(answer_parts),
        "tools_used": tools_used,
    }
