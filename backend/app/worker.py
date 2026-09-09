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
from app.services.transcription_service import (
    record_terminal_failure,
    transcribe_recording,
)

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


class WorkerSettings:
    functions = [transcribe_job]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # Matches the ThreadPoolExecutor's max_workers=4, so the cutover does not
    # change how much concurrent load reaches Gemini.
    max_jobs = settings.worker_max_jobs
    job_timeout = settings.transcription_job_timeout_seconds
    max_tries = settings.transcription_max_tries
