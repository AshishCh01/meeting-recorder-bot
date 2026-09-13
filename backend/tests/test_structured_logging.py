"""
Phase B5 - structured log fields, and backend/app off print().

Part 1 (sections 1-4) added the fields and converted transcription_service;
part 2 (sections 5-7) converted everything else and widened the guard.

What this proves:

  1. The fields. Every line logged inside log_context() carries meeting_id and
     user_id, rendered as " meeting_id=... user_id=..." by the real format
     configure_logging installs; lines outside carry nothing; an explicit
     extra= wins; values put back on exit, including ones set inside.
  2. Isolation. A ThreadPoolExecutor reuses threads and does not copy
     context - the executor transcription path runs on exactly such a pool -
     so one meeting's fields must not reach the next task on the same thread.
     Concurrent asyncio tasks each keep their own.
  3. transcription_service. The real pipeline's lines all carry the meeting,
     and the owner once the row is read; failures log at ERROR with the
     traceback, degraded paths at WARNING, progress at INFO; lines logged
     from the worker's terminal-failure path and the executor's done-callback
     - both outside transcribe_recording - carry the fields too.
  4. No print() is left in transcription_service.py, counted by AST (a grep
     would also count docstrings and comments).
  5. Chat. Every line logged during a turn - chat_service, the Groq fallback,
     and the embedding call a search tool makes on another thread - carries
     the turn's meeting and user, at the right level; a caller that stops the
     stream early releases the session lock and leaves no fields behind.
  6. The transcription fallbacks (Sarvam, Jina) log under the transcription's
     fields, and each converted route line carries them explicitly.
  7. No print() anywhere in backend/app.

Providers are faked as in test_transcription_queue.py and
tests/fake_ai_providers.py. Postgres only.
"""
import ast
import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import observability
from app.config import settings
from app.db.models import Meeting
from app.observability import LogContextFilter, current_log_context, log_context, set_log_context
from app.services import transcription_service

from tests.fake_ai_providers import NON_TRANSIENT
from tests.test_transcription_queue import (  # noqa: F401 - fixtures
    TRANSCRIPT,
    full_pipeline,
    gemini_down_sarvam_up,
    stub_embeddings,
)

APP_DIR = Path(__file__).resolve().parent.parent / "app"
TRANSCRIPTION_LOGGER = "app.services.transcription_service"


class _Capture(logging.Handler):
    """Keeps records, with LogContextFilter applied exactly as the real handler has it."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []
        self.addFilter(LogContextFilter())

    def emit(self, record):
        self.records.append(record)

    def lines(self, logger_name=TRANSCRIPTION_LOGGER):
        formatter = logging.Formatter(observability._LOG_FORMAT)
        return [formatter.format(r) for r in self.records if r.name == logger_name]

    def of(self, logger_name=TRANSCRIPTION_LOGGER):
        return [r for r in self.records if r.name == logger_name]


@pytest.fixture
def captured():
    app_logger = logging.getLogger("app")
    handler = _Capture()
    previous_level = app_logger.level
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        app_logger.removeHandler(handler)
        app_logger.setLevel(previous_level)


@pytest.fixture(autouse=True)
def clean_log_context():
    assert current_log_context() == {"meeting_id": None, "user_id": None}, "a previous test leaked log context"
    yield
    assert current_log_context() == {"meeting_id": None, "user_id": None}, "this test leaked log context"


def make_meeting(db, user, **columns):
    meeting = Meeting(
        id=uuid.uuid4(), user_id=user.id, meeting_url="https://meet.google.com/abc-defg-hij",
        platform="google", status=columns.pop("status", "transcribing"), **columns,
    )
    db.add(meeting)
    db.commit()
    return meeting


# ---------------------------------------------------------------------------
# 1. The fields
# ---------------------------------------------------------------------------

def test_lines_inside_a_log_context_carry_the_fields_and_lines_outside_do_not(captured):
    log = logging.getLogger("app.test_structured")

    log.info("before")
    with log_context(meeting_id="m-1"):
        log.info("meeting only")
        set_log_context(user_id="u-7")
        log.info("both")
    log.info("after")

    rendered = [line.split("] ", 1)[1] for line in captured.lines("app.test_structured")]
    assert rendered == [
        "before",
        "meeting only meeting_id=m-1",
        "both meeting_id=m-1 user_id=u-7",
        "after",
    ]


def test_an_explicit_extra_wins_over_the_context(captured):
    log = logging.getLogger("app.test_structured")

    with log_context(meeting_id="from-context", user_id="u-1"):
        log.info("explicit", extra={"meeting_id": "from-extra"})

    [record] = captured.of("app.test_structured")
    assert (record.meeting_id, record.user_id) == ("from-extra", "u-1")
    assert record.log_fields == " meeting_id=from-extra user_id=u-1"


def test_log_context_restores_what_was_there_including_values_set_inside():
    with log_context(meeting_id="outer", user_id="outer-user"):
        with log_context(meeting_id="inner"):
            set_log_context(user_id="inner-user")
            assert current_log_context() == {"meeting_id": "inner", "user_id": "inner-user"}
        assert current_log_context() == {"meeting_id": "outer", "user_id": "outer-user"}
    assert current_log_context() == {"meeting_id": None, "user_id": None}


def test_log_context_restores_even_when_the_block_raises():
    with pytest.raises(RuntimeError):
        with log_context(meeting_id="m-1"):
            set_log_context(user_id="u-1")
            raise RuntimeError("boom")

    assert current_log_context() == {"meeting_id": None, "user_id": None}


def test_configure_logging_installs_the_filter_and_the_real_format(capsys):
    """Through the real root handler, not a copy of its setup."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        observability.configure_logging()
        observability.configure_logging()  # idempotent: one filter, not two
        [handler] = root.handlers
        assert sum(isinstance(f, LogContextFilter) for f in handler.filters) == 1

        log = logging.getLogger("app.test_structured")
        with log_context(meeting_id="m-42", user_id="u-9"):
            log.warning("inside")
        log.warning("outside")
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

    out = [line for line in capsys.readouterr().out.splitlines() if "app.test_structured" in line]
    assert out[0].endswith("[app.test_structured] inside meeting_id=m-42 user_id=u-9")
    assert out[1].endswith("[app.test_structured] outside")


# ---------------------------------------------------------------------------
# 2. Isolation
# ---------------------------------------------------------------------------

def test_a_reused_executor_thread_does_not_inherit_the_previous_tasks_fields():
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        def first_task():
            with log_context(meeting_id="meeting-A"):
                set_log_context(user_id="user-A")
                return current_log_context()

        inside = pool.submit(first_task).result()
        after = pool.submit(current_log_context).result()  # same thread, next task
    finally:
        pool.shutdown()

    assert inside == {"meeting_id": "meeting-A", "user_id": "user-A"}
    assert after == {"meeting_id": None, "user_id": None}, "the next task on the thread inherited meeting A"


def test_concurrent_asyncio_tasks_keep_their_own_fields(captured):
    log = logging.getLogger("app.test_structured")

    async def work(meeting_id):
        with log_context(meeting_id=meeting_id):
            for _ in range(3):
                await asyncio.sleep(0)
                # asyncio.to_thread copies the caller's context into the thread.
                await asyncio.to_thread(log.info, f"working on {meeting_id}")

    async def main():
        await asyncio.gather(work("m-1"), work("m-2"))

    asyncio.run(main())

    records = captured.of("app.test_structured")
    assert len(records) == 6
    for record in records:
        assert record.getMessage() == f"working on {record.meeting_id}", "a line carried another task's meeting"


# ---------------------------------------------------------------------------
# 3. transcription_service
# ---------------------------------------------------------------------------

def test_every_transcription_line_carries_the_meeting_and_then_its_owner(db, user, full_pipeline, stub_embeddings, captured):
    meeting = make_meeting(db, user)

    transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    records = captured.of()
    assert records, "the pipeline logged nothing"
    assert all(r.meeting_id == str(meeting.id) for r in records), "a line was logged without its meeting"
    first, rest = records[0], records[1:]
    assert first.getMessage() == "[transcription] starting, path: recordings/x.m4a"
    assert first.user_id is None, "the owner is only known once the row is read"
    assert rest and all(r.user_id == str(user.id) for r in rest)
    assert {r.levelno for r in records} == {logging.INFO}, "a successful run logged above INFO"
    assert any(line.endswith(f"meeting_id={meeting.id} user_id={user.id}") for line in captured.lines())


def test_a_run_leaves_no_fields_behind_for_the_next_one(db, user, full_pipeline, stub_embeddings):
    """The executor path, for real: two transcriptions on one reused thread."""
    first, second = make_meeting(db, user), make_meeting(db, user)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        pool.submit(transcription_service.transcribe_recording, str(first.id), "recordings/x.m4a").result()
        between = pool.submit(current_log_context).result()
        pool.submit(transcription_service.transcribe_recording, str(second.id), "recordings/x.m4a").result()
    finally:
        pool.shutdown()

    assert between == {"meeting_id": None, "user_id": None}


def test_a_non_transient_gemini_failure_logs_an_error_with_the_fields(db, user, full_pipeline, captured, monkeypatch):
    meeting = make_meeting(db, user)

    def rejected(contents, config):
        raise NON_TRANSIENT["400"]()

    monkeypatch.setattr(transcription_service, "_call_gemini_with_retry", rejected)

    transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    [marked] = [r for r in captured.of() if r.getMessage().startswith("[transcription] marking failed")]
    assert marked.levelno == logging.ERROR
    assert (marked.meeting_id, marked.user_id) == (str(meeting.id), str(user.id))


def test_the_sarvam_fallback_logs_warnings_not_errors(db, user, gemini_down_sarvam_up, stub_embeddings, captured):
    meeting = make_meeting(db, user)

    transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    warnings = [r for r in captured.of() if r.levelno == logging.WARNING]
    messages = [r.getMessage() for r in warnings]
    assert sum("retrying in" in m for m in messages) == 3, "one warning per Gemini backoff"
    assert "[transcription] Gemini 503 persisted after retries, falling back to Sarvam AI" in messages
    assert not [r for r in captured.of() if r.levelno >= logging.ERROR], "a fallback that worked logged an error"
    assert all(r.user_id == str(user.id) for r in warnings)


def test_an_unexpected_failure_logs_the_traceback(db, user, full_pipeline, captured, monkeypatch):
    meeting = make_meeting(db, user)

    def broken(contents, config):
        raise KeyError("candidates")

    monkeypatch.setattr(transcription_service, "_call_gemini_with_retry", broken)

    transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    [failure] = [r for r in captured.of() if r.getMessage().startswith("[transcription] failed")]
    assert failure.levelno == logging.ERROR
    assert failure.exc_info is not None and failure.exc_info[0] is KeyError, "the traceback was dropped"
    assert failure.meeting_id == str(meeting.id)


def test_the_workers_terminal_failure_lines_carry_the_fields(db, user, captured):
    """Called after transcribe_recording has returned - it binds the fields itself."""
    kept = make_meeting(db, user, status="completed", transcript=TRANSCRIPT)
    failed = make_meeting(db, user)

    transcription_service.record_terminal_failure(str(kept.id), RuntimeError("embedding provider down"))
    transcription_service.record_terminal_failure(str(failed.id), RuntimeError("storage unreachable"))

    kept_line, failed_line = captured.of()
    assert (kept_line.levelno, kept_line.meeting_id, kept_line.user_id) == (logging.WARNING, str(kept.id), str(user.id))
    assert (failed_line.levelno, failed_line.meeting_id, failed_line.user_id) == (logging.ERROR, str(failed.id), str(user.id))


def test_the_executor_done_callback_logs_the_crash_with_its_traceback(db, user, captured, monkeypatch):
    meeting = make_meeting(db, user)

    def crashes(meeting_id, storage_path):
        raise OSError("disk full")

    monkeypatch.setattr(transcription_service, "transcribe_recording", crashes)
    monkeypatch.setattr(settings, "transcription_use_queue", False)

    future = transcription_service.submit_transcription(str(meeting.id), "recordings/x.m4a")
    with pytest.raises(OSError):
        future.result(timeout=10)
    transcription_service.transcription_executor.submit(lambda: None).result(timeout=10)  # let the callback finish

    [crash] = [r for r in captured.of() if "unhandled exception in background task" in r.getMessage()]
    assert crash.levelno == logging.ERROR
    assert crash.exc_info[0] is OSError
    assert crash.meeting_id == str(meeting.id)


def test_the_queued_line_carries_the_meeting(db, user, captured, monkeypatch):
    meeting = make_meeting(db, user)
    monkeypatch.setattr(settings, "transcription_use_queue", True)

    def fake_run_blocking(coro):
        coro.close()  # never awaited: no Redis in this test
        return SimpleNamespace(job_id="job-123")

    monkeypatch.setattr(transcription_service, "_run_blocking", fake_run_blocking)

    transcription_service.submit_transcription(str(meeting.id), "recordings/x.m4a")

    [queued] = captured.of()
    assert queued.getMessage() == "[transcription] queued as job job-123"
    assert queued.meeting_id == str(meeting.id)


# ---------------------------------------------------------------------------
# 4. No print() left
# ---------------------------------------------------------------------------

def print_calls(path: Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
    ]


def test_transcription_service_has_no_print_calls():
    assert print_calls(APP_DIR / "services" / "transcription_service.py") == []


# ---------------------------------------------------------------------------
# 5. Chat
# ---------------------------------------------------------------------------

from app.rag import chat_service  # noqa: E402
from tests.fake_ai_providers import TRANSIENT, FakeGeminiEmbed, FakeJina, call, install_fast_sleeps  # noqa: E402
from tests.test_chat_fallback_ladder import ask, chat  # noqa: E402,F401 - chat is a fixture
from tests.test_chat_stream_unhandled_errors import logged_in  # noqa: E402,F401 - fixture

CHAT_LOGGERS = ("app.rag.chat_service", "app.rag.chat_fallback_groq", "app.services.embedding_service")


def records_from(captured, names):
    return [r for r in captured.records if r.name in names]


def test_every_line_of_a_chat_turn_carries_its_meeting_and_user(chat, captured, monkeypatch):
    """
    Gemini fails, Groq retries once and answers. Lines come from chat_service
    and from chat_fallback_groq, and none of them was given an id directly.
    """
    chat.gemini.fail(TRANSIENT["503"], times=2)
    chat.groq.status(503).answer("The launch is on the 14th.")

    ask(chat, "When is the launch?")

    records = records_from(captured, CHAT_LOGGERS)
    assert {r.name for r in records} == {"app.rag.chat_service", "app.rag.chat_fallback_groq"}
    assert all((r.meeting_id, r.user_id) == (chat.meeting_id, chat.user_id) for r in records)
    assert all(r.levelno == logging.WARNING for r in records), "a turn that was answered logged above WARNING"
    assert any(r.getMessage() == "[chat] Gemini 503 persisted after retries, falling back to Groq" for r in records)


def test_a_search_tool_on_another_thread_logs_under_the_turns_fields(chat, captured, monkeypatch):
    """asyncio.to_thread copies the context, so the embedding retry line is attributed too."""
    FakeGeminiEmbed([]).fail(TRANSIENT["429"]).install(monkeypatch)
    chat.gemini.reply(call("_search_transcript", query="launch date"))
    chat.gemini.reply("The launch is on the 14th.")

    ask(chat, "When is the launch?")

    [retry] = records_from(captured, ("app.services.embedding_service",))
    assert retry.getMessage().startswith("[embedding] Gemini 429, retrying in")
    assert (retry.meeting_id, retry.user_id) == (chat.meeting_id, chat.user_id)


def test_groq_failing_too_logs_an_error_with_the_traceback(chat, captured):
    chat.gemini.fail(TRANSIENT["503"], times=2)
    chat.groq.status(400)

    ask(chat, "When is the launch?")

    [error] = [r for r in records_from(captured, CHAT_LOGGERS) if r.levelno >= logging.ERROR]
    assert error.getMessage().startswith("[chat] Groq fallback also failed")
    assert error.exc_info is not None, "the traceback was dropped"
    assert (error.meeting_id, error.user_id) == (chat.meeting_id, chat.user_id)


def test_stopping_a_stream_early_releases_the_session_and_the_fields(chat):
    """
    A client disconnecting mid-answer closes the stream. The session lock the
    turn holds must be released right then - the user's next question would
    otherwise wait on it - and the log fields must not outlive the turn.
    """
    chat.gemini.reply("The launch ", "is on the 14th.")
    chat.gemini.reply("It was moved from the 7th.")

    async def run():
        stream = chat_service.ask_question_stream(chat.meeting_id, "When is the launch?", None, chat.user_id)
        first = await stream.__anext__()
        assert current_log_context() == {"meeting_id": chat.meeting_id, "user_id": chat.user_id}
        key = f"{chat.user_id}:{chat.meeting_id}:meeting-{chat.meeting_id}"
        session_lock = chat_service._session_cache.cache[key][0]
        assert session_lock.locked()
        await stream.aclose()
        # Checked before anything else gets to run on the loop. Left to
        # garbage collection, the inner generator's cleanup would only be
        # scheduled here, and the lock would still be held at this point.
        released_by_close = not session_lock.locked()
        after_close = current_log_context()

        answered = []
        async def next_question():
            async for event in chat_service.ask_question_stream(chat.meeting_id, "Was it moved?", None, chat.user_id):
                answered.append(event)

        await asyncio.wait_for(next_question(), timeout=5)
        return first, released_by_close, after_close, answered

    first, released_by_close, after_close, answered = asyncio.run(run())

    assert first == {"type": "delta", "text": "The launch "}
    assert released_by_close, "closing the stream did not release the session lock - only garbage collection would"
    assert after_close == {"meeting_id": None, "user_id": None}, "the fields outlived a stream that was closed early"
    assert answered[-1]["type"] == "done", "the next question could not get the session"


# ---------------------------------------------------------------------------
# 6. The transcription fallbacks, and the routes
# ---------------------------------------------------------------------------

def test_the_sarvam_fallback_lines_carry_the_transcriptions_fields(db, user, gemini_down_sarvam_up, stub_embeddings, captured):
    meeting = make_meeting(db, user)

    transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    sarvam = records_from(captured, ("app.services.transcription_fallback_sarvam",))
    assert [r.getMessage() for r in sarvam] == [
        "[transcription_fallback] starting Sarvam AI fallback transcription",
        "[transcription_fallback] Sarvam AI fallback completed successfully",
    ]
    assert all((r.meeting_id, r.user_id) == (str(meeting.id), str(user.id)) for r in sarvam)


def test_the_jina_fallback_lines_carry_the_transcriptions_fields(db, user, full_pipeline, captured, monkeypatch):
    monkeypatch.setattr(settings, "jina_api_key", "stub-jina-key")
    install_fast_sleeps(monkeypatch)
    FakeGeminiEmbed([]).fail(TRANSIENT["503"], times=4).install(monkeypatch)
    FakeJina([]).status(503).install(monkeypatch)
    meeting = make_meeting(db, user)

    transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    lines = records_from(captured, ("app.services.embedding_service", "app.services.embedding_fallback_jina"))
    assert [r.name for r in lines].count("app.services.embedding_fallback_jina") == 1
    assert any(r.getMessage() == "[embedding] Gemini 503 persisted after retries, falling back to Jina AI" for r in lines)
    assert all((r.meeting_id, r.user_id, r.levelno) == (str(meeting.id), str(user.id), logging.WARNING) for r in lines)


def test_the_webhook_lines_carry_the_payloads_meeting_and_user(db, user, captured, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api import webhooks
    from app.main import app

    meeting = make_meeting(db, user, status="recording")
    body = {
        "user_id": str(user.id), "meeting_id": str(meeting.id), "status": "completed",
        "recording_path": f"{user.id}/{meeting.id}/recording.m4a",
    }
    headers = {"Authorization": "Bearer stub-token"}

    def missing(path, *args, **kwargs):
        raise RuntimeError("Object not found")

    monkeypatch.setattr(webhooks, "get_signed_recording_url", missing)
    TestClient(app).post("/webhooks/recording-complete", json=body, headers=headers)

    other = make_meeting(db, user, status="recording")
    monkeypatch.setattr(webhooks, "get_signed_recording_url", lambda path, *a, **k: f"https://signed/{path}")

    def redis_down(meeting_id, storage_path):
        raise ConnectionError("Error connecting to Redis")

    monkeypatch.setattr(webhooks, "submit_transcription", redis_down)
    TestClient(app, raise_server_exceptions=False).post(
        "/webhooks/recording-complete", json={**body, "meeting_id": str(other.id)}, headers=headers,
    )

    signed, queued = records_from(captured, ("app.api.webhooks",))
    assert (signed.levelno, signed.meeting_id, signed.user_id) == (logging.WARNING, str(meeting.id), str(user.id))
    assert (queued.levelno, queued.meeting_id, queued.user_id) == (logging.ERROR, str(other.id), str(user.id))
    assert queued.exc_info is not None


def test_the_meetings_route_lines_carry_the_meeting_and_user(db, user, captured, monkeypatch):
    from fastapi import HTTPException
    from app.api import meetings

    class _StorageDown:
        @property
        def storage(self):
            return self

        def from_(self, bucket):
            return self

        def remove(self, paths):
            raise RuntimeError("storage unavailable")

    pdf_meeting = make_meeting(db, user, status="completed", transcript=TRANSCRIPT)
    deleted = make_meeting(db, user, status="completed")
    monkeypatch.setattr(meetings, "supabase", _StorageDown())

    def pdf_broken(meeting):
        raise ValueError("bad font")

    monkeypatch.setattr(meetings, "generate_meeting_pdf", pdf_broken)

    with pytest.raises(HTTPException):
        meetings.export_meeting_pdf(meeting_id=pdf_meeting.id, db=db, user_id=str(user.id))
    meetings.delete_meeting(meeting_id=deleted.id, db=db, user_id=str(user.id))

    pdf, storage = records_from(captured, ("app.api.meetings",))
    assert (pdf.levelno, pdf.meeting_id, pdf.user_id) == (logging.ERROR, str(pdf_meeting.id), str(user.id))
    assert pdf.exc_info is not None and pdf.exc_info[0] is ValueError
    assert (storage.levelno, storage.meeting_id, storage.user_id) == (logging.WARNING, str(deleted.id), str(user.id))


def test_the_chat_route_logs_a_stream_failure_with_its_traceback_and_fields(chat, logged_in, captured, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api import chat as chat_api
    from app.main import app

    async def explodes(meeting_id, question, session_id=None, user_id=None):
        raise RuntimeError("stream exploded")
        yield  # an async generator

    monkeypatch.setattr(chat_api, "ask_question_stream", explodes)

    response = TestClient(app).post(
        f"/meetings/{chat.meeting_id}/chat/stream", json={"question": "hi"}, headers=logged_in,
    )

    assert '"type": "error"' in response.text
    [failure] = records_from(captured, ("app.api.chat",))
    assert (failure.levelno, failure.meeting_id, failure.user_id) == (logging.ERROR, chat.meeting_id, chat.user_id)
    assert failure.exc_info[0] is RuntimeError


# ---------------------------------------------------------------------------
# 7. No print() anywhere in backend/app
# ---------------------------------------------------------------------------

def test_backend_app_has_no_print_calls():
    offenders = {
        str(path.relative_to(APP_DIR)): lines
        for path in sorted(APP_DIR.rglob("*.py"))
        if (lines := print_calls(path))
    }
    assert offenders == {}, f"print() calls in backend/app (use logger.* - docs/scaling-plan.md B5): {offenders}"
