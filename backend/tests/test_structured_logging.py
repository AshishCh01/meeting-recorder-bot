"""
Phase B5, part 1 - structured log fields, and transcription_service off print().

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

Providers are faked as in test_transcription_queue.py. Postgres only.
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
