import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Only platforms meeting-bot can actually record. Teams is deliberately
# absent: there is no Teams bot implementation, so routing to it produced a
# meeting stuck in "joining" rather than a clean failure. platform_detector's
# host allowlist already rejects Teams URLs before they reach here.
PLATFORM_ENDPOINTS = {
    "google": "/google/join",
    "zoom": "/zoom/join",
}

# The name arq registers the dispatch task under (app/worker.py). A string
# rather than an import: enqueueing by name is what keeps this module free of
# any dependency on the worker, which imports *this* one.
DISPATCH_JOB = "dispatch_bot_join_job"


def initial_status() -> str:
    """
    What a meeting's status becomes the moment its bot is requested.

    Phase C2 (docs/scaling-plan.md): with the queue on, the join has not
    happened yet - a dispatcher will place it when a recorder is free - so the
    meeting is "queued", not "joining". This matters more than it looks:
    watchdog_joining_ttl_minutes is 10, so a meeting parked in "joining" while
    it waits gets swept to "failed" after 11 minutes of legitimate waiting,
    which is the exact outcome this phase exists to prevent, just slower.
    "queued" has its own, much longer TTL.

    Lives here so the flag is read in one module. Both call sites
    (meetings.py's create_meeting and scheduler.py's trigger_due_meetings)
    ask rather than deciding for themselves.
    """
    return "queued" if settings.bot_dispatch_use_queue else "joining"


def trigger_bot_join(platform: str, meeting_url: str, meeting_id: str, user_id: str, bot_display_name: str) -> dict:
    """
    The seam every bot-join call site goes through (POST /meetings and the
    scheduler's due sweep). Which side of the C2 cutover we are on is decided
    here and nowhere else, exactly as submit_transcription does for A3, so
    flipping BOT_DISPATCH_USE_QUEUE moves both callers without either one
    changing - and flipping it back is the rollback.

    Queue on: enqueues a dispatch job and returns immediately. The bot is
    contacted later, by the worker, once it has capacity.
    Queue off: posts the join synchronously and raises on a non-2xx, which is
    what turns a busy bot's 409 into a failed meeting and a 502 from
    POST /meetings. That is the behaviour C2 exists to replace.
    """
    endpoint = PLATFORM_ENDPOINTS.get(platform)
    if endpoint is None:
        # Unreachable via POST /meetings and the calendar flow (both go
        # through platform_detector's allowlist first) - this is here so a
        # future caller that skips that check fails loudly and immediately,
        # rather than leaving the meeting stuck in a non-terminal status.
        # Checked before the flag so an unsupported platform fails the same
        # way on both sides of the cutover, rather than being deferred into
        # a worker that can only discover it 30 seconds later.
        raise ValueError(f"No meeting-bot endpoint for platform: {platform!r}")

    if settings.bot_dispatch_use_queue:
        job = _run_blocking(_enqueue_dispatch(meeting_id))
        job_id = getattr(job, "job_id", None)
        logger.info("[dispatch] meeting %s queued for a recorder as job %s", meeting_id, job_id)
        return {"status": "queued", "meetingId": meeting_id, "jobId": job_id}

    return post_join(platform, meeting_url, meeting_id, user_id, bot_display_name)


def post_join(platform: str, meeting_url: str, meeting_id: str, user_id: str, bot_display_name: str) -> dict:
    """
    The actual HTTP call to meeting-bot, unchanged from the pre-C2
    trigger_bot_join. Both sides of the cutover end here - the synchronous
    path calls it inline, the queued path calls it from the worker once
    capacity exists - so there is exactly one definition of "join a meeting".

    Raises httpx.HTTPStatusError on a non-2xx; a 409 specifically means the
    bot is at capacity or already has this meeting.
    """
    endpoint = PLATFORM_ENDPOINTS.get(platform)
    if endpoint is None:
        raise ValueError(f"No meeting-bot endpoint for platform: {platform!r}")

    response = httpx.post(
        f"{settings.meeting_bot_url}{endpoint}",
        json={
            "url": meeting_url,
            "meetingId": meeting_id,
            "userId": user_id,
            "botDisplayName": bot_display_name,
        },
        headers={
            "Authorization": f"Bearer {settings.meeting_bot_bearer_token}"
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_bot_capacity() -> dict:
    """
    Asks the bot how loaded it is - GET /capacity, shipped in Phase C1.

    Returns the bot's payload: {"active", "max", "available", "meetingIds"}.
    Raises on transport failure or a non-2xx, which the dispatcher treats as
    "no capacity right now" rather than as a failure of the meeting.

    This is an optimisation, never a lock. The check and the join are not
    atomic, so two dispatch jobs can both read available: 1 and both post -
    the bot's own 409 is the authority, and the dispatcher is written to
    expect it. C1 made /capacity and the bot's admission rule share one
    expression, so the number here is honest; it is just not exclusive.
    """
    response = httpx.get(
        f"{settings.meeting_bot_url}/capacity",
        headers={
            "Authorization": f"Bearer {settings.meeting_bot_bearer_token}"
        },
        timeout=settings.bot_capacity_timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def stop_bot(meeting_id: str) -> dict:
    """
    Asks meeting-bot to abandon whatever it's currently doing for
    meeting_id (waiting for admission, or mid-recording) and shut down
    cleanly. meeting-bot reports the resulting "failed" status back via
    its usual webhook, same as any other in-flight failure.

    Never called for a "queued" meeting: it has no bot session to stop,
    because no join was ever posted. Cancelling one is a delete.
    """
    response = httpx.post(
        f"{settings.meeting_bot_url}/stop",
        json={"meetingId": meeting_id},
        headers={
            "Authorization": f"Bearer {settings.meeting_bot_bearer_token}"
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


async def _enqueue_dispatch(meeting_id: str, attempt: int = 1):
    """
    One short-lived Redis connection per enqueue, for the same reason
    transcription_service._enqueue uses one: an asyncio Redis pool is bound to
    the event loop that created it, and this is reached from sync route
    handlers running on whichever threadpool thread FastAPI picked.

    No fixed _job_id. Deduplicating on meeting_id would mean a second attempt
    at the same meeting (a retry, or a re-dispatch after a 409) silently
    dropping while an earlier job's result was still in Redis. A duplicate is
    cheap instead: the dispatcher's status re-check drops any job whose
    meeting is no longer "queued".
    """
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        return await pool.enqueue_job(DISPATCH_JOB, meeting_id, attempt)
    finally:
        await pool.aclose()


def _run_blocking(coro):
    """
    Runs a coroutine to completion from sync code, loop or no loop.

    A deliberate twin of transcription_service._run_blocking rather than an
    import of it. Sharing would mean editing the module the transcription
    queue runs on to ship a bot-dispatch change - C2 should not be able to
    break A3, and this is ten lines.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Called from the event loop thread: give the coroutine its own loop on a
    # worker thread instead of deadlocking on the running one.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
