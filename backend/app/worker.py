"""
arq worker entrypoint for the transcription queue (docs/scaling-plan.md, Phase A3).

Run it with the same image as the API, just a different command:

    arq app.worker.WorkerSettings

Why this exists: transcription used to run in a ThreadPoolExecutor inside the
API process, so every deploy killed in-flight jobs and left the meeting stuck
in "transcribing" until the watchdog TTL failed it - a failure the user sees
and did not cause. Redis holds the job across a restart instead.

The watchdog stays. A queue reduces stuck "transcribing" meetings; it does not
eliminate them, and a job that is never picked up at all is invisible to arq.
"""
import asyncio
import logging

from arq.connections import RedisSettings

from app.config import settings
from app.observability import configure_logging, init_sentry, set_meeting_context
from app.services.transcription_service import (
    record_terminal_failure,
    transcribe_recording,
)

# Phase A4, and the concrete bug A3 surfaced.
#
# This module is imported by the `arq` CLI (`import_string(worker_settings)`)
# *before* the CLI applies its own logging config, and that config only names
# the `arq` logger - it leaves root alone (`disable_existing_loggers: False`,
# no `root` key). So configuring root here sticks, and `app.*` loggers finally
# have somewhere to write. Before this, root stayed at WARNING with no handler
# and every logger.info() in this file was dropped: arq's own job lines showed
# up, "[worker] transcribing meeting ... (attempt 1/3)" never did.
#
# The consequence that actually mattered: the retry warning and the "gave up"
# error below were the only evidence a retry happened, so a job that succeeded
# on attempt 3 looked identical to one that succeeded first time.
configure_logging()
# arq's dictConfig gives the `arq` logger its own handler but never sets
# `propagate` - and dictConfig only assigns propagate when the key is present,
# so this survives it. Without it, arq's own lines would print twice: once
# through its handler, once through root's.
logging.getLogger("arq").propagate = False

# ArqIntegration patches Worker.run_job and wraps each registered coroutine, so
# it must be installed before the CLI builds the Worker - import time is the
# only place that is guaranteed. It opens a per-job isolation scope and reports
# exceptions that escape the job, which is why nothing below wraps
# transcribe_job by hand.
_sentry_on = init_sentry("worker")

logger = logging.getLogger(__name__)


async def transcribe_job(ctx, meeting_id: str, storage_path: str):
    """
    One transcription, start to finish.

    transcribe_recording is synchronous and blocking (HTTP downloads, ffmpeg
    subprocesses, Gemini calls), so it runs off the event loop via
    asyncio.to_thread - the same treatment chat_service gives its tool
    dispatch and main.py gives the watchdog and scheduler sweeps. It opens and
    closes its own SessionLocal inside that thread, which is what
    main.py:_run_with_session exists to guarantee: a Session must not be
    opened on the loop thread and closed there around an await, because
    to_thread cannot cancel the worker thread and the raced close surfaces as
    IllegalStateChangeError.

    Retries are arq's, bounded by max_tries below. The resume check at the top
    of transcribe_recording is what makes them affordable: attempt 2 skips
    transcription entirely and re-runs only the indexing step.
    """
    attempt = ctx.get("job_try", 1)
    # Tag this job's Sentry scope before anything can fail. `arq-job.args` is
    # redacted under send_default_pii=False, so without this an exception from
    # here arrives with no indication of which meeting it was. transcribe_recording
    # adds user_id once it has read the row.
    set_meeting_context(meeting_id)
    logger.info(
        "[worker] transcribing meeting %s (attempt %s/%s)",
        meeting_id, attempt, settings.transcription_max_tries,
    )
    try:
        return await asyncio.to_thread(transcribe_recording, meeting_id, storage_path)
    except Exception as e:
        if attempt < settings.transcription_max_tries:
            # Let arq retry. Nothing terminal is written yet, so the meeting
            # keeps whatever state the attempt left it in.
            logger.warning(
                "[worker] meeting %s attempt %s failed (%s) - retrying", meeting_id, attempt, e,
            )
            raise
        # Last attempt. Something has to write the terminal state, or the
        # meeting sits in a non-terminal status until the watchdog sweeps it -
        # this is what submit_transcription's _on_done callback did on the
        # executor path. Swallowed afterwards so arq does not also record a
        # job failure for a case that is now fully handled in the database.
        logger.error(
            "[worker] meeting %s gave up after %s attempts: %s",
            meeting_id, attempt, e,
        )
        await asyncio.to_thread(record_terminal_failure, meeting_id, e)
        return None


async def startup(ctx):
    """
    Re-assert the logging config once arq has applied its own.

    Import-time configure_logging() already wins today (see the comment at the
    top), but that depends on the CLI's ordering, and the failure mode if that
    ever changes is silence - exactly the bug this phase exists to fix. Calling
    it again here, after arq is fully configured, makes the fix independent of
    that ordering; basicConfig(force=True) is idempotent.
    """
    configure_logging()
    logging.getLogger("arq").propagate = False
    logger.info(
        "[worker] ready - log level %s, sentry %s, environment %s",
        settings.log_level.upper(),
        "enabled" if _sentry_on else "disabled (no SENTRY_DSN)",
        settings.environment,
    )


class WorkerSettings:
    functions = [transcribe_job]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # Matches the ThreadPoolExecutor's max_workers=4, so the cutover does not
    # change how much concurrent load reaches Gemini.
    max_jobs = settings.worker_max_jobs
    job_timeout = settings.transcription_job_timeout_seconds
    max_tries = settings.transcription_max_tries
