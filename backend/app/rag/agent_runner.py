"""
The streaming agent loop shared by every chat: Gemini streaming, retries with
backoff, `reset` events, the Groq fallback, history rollback, tool dispatch,
cost tracking and saving the exchange.

Everything specific to one chat - its tools, prompt, persistence, session id
and cost labels - is passed in by the caller (see chat_service.py for the
per-meeting chat).
"""

import asyncio
import logging
import uuid
from typing import Any, AsyncGenerator, Callable
import httpx
from google import genai
from google.genai import types, errors

from app.config import settings
from app.rag.chat_fallback_groq import gemini_history_to_openai, run_groq_chat_stream
from app.services.cost_tracker import gemini_generation_cost, groq_generation_cost, record_usage
from app.services.gemini_errors import is_transient

# We create the genai client here
client = genai.Client(
    api_key=settings.gemini_api_key,
    http_options=types.HttpOptions(timeout=int(settings.chat_timeout_seconds * 1000)),
)

# What the streaming loop below retries - and, if streamed text is already on
# screen, resets for. httpx.NetworkError rather than just ConnectError: it also
# covers ReadError and WriteError (a connection reset or broken pipe while the
# answer is streaming), which used to escape the loop mid-answer with no reset,
# no retry and the question left in the session.
#
# Only which failures are *retried*. Whether a failure that outlasts the
# retries falls back to Groq is still gemini_errors.is_transient's decision,
# shared with transcription and embedding, and ReadError is not in it.
_RETRYABLE_STREAM_ERRORS = (
    errors.APIError,
    httpx.NetworkError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
)


async def run_agent_stream(
    *,
    history: list[types.Content],
    session_lock: asyncio.Lock,
    question: str,
    instruction: str,
    gemini_tools: list[Callable],
    gemini_tool_dispatch: dict[str, Callable[[dict], Any]],
    groq_tool_map: dict[str, Callable[[dict], Any]],
    groq_tool_schemas: list[dict],
    load_history: Callable[[], list[types.Content]],
    history_turn_limit: int,
    save_exchange: Callable[[str, str, list[str]], None],
    usage_operation: str,
    usage_user_id: str | None,
    usage_meeting_id: str | None,
    done_session_id: str,
    max_tool_iterations: int,
    logger: logging.Logger,
    log_prefix: str = "[chat]",
) -> AsyncGenerator[dict, None]:
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
    non-streaming ask_question() wrapper in chat_service.py relies on. Keeping
    one implementation of the loop means the streaming and non-streaming
    paths can't drift apart.

    `history` and `session_lock` come from the caller's session cache; the
    lock is held for the whole turn. `load_history` and `save_exchange` are
    blocking (SQLAlchemy) and are run through asyncio.to_thread.
    `gemini_tool_dispatch` maps each Gemini function name (the callable's
    __name__) to a callable taking the decoded argument dict; `groq_tool_map`
    does the same for the bare names in `groq_tool_schemas`.

    `logger` is the caller's, so each chat's lines keep their own logger
    name. Callers should wrap this generator in contextlib.aclosing, so a
    client disconnecting mid-stream releases the session lock right away.
    """
    sid = done_session_id

    async with session_lock:
        # A cold session - the first question this process has seen for the
        # meeting, or one the LRU evicted - starts from the stored thread
        # instead of from nothing. This is what makes the conversation
        # survive a refresh, a logout, an eviction or a restart, and what
        # keeps the model's context in step with the thread the UI shows.
        if not history:
            try:
                history.extend(await asyncio.to_thread(load_history))
            except Exception as e:
                # Older context is worth losing; the question is not. Answer
                # it against an empty history rather than failing outright.
                logger.warning(log_prefix + " could not load stored thread: %s", e, exc_info=True)

        # Truncate history to preserve context window limits
        if len(history) > history_turn_limit:
            # Modify the list in-place to retain the reference inside the cache
            history[:] = history[-history_turn_limit:]

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

        tools_used = []

        config = types.GenerateContentConfig(
            tools=gemini_tools,
            system_instruction=instruction,
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
                    save_exchange, question, answer, list(tools_used)
                )
            except Exception as e:
                logger.warning(log_prefix + " could not store thread: %s", e, exc_info=True)

        # Groups this turn's usage rows - a Gemini row, and a Groq row if the
        # fallback ran - so cost per chat turn is one GROUP BY (Phase B4).
        request_id = uuid.uuid4()

        async def _record_gemini_usage(outcome: str):
            """
            One ai_usage_events row for everything Gemini consumed this turn,
            across every tool iteration. Off the event loop, and never raises
            (see cost_tracker.record_usage). Token counts are None rather than
            0 when no attempt ever reported usage.
            """
            reported = prompt_tokens_total > 0 or output_tokens_total > 0
            await asyncio.to_thread(
                record_usage,
                operation=usage_operation,
                provider="gemini",
                model=settings.rag_agent_model,
                outcome=outcome,
                request_id=request_id,
                user_id=usage_user_id,
                meeting_id=usage_meeting_id,
                input_tokens=prompt_tokens_total if reported else None,
                output_tokens=output_tokens_total if reported else None,
                usd=gemini_generation_cost(prompt_tokens_total, output_tokens_total),
            )

        while True:
            if loop_count >= max_tool_iterations:
                await _record_gemini_usage("failed")
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
                except Exception as e:
                    if not isinstance(e, _RETRYABLE_STREAM_ERRORS):
                        # Not a provider or network failure this loop knows how
                        # to retry. Let it reach the route, which reports it as
                        # an error event - but roll the question out of the
                        # session first. Escaping without this left the failed
                        # question in history, and the next question was sent
                        # to the model behind it.
                        _rollback_history()
                        await _record_gemini_usage("failed")
                        raise

                    # Log it: without this the failure is invisible, since every
                    # exit below replaces the error with a generic user-facing
                    # message. A non-transient fault (e.g. a 400 from malformed
                    # history) otherwise looks identical to rate limiting.
                    code = getattr(e, "code", None) or type(e).__name__
                    logger.warning(
                        log_prefix + " generate_content_stream failed (attempt %s/%s), code=%s: %s",
                        attempt + 1, chat_max_retries, code, e,
                    )

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
                        await _record_gemini_usage("failed")

                        # Retries are spent, so Gemini is genuinely down rather
                        # than briefly busy: hand the question to Groq instead
                        # of giving up. Only for transient faults - a 400 from
                        # malformed history would fail identically anywhere,
                        # and burning a second provider's quota on it is waste.
                        if is_transient(e) and settings.groq_api_key:
                            logger.warning(log_prefix + " Gemini %s persisted after retries, falling back to Groq", code)
                            fallback_text = ""
                            fallback_streamed = False
                            fallback_exhausted = False
                            groq_final = None
                            try:
                                async for event in run_groq_chat_stream(
                                    messages=gemini_history_to_openai(history),
                                    instruction=instruction,
                                    tool_map=groq_tool_map,
                                    max_tool_iterations=max_tool_iterations,
                                    tool_schemas=groq_tool_schemas,
                                ):
                                    if event["type"] == "final":
                                        groq_final = event
                                        fallback_text = event["text"]
                                        fallback_exhausted = event.get("exhausted", False)
                                        tools_used.extend(event["tools_used"])
                                    else:
                                        if event["type"] == "delta":
                                            fallback_streamed = True
                                            answer_parts.append(event["text"])
                                        yield event
                            except Exception as groq_err:
                                logger.exception(log_prefix + " Groq fallback also failed: %s", groq_err)

                            # One row for the fallback, whatever happened. A
                            # Groq failure that never reached its final event
                            # reported no usage, so its token counts stay None.
                            await asyncio.to_thread(
                                record_usage,
                                operation=usage_operation,
                                provider="groq",
                                model=settings.groq_chat_model,
                                outcome="fallback" if fallback_text.strip() else "failed",
                                request_id=request_id,
                                user_id=usage_user_id,
                                meeting_id=usage_meeting_id,
                                input_tokens=groq_final["prompt_tokens"] if groq_final else None,
                                output_tokens=groq_final["output_tokens"] if groq_final else None,
                                usd=groq_generation_cost(groq_final["prompt_tokens"], groq_final["output_tokens"]) if groq_final else 0.0,
                            )

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
                                # path uses when it hits max_tool_iterations.
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
                await _record_gemini_usage("failed")
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
                        func = gemini_tool_dispatch.get(func_name)
                        if func is None:
                            return {"error": f"Unknown tool {func_name}"}
                        return func(func_args)

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
            await _record_gemini_usage("ok")
            await _persist_exchange()
            yield {"type": "done", "session_id": sid, "tools_used": tools_used}
            return
