"""
Phase A3 - transcription moves to a Redis + arq queue.

Transcription used to run in a ThreadPoolExecutor inside the API process, so
every deploy killed in-flight jobs: the meeting sat in "transcribing" until
the watchdog TTL failed it, and the user saw a failed recording they did not
cause. These tests cover the four properties from docs/scaling-plan.md's Gate:

  1. Durability - a job enqueued with no worker running survives the API
     process going away, and runs when a worker appears.
  2. Atomic re-index - a crash between index_transcript's delete and its
     insert leaves the meeting's chunks intact, not zero.
  3. Retries do not re-transcribe - a retry over a meeting that already has a
     transcript never calls Gemini.
  4. Exhausted retries write a terminal state with a useful error_message.

Needs Redis as well as Postgres - see tests/README.md.
"""
import asyncio
import json
import os
import uuid

import pytest
from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import Retry
from sqlalchemy import text

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting, MeetingChunk
from app.services import embedding_service, transcription_service
from app import worker as worker_module
from app.worker import WorkerSettings, transcribe_job

REDIS_URL = os.environ.get("TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(
    not REDIS_URL,
    reason="TEST_REDIS_URL is not set - see tests/README.md",
)

EMBED_DIM = 768


def redis_settings():
    return RedisSettings.from_dsn(REDIS_URL)


@pytest.fixture(autouse=True)
def point_at_test_redis(monkeypatch):
    """Every enqueue in this module goes to the throwaway Redis, never .env's."""
    monkeypatch.setattr(settings, "redis_url", REDIS_URL)


@pytest.fixture(autouse=True)
def flush_redis():
    async def _flush():
        pool = await create_pool(redis_settings())
        try:
            await pool.flushdb()
        finally:
            await pool.aclose()

    asyncio.run(_flush())
    yield
    asyncio.run(_flush())


TRANSCRIPT = {
    "title": "Quarterly planning",
    "summary": "The team walked through next quarter's roadmap.",
    "key_points": ["Roadmap agreed"],
    "action_items": [],
    "conclusion": "Ship it.",
    "conversation": [
        {"text": "Priya - Let's start with the roadmap.", "timestamp_start": "00:00", "timestamp_end": "00:04"},
        {"text": "Rohan - Agreed, I'll take the API work.", "timestamp_start": "00:05", "timestamp_end": "00:09"},
        {"text": "Priya - Then we ship at the end of the month.", "timestamp_start": "00:10", "timestamp_end": "00:14"},
    ],
}


def make_meeting(db, user_id, *, status="transcribing", transcript=None, embedding_provider=None):
    meeting = Meeting(
        id=uuid.uuid4(),
        user_id=user_id,
        meeting_url="https://meet.google.com/abc-defg-hij",
        title="Quarterly planning",
        platform="google",
        status=status,
        transcript=transcript,
        embedding_provider=embedding_provider,
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return meeting


def seed_chunks(db, meeting_id, count=2, provider="gemini"):
    """Chunks as a previous successful indexing run would have left them."""
    db.add_all([
        MeetingChunk(
            meeting_id=meeting_id,
            chunk_index=i,
            content=f"pre-existing chunk {i}",
            speakers=["Priya"],
            timestamp_start="00:00",
            timestamp_end="00:10",
            embedding=[0.25] * EMBED_DIM,
        )
        for i in range(count)
    ])
    db.query(Meeting).filter(Meeting.id == meeting_id).update({"embedding_provider": provider})
    db.commit()


def chunk_count(meeting_id):
    """Counts on a fresh connection - never the session under test."""
    other = SessionLocal()
    try:
        return other.query(MeetingChunk).filter(MeetingChunk.meeting_id == meeting_id).count()
    finally:
        other.close()


def reread(meeting_id):
    other = SessionLocal()
    try:
        return other.query(Meeting).filter(Meeting.id == meeting_id).one()
    finally:
        other.close()


@pytest.fixture
def stub_embeddings(monkeypatch):
    """Deterministic vectors - no Gemini, no Jina, no network."""
    calls = []

    def _embed_documents(texts):
        calls.append(list(texts))
        return [[0.5] * EMBED_DIM for _ in texts], embedding_service.GEMINI_PROVIDER

    monkeypatch.setattr(embedding_service, "_embed_documents", _embed_documents)
    return calls


@pytest.fixture
def gemini_tripwire(monkeypatch):
    """
    Every route out of transcribe_recording that costs money, wired to
    explode. Nothing here should ever be reached on a resumed job - that is
    the assertion, not a safety net.
    """
    def boom(*args, **kwargs):
        raise AssertionError("re-transcribed a meeting that already had a transcript")

    monkeypatch.setattr(transcription_service.client.files, "upload", boom)
    monkeypatch.setattr(transcription_service, "_call_gemini_with_retry", boom)
    # supabase.storage is a read-only property on SyncClient, so the whole
    # client is swapped rather than one attribute of it.
    monkeypatch.setattr(transcription_service, "supabase", _ExplodingSupabase())
    return boom


class _ExplodingSupabase:
    @property
    def storage(self):
        raise AssertionError("re-downloaded a recording for a meeting that already had a transcript")


# ---------------------------------------------------------------------------
# Gate 1 - durability across a restart
# ---------------------------------------------------------------------------

def test_job_survives_with_no_worker_running_and_runs_when_one_starts(db, user, monkeypatch):
    """
    The property the ThreadPoolExecutor cannot provide, and the whole point of
    the phase. An executor submit lives in the process; a queued job lives in
    Redis.
    """
    monkeypatch.setattr(settings, "transcription_use_queue", True)
    meeting = make_meeting(db, user.id)
    meeting_id = str(meeting.id)

    # The API enqueues. No worker exists yet.
    job = transcription_service.submit_transcription(meeting_id, "recordings/x.m4a")
    assert job is not None

    async def queued():
        pool = await create_pool(redis_settings())
        try:
            return await pool.queued_jobs()
        finally:
            await pool.aclose()

    # "The API restarted": every connection the enqueue used is closed, and
    # this is a brand-new client against the same Redis.
    pending = asyncio.run(queued())
    assert len(pending) == 1, f"job did not persist: {pending}"
    assert pending[0].function == "transcribe_job"
    assert pending[0].args == (meeting_id, "recordings/x.m4a")

    # Now a worker comes up. burst=True drains what is pending and exits.
    ran = []
    # app/worker.py binds transcribe_recording at import time, so the patch
    # has to land on the worker module, not on the service it came from.
    monkeypatch.setattr(
        worker_module, "transcribe_recording",
        lambda mid, path: ran.append((mid, path)),
    )

    async def drain():
        from arq.worker import Worker
        worker = Worker(
            functions=[transcribe_job],
            redis_settings=redis_settings(),
            burst=True,
            max_tries=settings.transcription_max_tries,
            poll_delay=0.05,
        )
        await worker.async_run()
        await worker.close()

    asyncio.run(drain())

    assert ran == [(meeting_id, "recordings/x.m4a")]
    assert asyncio.run(queued()) == []


def test_executor_path_is_untouched_while_the_flag_is_off(db, user, monkeypatch):
    """
    Deploy 1: Redis and the worker are running, but submit_transcription still
    uses the executor and nothing reaches the queue.
    """
    monkeypatch.setattr(settings, "transcription_use_queue", False)
    submitted = []
    monkeypatch.setattr(
        transcription_service, "transcribe_recording",
        lambda mid, path: submitted.append((mid, path)),
    )

    meeting = make_meeting(db, user.id)
    future = transcription_service.submit_transcription(str(meeting.id), "recordings/x.m4a")
    future.result(timeout=10)

    assert submitted == [(str(meeting.id), "recordings/x.m4a")]

    async def queued():
        pool = await create_pool(redis_settings())
        try:
            return await pool.queued_jobs()
        finally:
            await pool.aclose()

    assert asyncio.run(queued()) == [], "the flag is off but something was enqueued"


# ---------------------------------------------------------------------------
# Gate 2 - the re-index is one transaction
# ---------------------------------------------------------------------------

def test_crash_between_delete_and_insert_leaves_chunks_intact(db, user, stub_embeddings):
    """
    A worker killed mid-index must not leave a meeting with zero chunks.

    Asserted on the chunk count and embedding_provider, never on status:
    status reads "completed" the whole way through - transcribe_recording sets
    it before it ever calls index_transcript - which is precisely what makes
    this failure invisible. The UI shows a finished meeting while RAG returns
    nothing.
    """
    meeting = make_meeting(db, user.id, status="completed", transcript=TRANSCRIPT)
    meeting_id = meeting.id
    seed_chunks(db, meeting_id, count=2, provider="gemini")
    assert chunk_count(meeting_id) == 2

    # The fault lands after the delete has been issued and before the insert -
    # exactly the window an arq shutdown, a job timeout or an OOM kill hits.
    def killed(*args, **kwargs):
        raise RuntimeError("worker killed mid-index")

    db.add_all = killed

    with pytest.raises(RuntimeError, match="worker killed mid-index"):
        embedding_service.index_transcript(db, str(meeting_id), TRANSCRIPT)

    # What the worker's `finally: db.close()` does to an open transaction.
    db.rollback()

    assert chunk_count(meeting_id) == 2, "the delete committed on its own - chunks were lost"
    assert reread(meeting_id).embedding_provider == "gemini"


def test_successful_reindex_replaces_chunks_in_one_transaction(db, user, stub_embeddings):
    """The happy path still replaces rather than duplicates."""
    meeting = make_meeting(db, user.id, status="completed", transcript=TRANSCRIPT)
    meeting_id = meeting.id
    seed_chunks(db, meeting_id, count=5, provider="jina")

    embedding_service.index_transcript(db, str(meeting_id), TRANSCRIPT)

    assert chunk_count(meeting_id) > 0
    other = SessionLocal()
    try:
        contents = [
            c.content for c in
            other.query(MeetingChunk).filter(MeetingChunk.meeting_id == meeting_id).all()
        ]
    finally:
        other.close()
    assert not any(c.startswith("pre-existing") for c in contents), "old chunks survived the re-index"
    assert reread(meeting_id).embedding_provider == "gemini"


def test_zero_chunk_meetings_are_findable_by_the_repair_query(db, user):
    """
    The free detector the plan calls out: embedding_provider is written in the
    same commit as the chunks, so its absence on a completed meeting is an
    exact marker for "transcribed but not indexed".
    """
    good = make_meeting(db, user.id, status="completed", transcript=TRANSCRIPT, embedding_provider="gemini")
    broken = make_meeting(db, user.id, status="completed", transcript=TRANSCRIPT)

    rows = db.execute(text(
        "SELECT id FROM meetings WHERE status = 'completed' AND embedding_provider IS NULL"
    )).scalars().all()

    assert broken.id in rows
    assert good.id not in rows


# ---------------------------------------------------------------------------
# Gate 3 - a retry must not re-transcribe
# ---------------------------------------------------------------------------

def test_retry_over_an_existing_transcript_never_calls_gemini(db, user, stub_embeddings, gemini_tripwire):
    """
    The cost lever. Gemini upload, generate_content and the Supabase download
    are all stubbed to raise - reaching any of them fails the test outright.
    """
    meeting = make_meeting(db, user.id, status="completed", transcript=TRANSCRIPT)
    meeting_id = meeting.id

    result = transcription_service.transcribe_recording(str(meeting_id), "recordings/x.m4a")

    assert result == TRANSCRIPT
    assert len(stub_embeddings) == 1, "resumed job should have indexed exactly once"
    assert chunk_count(meeting_id) > 0
    assert reread(meeting_id).embedding_provider == "gemini"


def test_a_fully_finished_meeting_short_circuits_entirely(db, user, stub_embeddings, gemini_tripwire):
    """Transcribed and indexed already - a duplicate job is a no-op, not a re-embed."""
    meeting = make_meeting(
        db, user.id, status="completed", transcript=TRANSCRIPT, embedding_provider="gemini",
    )

    result = transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    assert result == TRANSCRIPT
    assert stub_embeddings == [], "re-embedded a meeting that was already indexed"


def test_a_meeting_with_no_transcript_runs_the_full_pipeline(db, user):
    """The resume check must not swallow real work."""
    meeting = make_meeting(db, user.id, status="transcribing")

    other = SessionLocal()
    try:
        assert transcription_service._resume_if_work_already_done(
            other, str(meeting.id)
        ) is transcription_service._NOT_RESUMABLE
    finally:
        other.close()


def test_worker_retry_resumes_instead_of_restarting(db, user, stub_embeddings, gemini_tripwire):
    """
    End to end through the arq task: attempt 1 fails during indexing, attempt 2
    picks up the stored transcript and only re-indexes.
    """
    meeting = make_meeting(db, user.id, status="completed", transcript=TRANSCRIPT)
    meeting_id = meeting.id

    attempts = {"n": 0}
    real_index = embedding_service.index_transcript

    def flaky_index(db_, mid, transcript):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient embedding provider error")
        return real_index(db_, mid, transcript)

    transcription_service.index_transcript = flaky_index
    try:
        # arq's Retry, carrying the IndexingFailed - the same run through a
        # real worker is Gate 5's first test.
        with pytest.raises(Retry) as exc:
            asyncio.run(transcribe_job({"job_try": 1}, str(meeting_id), "recordings/x.m4a"))
        assert isinstance(exc.value.__cause__, transcription_service.IndexingFailed)

        transcription_service.index_transcript = real_index
        asyncio.run(transcribe_job({"job_try": 2}, str(meeting_id), "recordings/x.m4a"))
    finally:
        transcription_service.index_transcript = real_index

    assert chunk_count(meeting_id) > 0
    assert reread(meeting_id).embedding_provider == "gemini"


# ---------------------------------------------------------------------------
# Gate 4 - exhausted retries write a terminal state
# ---------------------------------------------------------------------------

def test_exhausted_retries_mark_a_transcript_less_meeting_failed(db, user, monkeypatch):
    monkeypatch.setattr(settings, "transcription_max_tries", 3)
    meeting = make_meeting(db, user.id, status="transcribing")

    def boom(mid, path):
        raise RuntimeError("storage unreachable")

    monkeypatch.setattr(worker_module, "transcribe_recording", boom)

    # Not the last attempt: hand arq a Retry, and write nothing terminal.
    with pytest.raises(Retry) as exc:
        asyncio.run(transcribe_job({"job_try": 1}, str(meeting.id), "recordings/x.m4a"))
    assert "storage unreachable" in str(exc.value.__cause__)
    assert reread(meeting.id).status == "transcribing"

    # Last attempt: the meeting must not be left non-terminal.
    asyncio.run(transcribe_job({"job_try": 3}, str(meeting.id), "recordings/x.m4a"))

    row = reread(meeting.id)
    assert row.status == "failed"
    assert "storage unreachable" in row.error_message
    assert "repeated attempts" in row.error_message


def test_exhausted_indexing_retries_keep_the_meeting_completed(db, user, monkeypatch):
    """
    A meeting that transcribed fine and only failed to index has a usable
    transcript, summary and action items. Marking it failed would take a
    working recording away from the user over a RAG problem - so it keeps
    "completed" and records the note the old inline handler wrote immediately,
    staying findable via embedding_provider IS NULL.
    """
    monkeypatch.setattr(settings, "transcription_max_tries", 2)
    meeting = make_meeting(db, user.id, status="completed", transcript=TRANSCRIPT)

    def boom(mid, path):
        raise transcription_service.IndexingFailed("pgvector connection dropped")

    monkeypatch.setattr(worker_module, "transcribe_recording", boom)

    asyncio.run(transcribe_job({"job_try": 2}, str(meeting.id), "recordings/x.m4a"))

    row = reread(meeting.id)
    assert row.status == "completed", "a good transcript was thrown away over an indexing failure"
    assert "RAG indexing failed" in row.error_message
    assert row.embedding_provider is None  # still visible to the repair query


def test_resumed_indexing_failure_keeps_the_meeting_completed(db, user, gemini_tripwire):
    """
    The resume path's half of the IndexingFailed contract: a retry that only
    re-indexes must surface the failure to the queue without touching the
    meeting's state. (The first-run path is covered separately below - it is
    the one that needs the dedicated re-raise clause, because it fails from
    inside transcribe_recording's broad `except Exception`.)
    """
    meeting = make_meeting(db, user.id, status="completed", transcript=TRANSCRIPT)

    def boom(db_, mid, transcript):
        raise RuntimeError("embedding provider down")

    real_index = transcription_service.index_transcript
    transcription_service.index_transcript = boom
    try:
        with pytest.raises(transcription_service.IndexingFailed):
            transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")
    finally:
        transcription_service.index_transcript = real_index

    row = reread(meeting.id)
    assert row.status == "completed", "an indexing failure marked the whole meeting failed"
    assert row.transcript == TRANSCRIPT


def test_worker_settings_are_wired_to_the_task():
    """A typo here means jobs enqueue forever and never run."""
    assert transcribe_job in WorkerSettings.functions
    assert WorkerSettings.max_tries == settings.transcription_max_tries
    assert WorkerSettings.job_timeout == settings.transcription_job_timeout_seconds
    assert transcribe_job.__name__ == "transcribe_job"


class _FakeUploadedFile:
    """Enough of a Gemini File API handle for transcribe_recording's poll loop."""

    def __init__(self):
        self.uri = "gemini://file/fake"
        self.name = "files/fake"
        self.state = type("S", (), {"name": "ACTIVE"})()


class _FakeUsage:
    prompt_token_count = 100
    candidates_token_count = 50


@pytest.fixture
def full_pipeline(monkeypatch):
    """
    Stubs the whole external surface of transcribe_recording so the *first
    run* path can be exercised end to end: download, silence check, Gemini
    upload, poll and generate. Nothing here reaches the network.
    """
    class _Storage:
        def from_(self, bucket):
            return self

        def download(self, path):
            return b"fake audio bytes"

    monkeypatch.setattr(transcription_service, "supabase", type("C", (), {"storage": _Storage()})())
    monkeypatch.setattr(transcription_service, "_is_audio_silent", lambda p: False)
    monkeypatch.setattr(transcription_service, "_get_audio_duration_seconds", lambda p: 42.0)

    uploaded = _FakeUploadedFile()
    monkeypatch.setattr(transcription_service.client.files, "upload", lambda **kw: uploaded)
    monkeypatch.setattr(transcription_service.client.files, "get", lambda **kw: uploaded)
    monkeypatch.setattr(transcription_service.client.files, "delete", lambda **kw: None)

    response = type("R", (), {
        "text": json.dumps(TRANSCRIPT),
        "usage_metadata": _FakeUsage(),
    })()
    monkeypatch.setattr(
        transcription_service, "_call_gemini_with_retry",
        lambda contents, config: response,
    )
    return response


def test_first_run_indexing_failure_keeps_the_meeting_completed(db, user, full_pipeline, monkeypatch):
    """
    The regression the IndexingFailed type exists to prevent, on the path that
    actually needs it: a *first* run, where the transcript is saved and the
    meeting set to "completed" before index_transcript is called.

    Letting the raw exception propagate from there would land in
    transcribe_recording's broad `except Exception`, which calls _mark_failed -
    throwing away a good transcript, summary and action items over a RAG
    problem. IndexingFailed plus its dedicated re-raise clause is what carries
    the failure out to the queue instead.
    """
    meeting = make_meeting(db, user.id, status="transcribing")

    def boom(db_, mid, transcript):
        raise RuntimeError("embedding provider down")

    monkeypatch.setattr(transcription_service, "index_transcript", boom)

    with pytest.raises(transcription_service.IndexingFailed, match="embedding provider down"):
        transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    row = reread(meeting.id)
    assert row.status == "completed", "an indexing failure marked the whole meeting failed"
    assert row.transcript == TRANSCRIPT
    assert row.embedding_provider is None


def test_first_run_success_transcribes_and_indexes(db, user, full_pipeline, stub_embeddings):
    """The ordinary path still works with the resume check in front of it."""
    meeting = make_meeting(db, user.id, status="transcribing")

    result = transcription_service.transcribe_recording(str(meeting.id), "recordings/x.m4a")

    assert result == TRANSCRIPT
    assert len(stub_embeddings) == 1
    row = reread(meeting.id)
    assert row.status == "completed"
    assert row.embedding_provider == "gemini"
    assert chunk_count(meeting.id) > 0


# ---------------------------------------------------------------------------
# Gate 5 - retries happen in a real worker, not only in a direct call
# ---------------------------------------------------------------------------
#
# The Gate 3/4 tests above drive transcribe_job with a hand-built
# {"job_try": n}, which assumes arq will call it again with n + 1. arq 0.26
# only re-runs a job that raised arq.worker.Retry, RetryJob or CancelledError;
# any other exception fails the job on the spot (Worker.run_job). These run the
# task inside a real Worker, so the retry has to actually happen.

class _RealWorkerRun:
    """One burst-mode worker over whatever is queued; records what arq counted."""

    def __init__(self, monkeypatch, retry_delay_seconds=0):
        # Burst mode waits for deferred jobs, so the delay is pinned short
        # here; the deferral itself is asserted in its own test below.
        monkeypatch.setattr(settings, "transcription_retry_delay_seconds", retry_delay_seconds)
        self.terminal_calls = []
        real_terminal = worker_module.record_terminal_failure

        def spy(meeting_id, exc):
            self.terminal_calls.append((meeting_id, exc))
            return real_terminal(meeting_id, exc)

        monkeypatch.setattr(worker_module, "record_terminal_failure", spy)

    def enqueue_and_drain(self, meeting_id, storage_path="recordings/x.m4a"):
        async def run():
            pool = await create_pool(redis_settings())
            try:
                await pool.enqueue_job("transcribe_job", meeting_id, storage_path)
            finally:
                await pool.aclose()

            from arq.worker import Worker
            worker = Worker(
                functions=[transcribe_job],
                redis_settings=redis_settings(),
                burst=True,
                max_tries=settings.transcription_max_tries,
                poll_delay=0.05,
            )
            try:
                await worker.async_run()
                return worker.jobs_complete, worker.jobs_failed, worker.jobs_retried
            finally:
                await worker.close()

        self.complete, self.failed, self.retried = asyncio.run(run())


def _flaky_embeddings(monkeypatch, fail_times):
    """An embedding provider that fails its first `fail_times` calls, then works."""
    calls = []

    def _embed_documents(texts):
        calls.append(list(texts))
        if len(calls) <= fail_times:
            raise RuntimeError("embedding provider down")
        return [[0.5] * EMBED_DIM for _ in texts], embedding_service.GEMINI_PROVIDER

    monkeypatch.setattr(embedding_service, "_embed_documents", _embed_documents)
    return calls


def test_a_real_worker_retries_a_failed_attempt_and_resumes_at_indexing(db, user, monkeypatch, gemini_tripwire):
    """
    The retry the resume check was built for. Attempt 1 fails indexing a
    stored transcript; arq must run attempt 2, which re-indexes only - the
    tripwire fails the test if anything re-downloads or re-transcribes.
    """
    monkeypatch.setattr(settings, "transcription_max_tries", 3)
    meeting = make_meeting(db, user.id, status="completed", transcript=TRANSCRIPT)
    embed_calls = _flaky_embeddings(monkeypatch, fail_times=1)
    run = _RealWorkerRun(monkeypatch)

    run.enqueue_and_drain(str(meeting.id))

    assert len(embed_calls) == 2, f"expected a failed attempt and a retry, got {len(embed_calls)} attempt(s)"
    assert (run.complete, run.failed, run.retried) == (1, 0, 1)
    assert run.terminal_calls == [], "a job that recovered was written off as terminal"
    row = reread(meeting.id)
    assert row.status == "completed"
    assert row.embedding_provider == "gemini"
    assert row.error_message is None
    assert chunk_count(meeting.id) > 0


def test_a_real_worker_retries_up_to_max_tries_then_writes_the_terminal_state(db, user, monkeypatch, gemini_tripwire):
    """
    Fails on every attempt. arq runs all of them - not one - and the last
    records the terminal note instead of leaving an un-indexed "completed"
    meeting with nothing on it.
    """
    monkeypatch.setattr(settings, "transcription_max_tries", 3)
    meeting = make_meeting(db, user.id, status="completed", transcript=TRANSCRIPT)
    embed_calls = _flaky_embeddings(monkeypatch, fail_times=99)
    run = _RealWorkerRun(monkeypatch)

    run.enqueue_and_drain(str(meeting.id))

    assert len(embed_calls) == 3, f"expected 3 attempts, got {len(embed_calls)}"
    # The last attempt handles the failure itself and returns, so arq counts
    # the job complete rather than failed - see transcribe_job.
    assert (run.complete, run.failed, run.retried) == (1, 0, 2)
    assert len(run.terminal_calls) == 1
    row = reread(meeting.id)
    assert row.status == "completed"
    assert "RAG indexing failed" in row.error_message
    assert "embedding provider down" in row.error_message
    assert row.embedding_provider is None


def test_a_real_worker_fails_a_transcript_less_meeting_after_max_tries(db, user, monkeypatch):
    monkeypatch.setattr(settings, "transcription_max_tries", 3)
    meeting = make_meeting(db, user.id, status="transcribing")
    attempts = []

    def boom(mid, path):
        attempts.append(mid)
        raise RuntimeError("storage unreachable")

    monkeypatch.setattr(worker_module, "transcribe_recording", boom)
    run = _RealWorkerRun(monkeypatch)

    run.enqueue_and_drain(str(meeting.id))

    assert len(attempts) == 3, f"expected 3 attempts, got {len(attempts)}"
    assert (run.complete, run.failed, run.retried) == (1, 0, 2)
    row = reread(meeting.id)
    assert row.status == "failed", "left non-terminal for the watchdog"
    assert "repeated attempts" in row.error_message
    assert "storage unreachable" in row.error_message


def test_the_retry_log_lines_match_what_the_worker_actually_did(db, user, monkeypatch, caplog):
    """
    "retrying" used to be logged for a retry that never happened. Each retry
    warning now has to correspond to a real re-run, and the give-up line to
    the real attempt count.
    """
    import logging

    monkeypatch.setattr(settings, "transcription_max_tries", 3)
    meeting = make_meeting(db, user.id, status="transcribing")
    attempts = []

    def boom(mid, path):
        attempts.append(mid)
        raise RuntimeError("storage unreachable")

    monkeypatch.setattr(worker_module, "transcribe_recording", boom)
    run = _RealWorkerRun(monkeypatch)

    with caplog.at_level(logging.INFO, logger="app.worker"):
        run.enqueue_and_drain(str(meeting.id))

    lines = [r.getMessage() for r in caplog.records if r.name == "app.worker"]
    started = [line for line in lines if "transcribing meeting" in line]
    retrying = [line for line in lines if "retrying" in line]
    gave_up = [line for line in lines if "gave up" in line]

    assert len(started) == len(attempts) == 3, f"attempt lines {started} for {len(attempts)} real attempt(s)"
    assert len(retrying) == run.retried == 2, f"retry lines {retrying} for {run.retried} real retries"
    assert gave_up == [f"[worker] meeting {meeting.id} gave up after 3 attempts: storage unreachable"]
    assert [f"attempt {n}/3" in line for n, line in zip((1, 2, 3), started)] == [True, True, True]
    assert [f"attempt {n}/3 failed" in line for n, line in zip((1, 2), retrying)] == [True, True]


def test_the_next_attempt_waits_out_the_configured_delay(db, user, monkeypatch):
    """The retry is deferred, not immediate: a provider outage gets time to clear."""
    import time

    monkeypatch.setattr(settings, "transcription_max_tries", 2)
    meeting = make_meeting(db, user.id, status="transcribing")
    started_at = []

    def boom(mid, path):
        started_at.append(time.monotonic())
        raise RuntimeError("storage unreachable")

    monkeypatch.setattr(worker_module, "transcribe_recording", boom)
    run = _RealWorkerRun(monkeypatch, retry_delay_seconds=1)

    run.enqueue_and_drain(str(meeting.id))

    assert len(started_at) == 2
    assert started_at[1] - started_at[0] >= 0.9, f"attempt 2 ran {started_at[1] - started_at[0]:.2f}s after attempt 1"


def test_a_non_final_attempt_hands_arq_a_retry_with_the_configured_delay(db, user, monkeypatch):
    """The contract the real-worker tests depend on, stated at the function."""
    from arq.worker import Retry

    monkeypatch.setattr(settings, "transcription_max_tries", 3)
    monkeypatch.setattr(settings, "transcription_retry_delay_seconds", 30)
    meeting = make_meeting(db, user.id, status="transcribing")

    def boom(mid, path):
        raise RuntimeError("storage unreachable")

    monkeypatch.setattr(worker_module, "transcribe_recording", boom)

    with pytest.raises(Retry) as exc:
        asyncio.run(transcribe_job({"job_try": 2}, str(meeting.id), "recordings/x.m4a"))

    assert exc.value.defer_score == 30_000
    assert isinstance(exc.value.__cause__, RuntimeError), "the original failure is lost from the traceback"
    assert reread(meeting.id).status == "transcribing", "a non-final attempt wrote a terminal state"
