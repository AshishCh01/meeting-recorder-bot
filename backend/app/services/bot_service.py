import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import httpx

from app.config import settings

if TYPE_CHECKING:
    # Type-only. bot_registry imports this module (its poll loop calls
    # get_bot_capacity), so a real import here would be a cycle - and there is
    # nothing to import at runtime anyway: every function below needs only a
    # host's `.url`.
    from app.services.bot_registry import BotHost

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

    # Phase C3: the synchronous path has no dispatcher to pick a host for it
    # and no session to record one with, so it takes the first configured
    # host. On a single-host install - which is every install that still has
    # this flag off - that is the same bot MEETING_BOT_URL always named, and
    # require_host(None) resolves stop/delete back to it. Running the queue
    # off *and* more than one host configured is a misconfiguration: only the
    # first host would ever be used, and the warning below says so.
    from app.services import bot_registry

    hosts = bot_registry.configured_hosts()
    if len(hosts) > 1:
        logger.warning(
            "[dispatch] %s recorders are configured but BOT_DISPATCH_USE_QUEUE is off - "
            "only %s will be used. Turn the queue on to use the pool.",
            len(hosts), hosts[0].id,
        )
    return post_join(hosts[0], platform, meeting_url, meeting_id, user_id, bot_display_name)


def post_join(
    host: "BotHost", platform: str, meeting_url: str, meeting_id: str,
    user_id: str, bot_display_name: str,
) -> dict:
    """
    The actual HTTP call to meeting-bot. Both sides of the cutover end here -
    the synchronous path calls it inline, the queued path calls it from the
    worker once capacity exists - so there is exactly one definition of
    "join a meeting".

    Phase C3 made `host` the first argument rather than reading
    settings.meeting_bot_url: which recorder to ask is now a decision made by
    the caller (the dispatcher, against the heartbeat cache), and burying a
    default here would let a caller that forgot to make that decision silently
    send every meeting to one host.

    Raises httpx.HTTPStatusError on a non-2xx; a 409 specifically means the
    bot is at capacity or already has this meeting.
    """
    endpoint = PLATFORM_ENDPOINTS.get(platform)
    if endpoint is None:
        raise ValueError(f"No meeting-bot endpoint for platform: {platform!r}")

    response = httpx.post(
        f"{host.url}{endpoint}",
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


def get_bot_capacity(host: "BotHost") -> dict:
    """
    Asks one bot how loaded it is - GET /capacity, shipped in Phase C1.

    Returns the bot's payload: {"active", "max", "available", "meetingIds"}.
    Raises on transport failure or a non-2xx.

    Phase C3 moved the only caller: the dispatcher no longer calls this at
    all. bot_registry's poll loop does, once per host per
    bot_heartbeat_interval_seconds, and the dispatcher reads the cache that
    fills. With N hosts and a queue of meetings each retrying every 30
    seconds, calling this on every dispatch attempt is O(queued x hosts)
    requests; polling is O(hosts).

    This is an optimisation, never a lock. The reading and the join are not
    atomic, so two dispatch jobs can both see room on the same host - the
    bot's own 409 is the authority, and the dispatcher is written to expect
    it. C1 made /capacity and the bot's admission rule share one expression,
    so the number here is honest; it is just not exclusive.
    """
    response = httpx.get(
        f"{host.url}/capacity",
        headers={
            "Authorization": f"Bearer {settings.meeting_bot_bearer_token}"
        },
        timeout=settings.bot_capacity_timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def stop_bot(host: "BotHost", meeting_id: str) -> dict:
    """
    Asks one recorder to abandon whatever it's currently doing for meeting_id
    (waiting for admission, or mid-recording) and shut down cleanly. It
    reports the resulting "failed" status back via its usual webhook, same as
    any other in-flight failure.

    Phase C3: `host` comes from the meeting's own bot_host_id, resolved
    through bot_registry.require_host - asking the wrong host would 404 on a
    session it never had while the real recording carried on. Callers resolve
    rather than guess, and an unresolvable host is a 4xx, not a call to some
    other bot.

    Never called for a "queued" meeting: it has no bot session to stop,
    because no join was ever posted. POST /meetings/{id}/stop cancels those
    with bot_dispatch.cancel_queued_meeting instead.
    """
    response = httpx.post(
        f"{host.url}/stop",
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
