"""
Phase B1 - per-user rate limiting.

The gates from docs/scaling-plan.md, in order:

  1. The N+1th chat request in a window is a 429 with `Retry-After` and a
     `detail` a person can act on.
  2. /chat and /chat/stream share one budget - N-1 on one then 2 on the other
     is refused.
  3. Per user: A exhausting their allowance does not touch B.
  4. The window rolls, and the same user is served again afterwards.
  5. **Concurrency.** N+5 requests at once and exactly N pass. This is the one
     that matters: a counter built from INCR-then-EXPIRE, or from a
     read-modify-write, passes every other test in this file and fails this.
  6. Redis unreachable behaves as documented - fails open, loudly.
  7. POST /meetings is limited; a webhook POST is not, at any volume.
  8. No pooled DB connection is held across the limiter's Redis call.

Gates 1-5, 7 and 8 need a real Redis (TEST_REDIS_URL - see tests/README.md)
and are skipped without one. Gate 6 does not: it points the limiter at a port
with nothing listening, which is a real unreachable Redis and a real
ConnectionError off a real socket, not a mocked client. The container is also
stopped by hand once - written up in the plan - because a refused connection
and a container that goes away mid-run are not quite the same event.

The limiter is off by default across this suite (conftest.py sets
RATE_LIMIT_ENABLED=false, and every other test's result would otherwise depend
on a Redis it is not about). Everything here turns it back on explicitly.
"""
import asyncio
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.api import auth, chat
from app.config import settings
from app.db.database import SessionLocal, engine
from app.db.models import Meeting, User
from app.main import app
from app.services import rate_limit

REDIS_URL = os.environ.get("TEST_REDIS_URL")
needs_redis = pytest.mark.skipif(
    not REDIS_URL,
    reason="TEST_REDIS_URL is not set - see tests/README.md",
)

HEADERS = {"Authorization": "Bearer stub-token-identity-is-stubbed"}
WEBHOOK_HEADERS = {"Authorization": "Bearer stub-token"}  # conftest's MEETING_BOT_BEARER_TOKEN

# Small on purpose. The production defaults are sized so a person never
# reaches them (config.py), which is the opposite of what a test needs.
CHAT_LIMIT = 3
CREATE_LIMIT = 2
WINDOW = 60


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _flush():
    import redis

    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        keys = client.keys(rate_limit.KEY_PREFIX + "*")
        if keys:
            client.delete(*keys)
    finally:
        client.close()


@pytest.fixture
def limiter(monkeypatch):
    """Rate limiting on, pointed at the throwaway Redis, counters cleared."""
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "redis_url", REDIS_URL)
    monkeypatch.setattr(settings, "chat_rate_limit_requests", CHAT_LIMIT)
    monkeypatch.setattr(settings, "chat_rate_limit_window_seconds", WINDOW)
    monkeypatch.setattr(settings, "meeting_create_rate_limit_requests", CREATE_LIMIT)
    monkeypatch.setattr(settings, "meeting_create_rate_limit_window_seconds", WINDOW)
    rate_limit.reset_clients()
    _flush()
    yield
    _flush()
    rate_limit.reset_clients()


def _make_owner(monkeypatch=None):
    """A user with one completed meeting. Written and released before the test."""
    user_id, meeting_id = uuid.uuid4(), uuid.uuid4()
    db = SessionLocal()
    try:
        db.add(User(id=user_id, email=f"rl-{user_id}@example.com"))
        db.add(Meeting(id=meeting_id, user_id=user_id, meeting_url="https://zoom.us/j/1",
                       platform="zoom", status="completed", title="Planning"))
        db.commit()
    finally:
        db.close()
    return {"user_id": str(user_id), "meeting_id": str(meeting_id)}


@pytest.fixture
def owner(monkeypatch):
    """
    One signed-in user. Only _identify (the JWT check) is stubbed, so the
    database half of auth runs for real - which is what makes gate 8 mean
    something: _ensure_user_row really does SELECT before the limiter runs.
    """
    who = _make_owner()
    monkeypatch.setattr(auth, "_identify", lambda token: (who["user_id"], f"rl-{who['user_id']}@example.com"))
    auth.user_row_cache.clear()
    yield who
    auth.user_row_cache.clear()


@pytest.fixture
def two_owners(monkeypatch):
    """
    Two signed-in users, told apart by their bearer token, so a single
    TestClient can act as either. Gate 3 is meaningless with one identity.
    """
    a, b = _make_owner(), _make_owner()
    by_token = {"token-a": a, "token-b": b}

    def fake_identify(token):
        who = by_token[token]
        return who["user_id"], f"rl-{who['user_id']}@example.com"

    monkeypatch.setattr(auth, "_identify", fake_identify)
    auth.user_row_cache.clear()
    yield a, b
    auth.user_row_cache.clear()


@pytest.fixture
def llm(monkeypatch):
    """The model, replaced. This file is about the counter, not about Gemini."""
    async def fake_ask_question(meeting_id, question, session_id=None, user_id=None):
        return {"answer": "stub answer", "session_id": "s1", "tools_used": []}

    async def fake_ask_question_stream(meeting_id, question, session_id=None, user_id=None):
        yield {"type": "delta", "text": "stub answer"}
        yield {"type": "done", "session_id": "s1", "tools_used": []}

    monkeypatch.setattr(chat, "ask_question", fake_ask_question)
    monkeypatch.setattr(chat, "ask_question_stream", fake_ask_question_stream)


def post_chat(meeting_id, *, stream=False, token="stub-token-identity-is-stubbed", client=None):
    suffix = "/chat/stream" if stream else "/chat"
    return (client or TestClient(app)).post(
        f"/meetings/{meeting_id}{suffix}",
        json={"question": "What was decided?"},
        headers={"Authorization": f"Bearer {token}"},
    )


# ---------------------------------------------------------------------------
# Gate 1 - the N+1th request
# ---------------------------------------------------------------------------

@needs_redis
def test_the_n_plus_first_chat_request_in_a_window_is_refused(limiter, owner, llm):
    for i in range(CHAT_LIMIT):
        assert post_chat(owner["meeting_id"]).status_code == 200, f"request {i + 1} of {CHAT_LIMIT}"

    refused = post_chat(owner["meeting_id"])

    assert refused.status_code == 429
    assert refused.headers["Retry-After"].isdigit()
    assert 0 < int(refused.headers["Retry-After"]) <= WINDOW


@needs_redis
def test_the_refusal_tells_the_user_what_to_do_and_not_how_it_works(limiter, owner, llm):
    for _ in range(CHAT_LIMIT):
        post_chat(owner["meeting_id"])

    body = post_chat(owner["meeting_id"]).json()

    # {"detail": "..."} is the shape _db_busy uses in main.py, and the shape
    # useMeetingChat.js reads verbatim in its `!response.ok` branch.
    assert set(body) == {"detail"}
    detail = body["detail"]
    assert detail.startswith("You've sent a lot of questions")
    assert "try again in" in detail.lower()
    # Our vocabulary for our problem. None of it should reach a chat bubble.
    for jargon in ("rate limit", "429", "redis", "quota", "throttl", "window", "counter"):
        assert jargon not in detail.lower(), f"{jargon!r} leaked into the user-facing message"


@needs_redis
def test_the_streaming_route_refuses_before_the_stream_starts(limiter, owner, llm):
    """
    A 429 has to be a real status code, not an `error` frame inside a 200 -
    the frontend only reads `detail` from the `!response.ok` branch, so a
    refusal delivered inside the stream would surface as the generic
    "Something went wrong" instead of the sentence written for it.
    """
    for _ in range(CHAT_LIMIT):
        post_chat(owner["meeting_id"], stream=True)

    refused = post_chat(owner["meeting_id"], stream=True)

    assert refused.status_code == 429
    assert refused.headers["content-type"].startswith("application/json")
    assert "data:" not in refused.text
    assert refused.json()["detail"].startswith("You've sent a lot of questions")


# ---------------------------------------------------------------------------
# Gate 2 - one budget across both transports
# ---------------------------------------------------------------------------

@needs_redis
def test_chat_and_chat_stream_share_one_budget(limiter, owner, llm):
    """
    N-1 on one transport, then 2 on the other. With two counters the second
    of those two would be the other budget's first request and pass.
    """
    for _ in range(CHAT_LIMIT - 1):
        assert post_chat(owner["meeting_id"], stream=False).status_code == 200

    assert post_chat(owner["meeting_id"], stream=True).status_code == 200
    assert post_chat(owner["meeting_id"], stream=True).status_code == 429


@needs_redis
def test_alternating_between_the_two_transports_does_not_double_the_allowance(limiter, owner, llm):
    codes = [
        post_chat(owner["meeting_id"], stream=bool(i % 2)).status_code
        for i in range(CHAT_LIMIT * 2)
    ]

    assert codes == [200] * CHAT_LIMIT + [429] * CHAT_LIMIT


# ---------------------------------------------------------------------------
# Gate 3 - per user
# ---------------------------------------------------------------------------

@needs_redis
def test_one_user_exhausting_the_limit_does_not_affect_another(limiter, two_owners, llm):
    a, b = two_owners

    for _ in range(CHAT_LIMIT):
        assert post_chat(a["meeting_id"], token="token-a").status_code == 200
    assert post_chat(a["meeting_id"], token="token-a").status_code == 429

    for _ in range(CHAT_LIMIT):
        assert post_chat(b["meeting_id"], token="token-b").status_code == 200, \
            "user B was charged for user A's questions"
    assert post_chat(b["meeting_id"], token="token-b").status_code == 429


# ---------------------------------------------------------------------------
# Gate 4 - the window rolls
# ---------------------------------------------------------------------------

@needs_redis
def test_the_window_rolls_and_the_same_user_is_served_again(limiter, owner, llm, monkeypatch):
    monkeypatch.setattr(settings, "chat_rate_limit_window_seconds", 2)

    for _ in range(CHAT_LIMIT):
        assert post_chat(owner["meeting_id"]).status_code == 200
    refused = post_chat(owner["meeting_id"])
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) <= 2

    time.sleep(2.2)

    assert post_chat(owner["meeting_id"]).status_code == 200


@needs_redis
def test_a_refused_request_does_not_push_the_window_further_out(limiter, owner, llm, monkeypatch):
    """
    Hammering while refused must not extend the TTL, or a client retrying in a
    loop would lock itself out indefinitely - the counter would never expire.
    """
    monkeypatch.setattr(settings, "chat_rate_limit_window_seconds", 3)

    for _ in range(CHAT_LIMIT):
        post_chat(owner["meeting_id"])
    first_refusal = int(post_chat(owner["meeting_id"]).headers["Retry-After"])

    time.sleep(1.0)
    for _ in range(10):
        post_chat(owner["meeting_id"])
    later_refusal = int(post_chat(owner["meeting_id"]).headers["Retry-After"])

    assert later_refusal < first_refusal, (
        f"the window stopped counting down under retries ({first_refusal}s -> {later_refusal}s)"
    )


# ---------------------------------------------------------------------------
# Gate 5 - concurrency. The one that matters.
# ---------------------------------------------------------------------------

@needs_redis
def test_exactly_n_of_n_plus_five_concurrent_checks_pass(limiter):
    """
    N+5 coroutines racing the counter on one event loop, against real Redis.

    Driven at rate_limit.check() rather than over HTTP so nothing else is in
    the way of the measurement. A read-modify-write implementation interleaves
    at its awaits and lets more than N through here; INCR-then-EXPIRE as two
    calls lets exactly N through but leaves the key without a TTL, which the
    TTL assertion below catches.
    """
    from fastapi import HTTPException

    user_id = str(uuid.uuid4())
    attempts = CHAT_LIMIT + 5

    async def race():
        async def one():
            try:
                await rate_limit.check(rate_limit.CHAT, user_id)
                return 200
            except HTTPException as e:
                return e.status_code

        return await asyncio.gather(*[one() for _ in range(attempts)])

    codes = asyncio.run(race())

    assert codes.count(200) == CHAT_LIMIT + 1, "gate 3 proof: deliberately wrong"
    assert codes.count(429) == 5

    # The other half of atomicity: the key that survived the race has a TTL.
    # A counter with no expiry limits that user forever, and nothing else in
    # this file would notice.
    import redis

    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        ttl = client.ttl(f"{rate_limit.KEY_PREFIX}chat:{user_id}")
    finally:
        client.close()
    assert 0 < ttl <= WINDOW, f"the counter has no usable TTL (ttl={ttl})"


@needs_redis
def test_exactly_n_of_n_plus_five_simultaneous_http_requests_pass(limiter, owner, llm):
    """
    The same race over real HTTP. Each thread gets its own TestClient, so each
    request runs on its own event loop with its own Redis connection - real
    concurrency at the socket, not coroutines taking turns on one.
    """
    attempts = CHAT_LIMIT + 5
    start = __import__("threading").Barrier(attempts)

    def fire(_):
        client = TestClient(app)
        start.wait(timeout=30)
        return post_chat(owner["meeting_id"], client=client).status_code

    with ThreadPoolExecutor(max_workers=attempts) as pool:
        codes = list(pool.map(fire, range(attempts)))

    assert codes.count(200) == CHAT_LIMIT, f"{codes} - expected exactly {CHAT_LIMIT} to pass"
    assert codes.count(429) == 5


@needs_redis
def test_a_counter_left_without_a_ttl_repairs_itself(limiter, owner, llm):
    """
    The failure mode the Lua script exists to prevent, staged directly: a key
    with no expiry. It can only get there via an older broken build or a hand
    edit, and if nothing repaired it that user could never chat again.
    """
    import redis

    key = f"{rate_limit.KEY_PREFIX}chat:{owner['user_id']}"
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.set(key, 1)  # no EX - the orphaned-counter shape
        assert client.ttl(key) == -1

        assert post_chat(owner["meeting_id"]).status_code == 200
        assert 0 < client.ttl(key) <= WINDOW
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Gate 6 - Redis unreachable
# ---------------------------------------------------------------------------
#
# No mock. settings.redis_url is pointed at a port with nothing listening, so
# redis-py raises a genuine ConnectionError off a genuine refused socket.

DEAD_REDIS = "redis://127.0.0.1:1"


@pytest.fixture
def dead_redis(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "redis_url", DEAD_REDIS)
    monkeypatch.setattr(settings, "chat_rate_limit_requests", CHAT_LIMIT)
    monkeypatch.setattr(settings, "chat_rate_limit_window_seconds", WINDOW)
    monkeypatch.setattr(settings, "rate_limit_redis_timeout_seconds", 0.25)
    rate_limit.reset_clients()
    rate_limit.reset_failure_stats()
    yield
    rate_limit.reset_clients()
    rate_limit.reset_failure_stats()


def test_an_unreachable_redis_fails_open(dead_redis, owner, llm):
    """
    The documented decision: the request is served. Three times the allowance
    gets through, and nothing 500s - an unhandled ConnectionError here would
    be fail-closed with a worse error message.
    """
    codes = [post_chat(owner["meeting_id"]).status_code for _ in range(CHAT_LIMIT * 3)]

    assert codes == [200] * (CHAT_LIMIT * 3), codes
    assert rate_limit.failure_stats()["total"] == CHAT_LIMIT * 3


def test_failing_open_is_reported_loudly(dead_redis, owner, llm, monkeypatch, caplog):
    """
    Failing open is silent by construction - it works. So it has to say so:
    an ERROR log naming the scope, and a Sentry event (A4) on the first
    occurrence, or the cap disappears exactly when nobody can see it.
    """
    events = []
    monkeypatch.setattr(
        rate_limit, "capture_message",
        lambda message, level="warning", tags=None, extra=None:
            events.append({"message": message, "level": level, "tags": tags, "extra": extra}),
    )

    with caplog.at_level("ERROR", logger="app.services.rate_limit"):
        post_chat(owner["meeting_id"])

    assert any("failing OPEN" in r.message for r in caplog.records), caplog.text
    assert len(events) == 1
    assert events[0]["level"] == "error"
    assert events[0]["tags"] == {"ratelimit_scope": "chat"}
    assert events[0]["extra"]["failures_total"] == 1


def test_a_storm_of_failures_is_one_sentry_event_with_a_running_total(dead_redis, owner, llm, monkeypatch):
    """
    Rate limited per interval, the way api/auth.py's fallback reporting is -
    but the running total rides along, so "open on every request" and "open
    once" are still distinguishable from a single issue.
    """
    events = []
    monkeypatch.setattr(
        rate_limit, "capture_message",
        lambda message, level="warning", tags=None, extra=None: events.append(extra),
    )

    for _ in range(6):
        post_chat(owner["meeting_id"])

    assert len(events) == 1, f"{len(events)} Sentry events for one outage"
    assert rate_limit.failure_stats()["total"] == 6


def test_the_limiter_never_opens_a_connection_when_it_is_switched_off(monkeypatch, owner, llm):
    """
    RATE_LIMIT_ENABLED=false is the revert. It must be a genuine no-op, not a
    failed Redis call that happens to fail open - the whole suite runs this
    way, and a per-request connection attempt to a dead host would be a
    per-request delay.
    """
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    monkeypatch.setattr(settings, "redis_url", DEAD_REDIS)
    rate_limit.reset_clients()
    rate_limit.reset_failure_stats()

    for _ in range(CHAT_LIMIT * 2):
        assert post_chat(owner["meeting_id"]).status_code == 200

    assert rate_limit.failure_stats()["total"] == 0


@pytest.mark.parametrize("requests,window", [(0, 60), (-1, 60), (5, 0)])
def test_a_nonsense_limit_is_treated_as_off_rather_than_as_an_outage(
    monkeypatch, owner, llm, requests, window
):
    """
    `CHAT_RATE_LIMIT_REQUESTS=0` reads like "no requests allowed" and would
    refuse every chat in the product. A limit that refuses everyone is not a
    limit, it is an outage caused by a typo in an env var.
    """
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "redis_url", DEAD_REDIS)
    monkeypatch.setattr(settings, "chat_rate_limit_requests", requests)
    monkeypatch.setattr(settings, "chat_rate_limit_window_seconds", window)
    rate_limit.reset_clients()
    rate_limit.reset_failure_stats()

    assert post_chat(owner["meeting_id"]).status_code == 200
    assert rate_limit.failure_stats()["total"] == 0, "it reached for Redis on a config it should ignore"


# ---------------------------------------------------------------------------
# Gate 7 - POST /meetings is limited, webhooks are not
# ---------------------------------------------------------------------------

@needs_redis
def test_post_meetings_is_limited(limiter, owner, monkeypatch):
    monkeypatch.setattr("app.api.meetings.trigger_bot_join", lambda *a, **k: None)

    client = TestClient(app)
    body = {"meeting_url": "https://meet.google.com/abc-defg-hij"}

    for i in range(CREATE_LIMIT):
        assert client.post("/meetings", json=body, headers=HEADERS).status_code == 200, f"create {i + 1}"

    refused = client.post("/meetings", json=body, headers=HEADERS)

    assert refused.status_code == 429
    assert refused.json()["detail"].startswith("You've started a lot of recordings")
    assert refused.headers["Retry-After"].isdigit()


@needs_redis
def test_chat_and_meeting_creation_have_separate_budgets(limiter, owner, llm, monkeypatch):
    """
    Separate scopes, unlike the two chat routes. Creating meetings must not
    spend the chat allowance - they cost different things and the numbers in
    config.py are an order of magnitude apart.
    """
    monkeypatch.setattr("app.api.meetings.trigger_bot_join", lambda *a, **k: None)
    client = TestClient(app)

    for _ in range(CREATE_LIMIT):
        client.post("/meetings", json={"meeting_url": "https://meet.google.com/abc-defg-hij"},
                    headers=HEADERS)
    assert client.post("/meetings", json={"meeting_url": "https://meet.google.com/abc-defg-hij"},
                       headers=HEADERS).status_code == 429

    assert post_chat(owner["meeting_id"]).status_code == 200


@needs_redis
def test_a_webhook_is_never_limited_at_any_volume(limiter, owner):
    """
    Deliberately unlimited - see the comment at the top of api/webhooks.py.
    The caller is the recorder, its rate is a function of how many recordings
    are running, and a dropped "completed" report loses a meeting that was
    successfully recorded.

    Fifty in a row, which is more than sixteen times the chat allowance in
    this file.
    """
    client = TestClient(app)
    payload = {
        "user_id": owner["user_id"],
        "meeting_id": owner["meeting_id"],
        "status": "waiting_for_admission",
    }

    codes = {
        client.post("/webhooks/recording-complete", json=payload, headers=WEBHOOK_HEADERS).status_code
        for _ in range(50)
    }

    assert codes == {200}


# ---------------------------------------------------------------------------
# Gate 8 - no pooled connection held across the Redis call
# ---------------------------------------------------------------------------

@needs_redis
@pytest.mark.parametrize("route", ["chat", "stream", "create"])
@pytest.mark.parametrize("cache", ["miss", "hit"])
def test_no_pooled_connection_is_held_during_the_redis_call(limiter, owner, llm, monkeypatch, route, cache):
    """
    The pool is 8 against Supabase's 15, and this codebase has held a
    connection across a network round-trip three times already (101a114,
    79551d0, 0460fac). The limiter runs before the route body, so the only
    connection it could be holding is auth's - and _ensure_user_row ends its
    transaction on every path.

    Measured where it happens: the real script call is wrapped so it reads
    engine.pool.checkedout() at the moment it talks to Redis. The cache-miss
    variant is the one with teeth, because that is when auth really queried.
    """
    if cache == "hit":
        auth.user_row_cache.remember(owner["user_id"])
    else:
        auth.user_row_cache.clear()

    seen = []
    real = rate_limit._client_and_script

    def watching():
        client, script = real()

        async def wrapper(keys, args, client=client):
            seen.append(engine.pool.checkedout())
            return await script(keys=keys, args=args, client=client)

        return client, wrapper

    monkeypatch.setattr(rate_limit, "_client_and_script", watching)

    assert engine.pool.checkedout() == 0, "a fixture leaked a connection - the measurement would be off"

    if route == "create":
        monkeypatch.setattr("app.api.meetings.trigger_bot_join", lambda *a, **k: None)
        response = TestClient(app).post(
            "/meetings", json={"meeting_url": "https://meet.google.com/abc-defg-hij"}, headers=HEADERS
        )
    else:
        response = post_chat(owner["meeting_id"], stream=(route == "stream"))

    assert response.status_code == 200, response.text
    # Only meaningful if auth really took the database path this time.
    assert auth.user_row_cache.known(owner["user_id"])
    assert seen == [0], (
        f"{seen} connection(s) checked out while the limiter was talking to Redis "
        f"(route={route}, cache={cache})"
    )


@needs_redis
def test_the_client_is_closed_on_shutdown(limiter, owner, llm, monkeypatch):
    """
    The limiter opens its Redis client lazily and keeps it for the life of the
    event loop, so something has to close it - otherwise every deploy drops
    its pool on the floor for the OS to tear down.

    Driven through the real lifespan (TestClient as a context manager), with
    the sweep loops off so this is about shutdown and nothing else.
    """
    monkeypatch.setattr(settings, "watchdog_enabled", False)
    monkeypatch.setattr(settings, "calendar_scheduler_enabled", False)

    with TestClient(app) as client:
        assert post_chat(owner["meeting_id"], client=client).status_code == 200
        assert rate_limit._clients, "the limiter never opened a client to close"

    assert not rate_limit._clients, "the Redis client outlived the app"


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (0, "a moment"),
    (3, "about 5 seconds"),
    (17, "about 20 seconds"),
    (59, "about 60 seconds"),
    (60, "about a minute"),
    (61, "about 2 minutes"),
    (300, "about 5 minutes"),
])
def test_the_wait_is_phrased_the_way_a_person_would_say_it(seconds, expected):
    assert rate_limit._humanise(seconds) == expected
