import asyncio
from collections import OrderedDict
import httpx
from google import genai
from google.genai import types, errors

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import ChatMessage
from app.rag.tools import get_meeting_summary, get_action_items, search_by_speaker, search_transcript
from app.rag.chat_fallback_groq import gemini_history_to_openai, run_groq_chat_stream
from app.services.cost_tracker import gemini_generation_cost, log_cost
from app.services.gemini_errors import is_transient

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

class LRUSessionCache:
    def __init__(self, capacity: int = 500):
        self.capacity = capacity
        # Maps key -> (asyncio.Lock(), list[types.Content])
        self.cache: OrderedDict[str, tuple[asyncio.Lock, list[types.Content]]] = OrderedDict()
        self.lock = asyncio.Lock()

    async def get_session(self, user_id: str, meeting_id: str, session_id: str) -> tuple[asyncio.Lock, list[types.Content]]:
        key = f"{user_id}:{meeting_id}:{session_id}"
        async with self.lock:
            if key not in self.cache:
                if len(self.cache) >= self.capacity:
                    self.cache.popitem(last=False)  # Remove oldest (FIFO/LRU)
                self.cache[key] = (asyncio.Lock(), [])
            else:
                self.cache.move_to_end(key)
            return self.cache[key]

# Global session cache instance
_session_cache = LRUSessionCache(capacity=500)

# How many stored turns are replayed into a cold session, and the cap the
# warm in-memory history is truncated to. One constant for both, so a
# session rebuilt from the database sees exactly as much context as one
# that stayed in the cache.
HISTORY_TURN_LIMIT = 20


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

# We create the genai client here
client = genai.Client(
    api_key=settings.gemini_api_key,
    http_options=types.HttpOptions(timeout=int(settings.chat_timeout_seconds * 1000)),
)

class DummyToolContext:
    def __init__(self, meeting_id: str, user_id: str):
        self.state = {"meeting_id": meeting_id, "user_id": user_id}

def _session_id(meeting_id: str, session_id: str | None) -> str:
    return session_id or f"meeting-{meeting_id}"

async def ask_question_stream(meeting_id: str, question: str, session_id: str | None = None, user_id: str = None):
    """
    Runs the agent loop and yields events as they happen, so the caller can
    show the answer while it's still being generated instead of waiting for
    the whole multi-turn tool-calling loop to finish:

        {"type": "tool",  "name": "search_transcript"}  - a tool is starting
        {"type": "delta", "text": "..."}                - a piece of the answer
        {"type": "reset"}                               - discard every delta
                                                          sent so far; the
                                                          answer is starting
                                                          over
        {"type": "done",  "session_id": ..., "tools_used": [...]}
        {"type": "error", "message": "..."}             - fatal; stream ends

    Joining every "delta" since the last "reset" reproduces the complete
    answer - including the canned fallback messages - which is what the
    non-streaming ask_question() wrapper below relies on. Keeping one
    implementation of the loop means the streaming and non-streaming paths
    can't drift apart.
    """
    if not user_id:
        raise ValueError("user_id must be provided to scope the agent's context.")

    sid = _session_id(meeting_id, session_id)
    session_lock, history = await _session_cache.get_session(user_id, meeting_id, sid)
    
    async with session_lock:
        # A cold session - the first question this process has seen for the
        # meeting, or one the LRU evicted - starts from the stored thread
        # instead of from nothing. This is what makes the conversation
        # survive a refresh, a logout, an eviction or a restart, and what
        # keeps the model's context in step with the thread the UI shows.
        if not history:
            try:
                history.extend(await asyncio.to_thread(_load_thread, meeting_id, user_id))
            except Exception as e:
                # Older context is worth losing; the question is not. Answer
                # it against an empty history rather than failing outright.
                print(f"[chat] could not load stored thread for meeting {meeting_id}: {e}")

        # Truncate history to preserve context window limits
        if len(history) > HISTORY_TURN_LIMIT:
            # Modify the list in-place to retain the reference inside the cache
            history[:] = history[-HISTORY_TURN_LIMIT:]

        # Where to roll history back to if this request never produces an
        # answer. Without it a failed question stays in the session forever:
        # every later question re-sends it as context, so a user retrying a
        # failing turn a few times ends up paying for - and waiting on - a
        # prompt padded with their own unanswered questions and the partial
        # tool round-trips that went with them.
        history_mark = len(history)

        # Append the user's question
        history.append(types.Content(role="user", parts=[types.Part.from_text(text=question)]))

        def _rollback_history():
            history[:] = history[:history_mark]
    
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

        tools_used = []
    
        config = types.GenerateContentConfig(
            tools=available_tools,
            system_instruction=INSTRUCTION,
            temperature=0.0,
            # Must stay disabled. Passing Python callables as `tools` otherwise
            # opts into the SDK's Automatic Function Calling, which invokes
            # them itself and never surfaces the function_call parts that the
            # dispatch loop below is built to handle. Two things break as a
            # result: in streaming mode the SDK emits a single empty-text chunk
            # (so the turn has neither text nor tool calls, and the request
            # fails with "No response was returned by the model"), and because
            # AFC runs the sync tool bodies inline inside the async client
            # call, they execute on the event loop thread - defeating the
            # asyncio.to_thread offload below, which is the whole point of it.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
    
        MAX_TOOL_ITERATIONS = 6
        loop_count = 0
        prompt_tokens_total = 0
        output_tokens_total = 0
        # Everything the client has been shown for this reply, in order.
        # Cleared by a reset, so it always matches what is on screen - which
        # is what makes it the right thing to store.
        answer_parts: list[str] = []
        # Whether any answer text has reached the client for this reply.
        # Scoped to the whole request, not to one turn of the tool loop,
        # because a "reset" clears the entire reply bubble - so a preamble
        # streamed two tool calls ago is text that has to be accounted for
        # before a retry or the Groq fallback starts writing over it.
        streamed_any_text = False

        async def _persist_exchange():
            """
            Stores the exchange the user just saw. A storage failure must not
            fail an answer that has already been streamed in full, so it is
            logged and swallowed: the turn stays in the in-memory session
            either way, and only a later cold start would miss it.
            """
            answer = "".join(answer_parts).strip()
            if not answer:
                return
            try:
                await asyncio.to_thread(
                    _save_exchange, meeting_id, user_id, question, answer, list(tools_used)
                )
            except Exception as e:
                print(f"[chat] could not store thread for meeting {meeting_id}: {e}")

        def _log_chat_cost():
            cost = gemini_generation_cost(prompt_tokens_total, output_tokens_total)
            log_cost(
                "chat",
                meeting=meeting_id,
                in_tok=prompt_tokens_total,
                out_tok=output_tokens_total,
                tool_calls=len(tools_used),
                usd=f"{cost:.6f}",
            )

        while True:
            if loop_count >= MAX_TOOL_ITERATIONS:
                _log_chat_cost()
                _rollback_history()
                yield {"type": "delta", "text": "I'm having trouble finding the exact information you requested. Could you try rephrasing your question?"}
                yield {"type": "done", "session_id": sid, "tools_used": tools_used}
                return
            loop_count += 1

            chat_max_retries = settings.chat_max_retries
            # Per-turn accumulators. Text parts are streamed to the caller as
            # they arrive AND collected here, so the complete model turn can
            # still be appended to history for the next iteration.
            text_acc: list[str] = []
            function_calls = []
            # The original Part objects exactly as the model produced them.
            # They must be echoed back verbatim in history rather than rebuilt:
            # Gemini attaches a thought_signature to function-call parts, and
            # reconstructing a part from just its .function_call drops it,
            # which the API rejects on the next turn with
            # "400 INVALID_ARGUMENT: Function call is missing a thought_signature".
            model_parts = []
            turn_prompt_tokens = 0
            turn_output_tokens = 0

            for attempt in range(chat_max_retries):
                text_acc = []
                function_calls = []
                model_parts = []
                turn_prompt_tokens = 0
                turn_output_tokens = 0
                try:
                    stream = await client.aio.models.generate_content_stream(
                        model=settings.rag_agent_model,
                        contents=history,
                        config=config,
                    )
                    async for chunk in stream:
                        # Streaming usage_metadata is cumulative for the turn,
                        # so keep the latest rather than summing per chunk.
                        usage = getattr(chunk, "usage_metadata", None)
                        if usage:
                            turn_prompt_tokens = usage.prompt_token_count or 0
                            turn_output_tokens = usage.candidates_token_count or 0

                        if not chunk.candidates:
                            continue
                        for part in chunk.candidates[0].content.parts or []:
                            if part.function_call:
                                function_calls.append(part.function_call)
                                model_parts.append(part)
                            elif getattr(part, "text", None):
                                text_acc.append(part.text)
                                model_parts.append(part)
                                streamed_any_text = True
                                answer_parts.append(part.text)
                                yield {"type": "delta", "text": part.text}
                    break  # Success!
                except (errors.APIError, httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                    # Log it: without this the failure is invisible, since every
                    # exit below replaces the error with a generic user-facing
                    # message. A non-transient fault (e.g. a 400 from malformed
                    # history) otherwise looks identical to rate limiting.
                    code = getattr(e, "code", None) or type(e).__name__
                    print(f"[chat] generate_content_stream failed (attempt {attempt+1}/{chat_max_retries}), code={code}: {e}")

                    # Deltas from the dead turn have already reached the
                    # client, so retrying or failing over would print the
                    # answer twice. Tell the client to drop what it has and
                    # treat the reply as starting from scratch, then carry
                    # on into the retries and the Groq fallback like any
                    # other failure.
                    #
                    # This used to `return` with a canned "interrupted
                    # mid-answer" message, which made the single most common
                    # failure - a stream that dies a few tokens in - the one
                    # case that reached neither the retries nor the fallback:
                    # a half-written bubble and an error was the whole answer.
                    #
                    # A reset also clears any preamble text from an earlier
                    # tool iteration of the same reply. That text is gone from
                    # the screen but still in `history`, so the model won't
                    # repeat it - losing a "let me check..." beats showing it
                    # twice.
                    if streamed_any_text:
                        yield {"type": "reset"}
                        streamed_any_text = False
                        answer_parts.clear()

                    if attempt == chat_max_retries - 1:
                        _log_chat_cost()

                        # Retries are spent, so Gemini is genuinely down rather
                        # than briefly busy: hand the question to Groq instead
                        # of giving up. Only for transient faults - a 400 from
                        # malformed history would fail identically anywhere,
                        # and burning a second provider's quota on it is waste.
                        if is_transient(e) and settings.groq_api_key:
                            print(f"[chat] Gemini {code} persisted after retries, falling back to Groq...")
                            fallback_text = ""
                            fallback_streamed = False
                            fallback_exhausted = False
                            try:
                                async for event in run_groq_chat_stream(
                                    messages=gemini_history_to_openai(history),
                                    instruction=INSTRUCTION,
                                    tool_map=groq_tool_map,
                                    max_tool_iterations=MAX_TOOL_ITERATIONS,
                                ):
                                    if event["type"] == "final":
                                        fallback_text = event["text"]
                                        fallback_exhausted = event.get("exhausted", False)
                                        tools_used.extend(event["tools_used"])
                                        log_cost(
                                            "chat_fallback",
                                            meeting=meeting_id,
                                            provider="groq",
                                            model=settings.groq_chat_model,
                                            in_tok=event["prompt_tokens"],
                                            out_tok=event["output_tokens"],
                                            tool_calls=len(event["tools_used"]),
                                            usd="0.000000",
                                        )
                                    else:
                                        if event["type"] == "delta":
                                            fallback_streamed = True
                                            answer_parts.append(event["text"])
                                        yield event
                            except Exception as groq_err:
                                print(f"[chat] Groq fallback also failed: {groq_err}")

                            if fallback_text.strip():
                                # Record the answer in Gemini's own history
                                # format: this session may well be back on
                                # Gemini by the next question, and it should
                                # see what was already said either way.
                                history.append(types.Content(
                                    role="model",
                                    parts=[types.Part.from_text(text=fallback_text)],
                                ))
                                await _persist_exchange()
                                yield {"type": "done", "session_id": sid, "tools_used": tools_used}
                                return

                            if fallback_streamed:
                                # Groq died partway through an answer the user
                                # can already see; clear it so the message
                                # below replaces the half-answer instead of
                                # reading as its continuation.
                                yield {"type": "reset"}
                                answer_parts.clear()

                            if fallback_exhausted:
                                # Groq answered fine, it just kept searching
                                # without settling. Say that, rather than
                                # blaming load - the same wording the Gemini
                                # path uses when it hits MAX_TOOL_ITERATIONS.
                                _rollback_history()
                                yield {"type": "delta", "text": "I'm having trouble finding the exact information you requested. Could you try rephrasing your question?"}
                                yield {"type": "done", "session_id": sid, "tools_used": tools_used}
                                return

                        _rollback_history()
                        yield {"type": "delta", "text": "The AI service is currently experiencing high load or rate limits. Please try again in a few moments."}
                        yield {"type": "done", "session_id": sid, "tools_used": tools_used}
                        return
                    await asyncio.sleep((2 ** attempt) + 0.5)

            prompt_tokens_total += turn_prompt_tokens
            output_tokens_total += turn_output_tokens

            if not text_acc and not function_calls:
                _log_chat_cost()
                _rollback_history()
                yield {"type": "error", "message": "No response was returned by the model."}
                return

            # Append the model turn to history so the next iteration sees what
            # was said. The parts go back exactly as they arrived (see the
            # thought_signature note above) - streamed text stays split across
            # several parts, which is equivalent to one merged part as far as
            # the model is concerned.
            history.append(types.Content(role="model", parts=model_parts))

            # If there are function calls, execute them and re-prompt
            if function_calls:
                # Every tool body is synchronous: it opens a blocking
                # SQLAlchemy session, and _search_transcript additionally makes
                # a blocking Gemini embedding call (which internally retries
                # with time.sleep). Calling those directly from this coroutine
                # would block the event loop for their full duration, stalling
                # every other request the process is serving - not just this
                # one. asyncio.to_thread moves each into a worker thread, where
                # blocking is confined to that thread. Independent calls in the
                # same turn are then run concurrently instead of end-to-end.
                async def _dispatch(fc):
                    func_name = fc.name
                    func_args = fc.args or {}

                    def _run():
                        if func_name == "_get_meeting_summary":
                            return _get_meeting_summary()
                        elif func_name == "_get_action_items":
                            return _get_action_items()
                        elif func_name == "_search_by_speaker":
                            return _search_by_speaker(func_args.get("speaker_name", ""))
                        elif func_name == "_search_transcript":
                            return _search_transcript(func_args.get("query", ""))
                        return {"error": f"Unknown tool {func_name}"}

                    try:
                        tool_result = await asyncio.to_thread(_run)
                    except Exception as e:
                        tool_result = {"error": str(e)}

                    return types.Part.from_function_response(
                        name=func_name,
                        response={"result": tool_result}
                    )

                # Map the wrapped tool names to the original names to show cleanly in the UI
                clean_names = [fc.name.lstrip("_") for fc in function_calls]
                tools_used.extend(clean_names)
                # Surface each tool before it runs, so the UI can show what the
                # assistant is doing during the wait instead of a bare spinner.
                for clean_name in clean_names:
                    yield {"type": "tool", "name": clean_name}

                # gather preserves input order, so responses stay aligned with
                # the function_calls they answer.
                responses = list(await asyncio.gather(*(_dispatch(fc) for fc in function_calls)))

                # Append the function responses to history and loop to generate content again
                history.append(types.Content(role="user", parts=responses))
                continue

            # No function calls, meaning the text streamed above was the final answer
            _log_chat_cost()
            await _persist_exchange()
            yield {"type": "done", "session_id": sid, "tools_used": tools_used}
            return


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
