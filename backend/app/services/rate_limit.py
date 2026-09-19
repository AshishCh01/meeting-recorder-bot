"""
Per-user rate limiting (docs/scaling-plan.md, Phase B1).

There was none anywhere. `POST /meetings/{id}/chat` and `/chat/stream` proxy
straight to a paid model with nothing but a 4000-character cap on the question
(models/meeting.py) between a `while true` loop and the API key. On a public
box that is an unbounded bill, and it is the one Phase B item that blocks a
deploy.

Four decisions worth reading before changing anything here.

**Redis, not a library.** `slowapi` is the obvious reach and it is the wrong
shape twice over: it keys on IP, which for this app is a shared NAT or a
corporate egress as often as it is a person, and it installs as middleware,
which runs before `get_current_user` has resolved the only key that means
anything. A3 already put Redis in `requirements.txt` and C3 already keeps a
cache in it. This is a Lua script and a dependency.

**A dependency, not middleware.** The key is the `user_id` from
`get_current_user`, so this has to run after auth; and the limits differ per
route, which middleware would have to rediscover from the path. As a
`Depends(...)` it composes with auth by declaring it, and FastAPI's dependency
cache means `get_current_user` still runs exactly once per request.

**Async client, unlike bot_registry.** `bot_registry` uses a synchronous
client deliberately, and says why: everything that calls it runs in a thread.
That reasoning does not transfer. `chat_with_meeting_stream` is `async def`
and runs on the event loop, so a blocking `redis-py` call inside it stalls
*every* request in flight, not just its own - a 500ms Redis hiccup would
become 500ms added to every concurrent request in the process. So: an
`async def` dependency and `redis.asyncio`. That is also correct for
`create_meeting`, which is a plain `def` route: FastAPI solves dependencies on
the event loop regardless of how the route body will be run, so one async
dependency covers both flavours of route and there is no second code path.

**No database connection is held across the Redis call.** This codebase has
been bitten by that repeatedly (`101a114`, `79551d0`, `0460fac`) and the pool
is 8 against Supabase's 15. `get_db` takes a connection lazily on the first
query, and `_ensure_user_row` ends its transaction on every path, so by the
time this dependency runs nothing is checked out - and because it is a
dependency it finishes before the route body runs its first query. Keep it in
that position: moved into the route body after `_assert_chattable`, it would
hold that check's connection across a network round-trip.
"""
import asyncio
import logging
import math
import time
import weakref
from dataclasses import dataclass
from typing import Callable

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException

from app.api.auth import get_current_user
from app.config import settings
from app.observability import capture_message

logger = logging.getLogger(__name__)

KEY_PREFIX = "ratelimit:"

# Fixed window, incremented and expired in one Lua script.
#
# The bug this exists to avoid is INCR-then-EXPIRE as two calls: a process
# that dies between them leaves a key with no TTL, which rate-limits that user
# forever with no way to notice. A Redis script runs to completion without
# interleaving, so the increment and its expiry are not separable, and the
# same property is what makes the concurrency gate pass - N+5 simultaneous
# requests get N+5 distinct counter values, not a read-modify-write race.
#
# `ttl < 0` is the repair path rather than a theoretical branch: it covers a
# key left behind by an older, broken version of this code, and a key whose
# TTL was cleared by hand. Without it such a key is permanent.
#
# A refused request still increments. The TTL is never extended, so the window
# still rolls when it was always going to - hammering the endpoint delays
# nothing, it just does not help.
#
# Fixed window, not sliding: the worst case is 2N across a window boundary,
# and with defaults chosen so a person never approaches N that burst is
# tolerance rather than a leak. A sliding-window ZSET would store N members
# per user to buy back a factor of two on a cost cap - the wrong trade for the
# extra moving part.
_INCREMENT = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('PTTL', KEYS[1])
if count == 1 or ttl < 0 then
  redis.call('PEXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {count, ttl}
"""


@dataclass(frozen=True)
class Limit:
    """
    One budget. `scope` is the second segment of the Redis key, so two routes
    sharing a scope share a counter - which is the whole point for the two
    chat routes.

    `requests` and `window_seconds` are callables rather than numbers because
    they come from settings, and a test that monkeypatches a setting must not
    have to know that a module-level constant captured it at import time.
    """
    scope: str
    requests: Callable[[], int]
    window_seconds: Callable[[], int]
    # Written for the person who reads it in a chat bubble. It says what to do
    # and does not name the mechanism - "rate limit" is our word for our
    # problem, and "{wait}" is filled in from the counter's real TTL.
    message: str


CHAT = Limit(
    # One scope for POST /chat and POST /chat/stream. They are the same
    # operation with different transports; two scopes would mean a caller
    # alternating between them gets exactly double the budget.
    scope="chat",
    requests=lambda: settings.chat_rate_limit_requests,
    window_seconds=lambda: settings.chat_rate_limit_window_seconds,
    message="You've sent a lot of questions in a short time. Please try again in {wait}.",
)

ASK_AI = Limit(
    # Separate from CHAT, and tighter: see settings.ask_ai_rate_limit_requests.
    scope="ask-ai",
    requests=lambda: settings.ask_ai_rate_limit_requests,
    window_seconds=lambda: settings.ask_ai_rate_limit_window_seconds,
    message="You've asked a lot of questions in a short time. Please try again in {wait}.",
)

MEETING_CREATE = Limit(
    scope="meeting-create",
    requests=lambda: settings.meeting_create_rate_limit_requests,
    window_seconds=lambda: settings.meeting_create_rate_limit_window_seconds,
    message="You've started a lot of recordings in a short time. Please try again in {wait}.",
)


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------
#
# Cached per event loop, because a redis.asyncio pool's connections belong to
# the loop that opened them. The API has exactly one loop, so in production
# this is a singleton opened on the first limited request. The tests drive
# async code with asyncio.run(...) per test - a plain module-level singleton
# would hand the second test connections bound to the first test's closed
# loop. A WeakKeyDictionary means a finished loop's entry simply goes away
# with it rather than being something to remember to clean up.
#
# Keyed by URL as well, so monkeypatching settings.redis_url in a test points
# at the throwaway Redis instead of reusing a client aimed somewhere else.
_clients: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _client_and_script():
    loop = asyncio.get_running_loop()
    url = settings.redis_url
    per_loop = _clients.get(loop)
    if per_loop is None:
        per_loop = {}
        _clients[loop] = per_loop
    entry = per_loop.get(url)
    if entry is None:
        client = aioredis.Redis.from_url(
            url,
            decode_responses=True,
            # Both halves, because "Redis is down" has two shapes and only one
            # of them is fast on its own: a refused connection returns
            # immediately, a black-holed host (a security group change, a
            # frozen container) hangs until something gives up. redis-py
            # applies no retry by default at this version, so one timeout is
            # one timeout, not three.
            socket_connect_timeout=settings.rate_limit_redis_timeout_seconds,
            socket_timeout=settings.rate_limit_redis_timeout_seconds,
        )
        entry = (client, client.register_script(_INCREMENT))
        per_loop[url] = entry
    return entry


def reset_clients() -> None:
    """Drops the cached clients. For tests that repoint settings.redis_url."""
    _clients.clear()


async def aclose() -> None:
    """
    Close this loop's client on shutdown, called from main.py's lifespan.

    Not strictly required - the process is ending - but an abandoned pool
    means sockets torn down by the OS rather than closed, which shows up on
    the Redis side as client connections dropping on every deploy. Cheap to do
    properly. Only this loop's entry: another loop's connections cannot be
    closed from here, and in the API there is only ever one.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for client, _script in (_clients.pop(loop, None) or {}).values():
        try:
            await client.aclose()
        except Exception as e:
            logger.warning("[ratelimit] could not close the Redis client cleanly: %s", e)


# ---------------------------------------------------------------------------
# Failing open
# ---------------------------------------------------------------------------
#
# **If Redis is unreachable, the request is allowed through.** Deliberately,
# and the reasoning is worth keeping because the opposite choice is just as
# arguable:
#
# - This is a cost cap, not an authorisation check. Nothing behind it is
#   unsafe to serve; it is only expensive to serve a lot of. Failing closed
#   treats an infrastructure blip as a permissions decision.
# - Chat is the feature that still works when Redis does not. Transcription
#   (A3) and dispatch (C2) are queued through Redis and are already degraded,
#   but answering a question about an existing transcript needs only Postgres
#   and Gemini. Failing closed would take the one working feature offline to
#   protect a budget - a self-inflicted outage on the most visible surface,
#   during an incident, at exactly the moment nobody wants a second symptom.
# - The exposure is bounded and attended. Abuse still needs a valid Supabase
#   JWT, questions are still capped at 4000 characters, and every user is
#   still bounded by how fast a model answers. Meanwhile the first failure in
#   each interval raises a Sentry event (A4) that says the cap is off, so the
#   window is as long as it takes someone to notice - not indefinite and not
#   silent.
#
# What was never on the table is letting an unhandled exception decide: an
# uncaught ConnectionError here would be a 500 on every chat, which is
# fail-closed with a worse error message.
#
# The revert, if a real bill ever makes this the wrong call, is the `return`
# in the except block below becoming a raise - plus accepting that a Redis
# outage is then a chat outage.
_FAIL_OPEN_LOG_INTERVAL_SECONDS = 60.0
_last_failure_report = 0.0
_failure_count = 0


def failure_stats() -> dict:
    """How often the limiter has failed open in this process. For tests."""
    return {"total": _failure_count}


def reset_failure_stats() -> None:
    global _failure_count, _last_failure_report
    _failure_count = 0
    _last_failure_report = 0.0


def _report_fail_open(scope: str, exc: Exception) -> None:
    global _failure_count, _last_failure_report

    _failure_count += 1
    total = _failure_count

    logger.error(
        "[ratelimit] Redis is unreachable - failing OPEN, the %s cap is not being "
        "enforced (%s occurrence(s) in this process): %s: %s",
        scope, total, type(exc).__name__, exc,
    )

    # Rate limited the way the auth fallback's is (api/auth.py): the running
    # total rides along in every event, so a limiter that is open on 100% of
    # requests is legible from one Sentry issue without sending one event per
    # request.
    now = time.monotonic()
    if total == 1 or (now - _last_failure_report) >= _FAIL_OPEN_LOG_INTERVAL_SECONDS:
        _last_failure_report = now
        capture_message(
            "[ratelimit] Redis unreachable - rate limiting is failing open",
            level="error",
            tags={"ratelimit_scope": scope},
            extra={"failures_total": total, "detail": "{}: {}".format(type(exc).__name__, exc)},
        )


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

def _humanise(seconds: int) -> str:
    """'in about 20 seconds' beats 'in 17 seconds' - nobody is holding a watch."""
    if seconds <= 0:
        return "a moment"
    if seconds < 60:
        return "about {} seconds".format(int(math.ceil(seconds / 5.0) * 5))
    minutes = int(math.ceil(seconds / 60.0))
    return "about a minute" if minutes == 1 else "about {} minutes".format(minutes)


async def check(limit: Limit, user_id: str) -> None:
    """
    Counts one request against `limit` for `user_id`, or raises 429.

    The 429 carries `Retry-After` and a `{"detail": ...}` body, mirroring
    `_db_busy` in main.py. The frontend's stream reader already renders
    `detail` verbatim from its `!response.ok` branch (useMeetingChat.js), so
    the sentence below is what the user reads - no frontend change.
    """
    if not settings.rate_limit_enabled:
        return

    allowance = limit.requests()
    window = limit.window_seconds()
    if allowance <= 0 or window <= 0:
        # A misconfiguration that would refuse every request is not a limit,
        # it is an outage. Treat it as "off" and say so.
        logger.warning(
            "[ratelimit] the %s limit is configured as %s per %ss - not enforcing it",
            limit.scope, allowance, window,
        )
        return

    key = "{}{}:{}".format(KEY_PREFIX, limit.scope, user_id)
    try:
        client, script = _client_and_script()
        count, ttl_ms = await script(keys=[key], args=[int(window * 1000)], client=client)
    except Exception as e:
        # Fail open. See the block comment above for why, and for what to
        # change if that stops being the right call.
        _report_fail_open(limit.scope, e)
        return

    count, ttl_ms = int(count), int(ttl_ms)
    if count <= allowance:
        return

    retry_after = max(1, int(math.ceil(ttl_ms / 1000.0))) if ttl_ms > 0 else window
    logger.info(
        "[ratelimit] user %s is over the %s limit (%s in a %ss window) - 429, retry after %ss",
        user_id, limit.scope, count, window, retry_after,
    )
    raise HTTPException(
        status_code=429,
        detail=limit.message.format(wait=_humanise(retry_after)),
        # Readable by a client that looks; the browser does not need it, since
        # `detail` already carries the wait in words and CORS would hide this
        # header from JavaScript anyway unless it were explicitly exposed.
        headers={"Retry-After": str(retry_after)},
    )


def limited(limit: Limit):
    """
    The route dependency: `user_id: str = Depends(limited(rate_limit.CHAT))`.

    It returns the user id, so it *replaces* `Depends(get_current_user)` in a
    route signature rather than sitting beside it - which is what guarantees
    the ordering. Auth resolves first because this depends on it, and the
    whole thing finishes before the route body's first query.
    """
    async def dependency(user_id: str = Depends(get_current_user)) -> str:
        await check(limit, user_id)
        return user_id

    return dependency
