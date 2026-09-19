"""
Fallback chat path using Groq (openai/gpt-oss-120b by default), used when
Gemini keeps failing (429/503/504) in rag/chat_service.py after its own
retries are exhausted.

Chat was the only stage of the pipeline with no second provider:
transcription falls back to Sarvam, embedding falls back to Jina, but a
Gemini outage during "Ask about this meeting" produced nothing except a
canned "try again later" message. This module closes that gap.

It mirrors the Gemini agent loop rather than replacing it - same four tools,
same streamed event shapes ({"type": "tool"}, {"type": "delta"}) - so the SSE
contract in api/chat.py and the frontend need no changes.

Groq exposes an OpenAI-compatible API, so this talks to it over plain httpx
instead of pulling in another SDK - the same approach
embedding_fallback_jina.py takes for the embedding fallback.
"""

import asyncio
import json
import logging
import random
from typing import Any, AsyncGenerator

import httpx

from app.config import settings

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Runs inside chat_service.ask_question_stream, which binds the turn's
# meeting_id/user_id log fields (Phase B5).
logger = logging.getLogger(__name__)

# Worth retrying on Groq's side. Mirrors gemini_errors.TRANSIENT_STATUS_CODES
# but is kept separate: this path never sees a google.genai exception, and
# Groq additionally returns 500/502 under load.
TRANSIENT_STATUS_CODES = (429, 500, 502, 503, 504)

TRANSIENT_NETWORK_ERRORS = (
    httpx.ConnectError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
)

# The Gemini path hands google-genai the Python callables and lets the SDK
# derive tool schemas from their signatures and docstrings. Groq's
# OpenAI-compatible API has no such introspection, so the same four tools are
# declared explicitly here. Keep these descriptions in sync with the
# docstrings in chat_service.py - they are what the model reasons over when
# choosing a tool.
GROQ_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_meeting_summary",
            "description": (
                "Returns the high-level summary of the meeting, key points discussed, "
                "and the final conclusion/resolution. Use this FIRST for broad questions "
                "like 'what was this meeting about', 'what were the main topics', or "
                "'how did it conclude'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_action_items",
            "description": (
                "Returns the concrete tasks, decisions, or follow-ups mentioned in the "
                "meeting, including the owner and timestamp if available. Use this "
                "specifically when asked about action items, tasks, or follow-ups."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_by_speaker",
            "description": (
                "Searches the transcript for everything said by a specific person. Use "
                "this when the user asks 'what did Alice say', 'find quotes by Bob', or "
                "'did John mention anything?'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "speaker_name": {
                        "type": "string",
                        "description": "The name of the speaker to search for.",
                    }
                },
                "required": ["speaker_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_transcript",
            "description": (
                "Semantically searches this meeting's full transcript for passages "
                "relevant to `query`, and returns the matching passages with their "
                "speakers and MM:SS timestamps. Use this for specific questions the "
                "summary can't answer: exact wording, or anything tied to a specific "
                "moment or topic. Call it more than once with reworded queries if the "
                "first results don't fully answer the question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "A focused natural-language description of what to find, "
                            "e.g. 'budget concerns raised about the Q3 launch'."
                        ),
                    }
                },
                "required": ["query"],
            },
        },
    },
]


# Appended to the shared INSTRUCTION for this provider only. gpt-oss reaches
# for search_transcript repeatedly with reworded queries instead of pivoting to
# another tool or answering from what it already has - left alone it will spend
# every tool iteration re-searching and end with no answer at all. The shared
# instruction is not changed for this, because the Gemini path does not have
# the problem and the prompt is what its own tool selection is tuned against.
GROQ_INSTRUCTION_SUFFIX = """

Tool efficiency (important):
- Prefer `get_meeting_summary` or `get_action_items` first for anything about
  owners, tasks, topics, or outcomes - they return the whole picture in one call.
- Do NOT call the same tool again with a reworded query unless the new query
  looks for something genuinely different. Two similar searches will return
  similar passages.
- Once you have passages that address the question, ANSWER. Do not keep
  searching for a better version of what you already have.
- If the transcript genuinely does not contain the answer, say so plainly
  instead of searching again.
"""


def gemini_history_to_openai(history) -> list[dict]:
    """
    Converts the session's Gemini `types.Content` history into OpenAI-format
    messages for Groq.

    Only text-bearing turns are carried over. Function-call and
    function-response parts are deliberately dropped: Gemini's function calls
    carry no id, while OpenAI's schema requires every `tool` message to
    reference a matching `tool_call_id`, so mirroring them would mean inventing
    ids and hoping the two providers' notions of a tool turn line up. The tools
    are deterministic reads over the meeting's own rows, so it is both cheaper
    and more reliable to let Groq re-call whatever it needs than to replay a
    half-finished Gemini tool round-trip into a different provider's format.
    """
    messages: list[dict] = []
    for content in history:
        parts = content.parts or []
        text = "".join(p.text for p in parts if getattr(p, "text", None))
        if not text.strip():
            continue
        role = "assistant" if content.role == "model" else "user"
        messages.append({"role": role, "content": text})
    return messages


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in TRANSIENT_STATUS_CODES
    return isinstance(exc, TRANSIENT_NETWORK_ERRORS)


def _accumulate_tool_call_deltas(acc: dict, deltas: list[dict]) -> None:
    """
    Merges one chunk's `tool_calls` deltas into `acc`, keyed by the `index`
    field. The id and function name usually arrive whole in the first chunk for
    a given index, while `function.arguments` is streamed in fragments that
    have to be concatenated before they parse as JSON.
    """
    for delta in deltas:
        index = delta.get("index", 0)
        entry = acc.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if delta.get("id"):
            entry["id"] = delta["id"]
        function = delta.get("function") or {}
        if function.get("name"):
            entry["name"] = function["name"]
        if function.get("arguments"):
            entry["arguments"] += function["arguments"]


async def _stream_one_turn(
    client: httpx.AsyncClient,
    messages: list[dict],
    force_answer: bool = False,
    tool_schemas: list[dict] = GROQ_TOOLS,
):
    """
    Runs a single Groq turn, yielding ("delta", text) as content arrives and
    finally ("final", {...}) with the assembled tool calls, full text and usage.

    `force_answer` sends tool_choice="none", which makes the model answer from
    what it has already gathered instead of calling another tool. It is used on
    the last permitted iteration so the budget ends in an answer rather than in
    a half-finished search.

    `tool_schemas` is the tool list offered to the model; the per-meeting
    GROQ_TOOLS unless the caller runs a different chat.

    Retries transient failures, but only before any text has been yielded -
    once a delta has gone out to the caller it has already reached the user's
    screen, and re-running the turn would duplicate it.
    """
    max_retries = 3
    for attempt in range(max_retries):
        tool_call_acc: dict = {}
        text_parts: list[str] = []
        usage: dict = {}
        emitted_text = False

        payload = {
            "model": settings.groq_chat_model,
            "messages": messages,
            "tools": tool_schemas,
            "tool_choice": "none" if force_answer else "auto",
            "temperature": 0.0,
            "max_tokens": settings.groq_max_output_tokens,
            "stream": True,
        }

        try:
            async with client.stream(
                "POST",
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    # The body has to be pulled in explicitly on a streamed
                    # response before it can be read for the error message.
                    await response.aread()
                    response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    # Groq reports usage on the terminal chunk, either at the
                    # top level or nested under x_groq depending on the route.
                    chunk_usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
                    if chunk_usage:
                        usage = chunk_usage

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}

                    if delta.get("tool_calls"):
                        _accumulate_tool_call_deltas(tool_call_acc, delta["tool_calls"])

                    # `reasoning` is gpt-oss's chain of thought. It is
                    # deliberately not streamed - only `content` is the answer.
                    content = delta.get("content")
                    if content:
                        text_parts.append(content)
                        emitted_text = True
                        yield "delta", content

            tool_calls = [tool_call_acc[i] for i in sorted(tool_call_acc)]
            yield "final", {
                "text": "".join(text_parts),
                "tool_calls": tool_calls,
                "usage": usage,
            }
            return

        except (httpx.HTTPStatusError,) + TRANSIENT_NETWORK_ERRORS as e:
            status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else type(e).__name__
            logger.warning(
                "[chat_fallback] Groq turn failed (attempt %s/%s), code=%s: %s",
                attempt + 1, max_retries, status, e,
            )
            if emitted_text or not _is_transient(e) or attempt == max_retries - 1:
                raise
            await asyncio.sleep((2 ** attempt) + random.uniform(0, 0.5))


async def run_groq_chat_stream(
    messages: list[dict],
    instruction: str,
    tool_map: dict,
    max_tool_iterations: int = 6,
    tool_schemas: list[dict] = GROQ_TOOLS,
) -> AsyncGenerator[dict, None]:
    """
    Runs the same agentic tool-calling loop as chat_service.ask_question_stream,
    but against Groq. Yields:

        {"type": "tool",  "name": ...}   - a tool is starting
        {"type": "delta", "text": ...}   - a piece of the answer
        {"type": "final", "text": ..., "tools_used": [...], ...}  - loop finished

    `tool_map` maps a bare tool name to a synchronous callable taking the
    decoded argument dict. The bodies open blocking SQLAlchemy sessions (and
    search_transcript makes a blocking embedding call), so each runs in a
    worker thread - blocking the event loop here would stall every other
    request the process is serving, not just this one. `tool_schemas` are
    the declarations sent with every turn and must describe the same tools
    as `tool_map`; they default to the per-meeting GROQ_TOOLS.

    Raises on an unrecoverable Groq failure; the caller is expected to fall
    back to its canned message.
    """
    convo: list[dict] = [{"role": "system", "content": instruction + GROQ_INSTRUCTION_SUFFIX}] + list(messages)
    tools_used: list[str] = []
    answer_parts: list[str] = []
    prompt_tokens = 0
    output_tokens = 0

    timeout = httpx.Timeout(settings.groq_timeout_seconds, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for iteration in range(max_tool_iterations):
            # Spend the last iteration answering rather than searching: by then
            # the tool results are already in `convo`, and one more search the
            # model has no turn left to read is strictly worse than an answer.
            force_answer = iteration == max_tool_iterations - 1
            turn_text = ""
            tool_calls: list[dict] = []

            async for kind, value in _stream_one_turn(client, convo, force_answer=force_answer, tool_schemas=tool_schemas):
                if kind == "delta":
                    answer_parts.append(value)
                    yield {"type": "delta", "text": value}
                else:
                    turn_text = value["text"]
                    tool_calls = value["tool_calls"]
                    usage = value["usage"] or {}
                    prompt_tokens += usage.get("prompt_tokens", 0) or 0
                    output_tokens += usage.get("completion_tokens", 0) or 0

            if not tool_calls:
                yield {
                    "type": "final",
                    "text": "".join(answer_parts),
                    "tools_used": tools_used,
                    "prompt_tokens": prompt_tokens,
                    "output_tokens": output_tokens,
                }
                return

            # Echo the assistant's tool-call turn back verbatim; the OpenAI
            # schema requires it to precede the matching `tool` messages.
            convo.append({
                "role": "assistant",
                "content": turn_text or None,
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call["arguments"] or "{}"},
                    }
                    for call in tool_calls
                ],
            })

            for call in tool_calls:
                yield {"type": "tool", "name": call["name"]}
                tools_used.append(call["name"])

            async def _dispatch(call: dict) -> dict:
                name = call["name"]
                raw_args = call.get("arguments") or ""
                try:
                    args = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    args = {}

                func = tool_map.get(name)
                if func is None:
                    result: Any = {"error": f"Unknown tool {name}"}
                else:
                    try:
                        result = await asyncio.to_thread(func, args)
                    except Exception as e:
                        result = {"error": str(e)}

                return {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps({"result": result}, default=str),
                }

            # gather preserves input order, so tool replies stay aligned with
            # the calls they answer - and independent calls in the same turn
            # run concurrently instead of end to end.
            convo.extend(await asyncio.gather(*(_dispatch(call) for call in tool_calls)))

    # Ran out of tool iterations without the model settling on an answer.
    yield {
        "type": "final",
        "text": "".join(answer_parts),
        "tools_used": tools_used,
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens,
        "exhausted": True,
    }
