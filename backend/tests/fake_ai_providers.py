"""
Fakes at the AI provider boundary, shared by B3's fallback-ladder and chat
tests. A helper, not a test module - same arrangement as fake_bot_pool.py.

Each fake stands in for the one call the app makes to a provider, and
nothing inside the app: gemini_errors.is_transient, the retry loops and the
fallback modules all run for real. Failures are real-shaped - google.genai's
own ClientError/ServerError with a real status code, httpx's own connection
exceptions, real HTTP status codes and SSE bodies from "Groq" - because the
thing under test is whether the real predicate classifies them correctly.

Every fake appends to a shared `log` list ("gemini:1", "groq:2", ...), and
tests that care about ordering append their own markers to the same list, so
call order and event order can be asserted in one place.
"""
import json
from types import SimpleNamespace

import httpx
from google.genai import errors, types

_STATUS = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    429: "RESOURCE_EXHAUSTED",
    500: "INTERNAL",
    503: "UNAVAILABLE",
    504: "DEADLINE_EXCEEDED",
}


def gemini_error(code: int) -> errors.APIError:
    """What google-genai raises for an HTTP error from Gemini."""
    cls = errors.ClientError if code < 500 else errors.ServerError
    return cls(code, {"error": {"code": code, "message": f"stub {_STATUS[code]}", "status": _STATUS[code]}})


# Factories, not instances: an exception object must not be raised twice.
TRANSIENT = {
    "429": lambda: gemini_error(429),
    "503": lambda: gemini_error(503),
    "504": lambda: gemini_error(504),
    "connect-error": lambda: httpx.ConnectError("connection refused"),
    "timeout": lambda: httpx.ReadTimeout("read timed out"),
    "dropped-connection": lambda: httpx.RemoteProtocolError("peer closed connection without response"),
}

NON_TRANSIENT = {
    "400": lambda: gemini_error(400),
    "401": lambda: gemini_error(401),
    "404": lambda: gemini_error(404),
}


def install_fast_sleeps(monkeypatch):
    """
    Skips the retry backoff sleeps - asyncio's for chat and Groq, time's for
    transcription, embedding and Jina - and records each requested delay, so
    a test can still assert how many backoffs happened.
    """
    import asyncio
    import time

    slept = []

    async def fast_async_sleep(delay, *args, **kwargs):
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fast_async_sleep)
    monkeypatch.setattr(time, "sleep", lambda delay: slept.append(delay))
    return slept


# ---------------------------------------------------------------------------
# Gemini, chat: client.aio.models.generate_content_stream
# ---------------------------------------------------------------------------

def call(name: str, **args):
    """A function-call part, named as chat_service's closures are (leading underscore)."""
    return ("function_call", name, args)


class FakeGeminiChat:
    """
    One scripted reply per generate_content_stream call. A reply is a sequence
    of items streamed in order: a str is a text part, call(...) a function
    call, and an exception raised where it sits - first item means the request
    itself failed, a later one means the stream broke mid-answer.
    """

    def __init__(self, log):
        self.log = log
        self.replies = []
        self.contents_seen = []

    def reply(self, *items):
        self.replies.append(items)
        return self

    def fail(self, make_error, times=1):
        for _ in range(times):
            self.replies.append((make_error(),))
        return self

    @property
    def calls(self):
        return len(self.contents_seen)

    async def generate_content_stream(self, model, contents, config):
        # A snapshot of the prompt as (role, text) pairs, taken before the
        # caller mutates history again.
        self.contents_seen.append([
            (c.role, "".join(p.text or "" for p in (c.parts or []) if getattr(p, "text", None)))
            for c in contents
        ])
        self.log.append(f"gemini:{self.calls}")
        if not self.replies:
            raise AssertionError(f"unscripted Gemini call #{self.calls}")
        items = self.replies.pop(0)
        if items and isinstance(items[0], BaseException):
            raise items[0]

        async def stream():
            for item in items:
                if isinstance(item, BaseException):
                    raise item
                if isinstance(item, str):
                    part = types.Part.from_text(text=item)
                else:
                    _, name, args = item
                    part = types.Part.from_function_call(name=name, args=args)
                yield SimpleNamespace(
                    usage_metadata=None,
                    candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))],
                )

        return stream()

    def install(self, monkeypatch):
        from app.rag import chat_service

        monkeypatch.setattr(chat_service.client.aio.models, "generate_content_stream", self.generate_content_stream)
        return self


# ---------------------------------------------------------------------------
# Groq, chat fallback: POST https://api.groq.com/openai/v1/chat/completions
# ---------------------------------------------------------------------------

def _sse(chunks) -> str:
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


class FakeGroq:
    """
    Scripted HTTP responses for the Groq endpoint, served through a
    MockTransport on the real httpx.AsyncClient chat_fallback_groq builds - so
    status handling, SSE parsing, its own retries and tool-call assembly are
    all the real code.
    """

    def __init__(self, log):
        self.log = log
        self.responses = []
        self.requests = []

    def answer(self, *pieces):
        chunks = [{"choices": [{"delta": {"content": p}}]} for p in pieces]
        chunks.append({"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 7}})
        self.responses.append(("sse", _sse(chunks)))
        return self

    def tool_calls(self, *names):
        chunks = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": i, "id": f"call_{i}", "function": {"name": name, "arguments": "{}"}}
            ]}}]}
            for i, name in enumerate(names)
        ]
        self.responses.append(("sse", _sse(chunks)))
        return self

    def answer_then_drop(self, *pieces):
        """Streams some answer text, then the connection dies mid-body."""
        self.responses.append(("drop", pieces))
        return self

    def status(self, code, times=1):
        for _ in range(times):
            self.responses.append(("status", code))
        return self

    def raise_(self, make_error, times=1):
        for _ in range(times):
            self.responses.append(("raise", make_error))
        return self

    @property
    def calls(self):
        return len(self.requests)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.log.append(f"groq:{self.calls}")
        if not self.responses:
            raise AssertionError(f"unscripted Groq request #{self.calls}")
        kind, value = self.responses.pop(0)
        if kind == "raise":
            raise value()
        if kind == "status":
            return httpx.Response(value, json={"error": {"message": f"stub {value}"}})
        if kind == "drop":
            async def body():
                for p in value:
                    yield f"data: {json.dumps({'choices': [{'delta': {'content': p}}]})}\n\n".encode()
                raise httpx.RemoteProtocolError("peer closed connection mid-body")

            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=value.encode())

    def install(self, monkeypatch):
        from app.rag import chat_fallback_groq

        real_async_client = httpx.AsyncClient
        handler = self._handle

        def client_with_fake_transport(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(chat_fallback_groq.httpx, "AsyncClient", client_with_fake_transport)
        return self


# ---------------------------------------------------------------------------
# Gemini embeddings and Jina
# ---------------------------------------------------------------------------

class FakeGeminiEmbed:
    """client.models.embed_content in embedding_service: fails `failures` times, then embeds."""

    def __init__(self, log, dims=768):
        self.log = log
        self.dims = dims
        self.failures = []
        self.inputs = []

    def fail(self, make_error, times=1):
        self.failures.extend([make_error] * times)
        return self

    @property
    def calls(self):
        return len(self.inputs)

    def embed_content(self, model, contents, config=None):
        self.inputs.append(contents)
        self.log.append(f"gemini-embed:{self.calls}")
        if self.failures:
            raise self.failures.pop(0)()
        texts = [contents] if isinstance(contents, str) else list(contents)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.25] * self.dims) for _ in texts])

    def install(self, monkeypatch):
        from app.services import embedding_service

        monkeypatch.setattr(embedding_service.client.models, "embed_content", self.embed_content)
        return self


class FakeJina:
    """httpx.post to Jina's embeddings endpoint, as embedding_fallback_jina makes it."""

    def __init__(self, log, dims=768):
        self.log = log
        self.dims = dims
        self.requests = []
        self.statuses = []

    def status(self, code, times=1):
        self.statuses.extend([code] * times)
        return self

    @property
    def calls(self):
        return len(self.requests)

    def post(self, url, headers=None, json=None, timeout=None):
        self.requests.append({"url": url, "json": json, "authorization": (headers or {}).get("Authorization")})
        self.log.append(f"jina:{self.calls}")
        request = httpx.Request("POST", url)
        if self.statuses:
            return httpx.Response(self.statuses.pop(0), json={"detail": "stub"}, request=request)
        # Out of order on purpose: _call_jina sorts by index.
        data = [{"index": i, "embedding": [0.75] * self.dims} for i in range(len(json["input"]))][::-1]
        return httpx.Response(200, json={"data": data}, request=request)

    def install(self, monkeypatch):
        from app.services import embedding_fallback_jina

        monkeypatch.setattr(embedding_fallback_jina.httpx, "post", self.post)
        return self
