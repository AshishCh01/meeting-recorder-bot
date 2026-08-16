from google.adk.runners import InMemoryRunner
from google.genai import types

from app.rag.agent import root_agent

APP_NAME = "meeting_qa"

# Created once at import time and reused for the process lifetime, same as
# any other client. The runner/session service are safe to call concurrently
# - isolation happens per (user_id, session_id).
_runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)


def _session_id(meeting_id: str, session_id: str | None) -> str:
    # Default to one running chat session per meeting so follow-up questions
    # keep context, unless the caller wants separate threads.
    return session_id or f"meeting-{meeting_id}"


async def _ensure_session(meeting_id: str, session_id: str, user_id: str) -> None:
    existing = await _runner.session_service.get_session(
        app_name=APP_NAME, user_id=meeting_id, session_id=session_id,
    )
    if existing is None:
        await _runner.session_service.create_session(
            app_name=APP_NAME,
            user_id=meeting_id,
            session_id=session_id,
            state={"meeting_id": meeting_id, "user_id": user_id},
        )


async def ask_question(meeting_id: str, question: str, session_id: str | None = None, user_id: str = None) -> dict:
    """
    Runs the RAG agent against one meeting's transcript for a single
    question, returning the final answer text plus which tools were used
    (handy for debugging/UI transparency).
    """
    if not user_id:
        raise ValueError("user_id must be provided to scope the agent's context.")
        
    sid = _session_id(meeting_id, session_id)
    await _ensure_session(meeting_id, sid, user_id)

    message = types.Content(role="user", parts=[types.Part(text=question)])

    answer = ""
    tool_calls = []

    async for event in _runner.run_async(
        user_id=meeting_id, session_id=sid, new_message=message,
    ):
        for call in event.get_function_calls() or []:
            tool_calls.append(call.name)

        if event.is_final_response() and event.content and event.content.parts:
            answer = "".join(part.text or "" for part in event.content.parts)

    return {
        "session_id": sid,
        "answer": answer,
        "tools_used": tool_calls,
    }
