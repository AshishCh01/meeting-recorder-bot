"""
Phase C4 - per-host auth identity, and auth health gating dispatch.

C3 made the pool real. This is what stops it being a pool in name only: each
recorder has its own Google and Zoom credentials, and - the part that needed
building - a recorder whose credential for a platform has died stops receiving
meetings on that platform.

The failure this prevents is specific and was made *worse* by C3, not better.
A host with a dead Google session still answers `GET /capacity` with free
slots. Before C4 the registry would keep picking it, every join would fail
`AUTH_EXPIRED`, and the healthy host would sit idle - a dead credential
silently becomes a meeting-shredder that looks like a working recorder. On one
host that was merely "the bot is broken", which was obvious.

These cover the gate:

  2. A host whose Google session is dead stops receiving Google meetings while
     still receiving Zoom ones, and the other host keeps receiving both.
  3. With every host's Google session dead, the failure message names the
     credential, distinctly from the busy case.
  4. Restoring the credential lets the host receive meetings again with no
     restart - the next heartbeat is enough.
  5. A single-host install with no health reported at all is untouched.

Gates 1 and 6 are not here: 1 is about which file each container loads (proved
live, from container logs) and 6 is the suites themselves.

Postgres for the dispatch tests. Redis only for the ones that exercise the
real heartbeat cache - see tests/README.md.
"""
import os
import uuid

import pytest

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting
from app.services import bot_dispatch, bot_registry, bot_service

from tests.fake_bot_pool import FakeBot, FakeBotPool, install

REDIS_URL = os.environ.get("TEST_REDIS_URL")
needs_redis = pytest.mark.skipif(
    not REDIS_URL,
    reason="TEST_REDIS_URL is not set - see tests/README.md",
)

OK = bot_registry.AUTH_OK
EXPIRED = bot_registry.AUTH_EXPIRED
UNKNOWN = bot_registry.AUTH_UNKNOWN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_meeting(db, user_id, *, platform="google", status="queued"):
    url = (
        "https://meet.google.com/abc-defg-hij" if platform == "google"
        else "https://zoom.us/j/123456789"
    )
    meeting = Meeting(
        id=uuid.uuid4(), user_id=user_id, meeting_url=url,
        title=f"C4 {platform}", platform=platform, status=status,
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return meeting


def reread(meeting_id):
    other = SessionLocal()
    try:
        return other.query(Meeting).filter(Meeting.id == meeting_id).one_or_none()
    finally:
        other.close()


def beat(host_id="bot-a", *, available=1, active=0, max_concurrent=1, auth=None):
    return bot_registry.Heartbeat(
        host_id=host_id, url=f"http://{host_id}:3000",
        active=active, max=max_concurrent, available=available,
        last_seen=0.0, auth=auth or {},
    )


@pytest.fixture
def queue_on(monkeypatch):
    monkeypatch.setattr(settings, "bot_dispatch_use_queue", True)


@pytest.fixture
def two_hosts(monkeypatch):
    return install(monkeypatch, FakeBotPool(
        FakeBot(host_id="bot-a", url="http://meeting-bot:3000"),
        FakeBot(host_id="bot-b", url="http://meeting-bot-2:3000"),
    ))


@pytest.fixture
def real_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", REDIS_URL)
    _flush()
    yield
    _flush()


def _flush():
    import redis

    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        for prefix in (bot_registry.HEARTBEAT_PREFIX, bot_registry.RESERVED_PREFIX):
            keys = client.keys(prefix + "*")
            if keys:
                client.delete(*keys)
    finally:
        client.close()


# ---------------------------------------------------------------------------
# usable_for - the rule everything else is built on
# ---------------------------------------------------------------------------

def test_an_expired_platform_is_unusable_and_the_other_one_is_not():
    """
    **Per platform, not per host** - the point of the whole design. Google and
    Zoom are separate identities that expire independently, so collapsing them
    into one "unhealthy" flag would take a working recorder offline over a
    credential it was never going to use.
    """
    host = beat(auth={"google": {"status": EXPIRED}, "zoom": {"status": OK}})

    assert host.usable_for("google") is False
    assert host.usable_for("zoom") is True


def test_unknown_counts_as_usable(monkeypatch):
    """
    The state between boot and the first keepalive cycle. Treating it as
    expired would make every bot restart a brief pool-wide outage and would
    make a fresh install refuse meetings for its first half-minute - a
    guaranteed cost, paid every time, to avoid a bounded and self-correcting
    one. See Heartbeat.usable_for for the full reasoning.
    """
    assert beat(auth={"google": {"status": UNKNOWN}}).usable_for("google") is True


def test_a_bot_that_reports_no_health_at_all_is_usable():
    """
    Backward compatibility, and the single-host guarantee. A meeting-bot
    running pre-C4 code sends no `auth` key; so does a cache entry written
    before this field existed. Either must mean "no opinion, carry on" - the
    alternative is that deploying the backend ahead of the bots empties the
    pool.
    """
    assert beat(auth={}).usable_for("google") is True
    assert beat(auth=None).usable_for("google") is True


def test_no_platform_to_check_against_is_usable():
    assert beat(auth={"google": {"status": EXPIRED}}).usable_for(None) is True


def test_auth_status_reads_both_the_object_and_a_bare_string():
    """
    The endpoint sends {status, detail, checkedAt}; a bare string is accepted
    too so a hand-written fixture or a future flattening cannot silently read
    as "unknown" and quietly re-enable a dead host.
    """
    assert beat(auth={"google": {"status": EXPIRED}}).auth_status("google") == EXPIRED
    assert beat(auth={"google": EXPIRED}).auth_status("google") == EXPIRED
    assert beat(auth={}).auth_status("google") == UNKNOWN


# ---------------------------------------------------------------------------
# Gate 2 - a dead credential takes the host out for that platform only
# ---------------------------------------------------------------------------

def test_a_dead_google_session_stops_google_meetings_but_not_zoom(db, user, two_hosts, queue_on):
    """
    **Gate 2, in one test.** bot-a's Google session is dead. A Google meeting
    must go to bot-b; a Zoom meeting must still be allowed to land on bot-a,
    because bot-a's Zoom identity is a different credential and is fine.
    """
    two_hosts.by_id("bot-a").expire("google")

    google = make_meeting(db, user.id, platform="google")
    assert bot_dispatch.dispatch_queued_meeting(str(google.id)).host_id == "bot-b"
    assert reread(google.id).bot_host_id == "bot-b"
    assert two_hosts.by_id("bot-a").joins == [], "a Google join was sent to a dead Google session"

    # bot-b is now full, so the only host that can take the Zoom meeting is
    # bot-a - the one that is "unhealthy", but only for Google.
    zoom = make_meeting(db, user.id, platform="zoom")
    assert bot_dispatch.dispatch_queued_meeting(str(zoom.id)).host_id == "bot-a"
    assert reread(zoom.id).bot_host_id == "bot-a"


def test_the_healthy_host_keeps_taking_both_platforms(db, user, two_hosts, queue_on):
    """The other half of gate 2: nothing about bot-b changed."""
    two_hosts.by_id("bot-a").expire("google")
    two_hosts.by_id("bot-a").expire("zoom")
    # Room for both, with headroom to spare: claim_host reserves a slot per
    # dispatch and reservations only expire on a timer, so a max of exactly 2
    # would have the second dispatch refused by its own predecessor's
    # reservation rather than by anything this test is about.
    two_hosts.by_id("bot-b").max = 3

    google = make_meeting(db, user.id, platform="google")
    zoom = make_meeting(db, user.id, platform="zoom")

    assert bot_dispatch.dispatch_queued_meeting(str(google.id)).host_id == "bot-b"
    assert bot_dispatch.dispatch_queued_meeting(str(zoom.id)).host_id == "bot-b"
    assert two_hosts.by_id("bot-a").joins == []


def test_a_dead_credential_does_not_consume_a_dispatch_attempt_on_that_host(db, user, two_hosts, queue_on):
    """
    The host is skipped at selection, not tried and failed. Trying it would
    burn one of the meeting's 40 attempts and 30 seconds of its wait on a
    recorder that could not possibly succeed.
    """
    two_hosts.by_id("bot-a").expire("google")
    meeting = make_meeting(db, user.id, platform="google")

    result = bot_dispatch.dispatch_queued_meeting(str(meeting.id))

    assert result.outcome == "dispatched"
    assert result.host_id == "bot-b"


# ---------------------------------------------------------------------------
# Gate 3 - the failure message names the credential, not capacity
# ---------------------------------------------------------------------------

@needs_redis
def test_every_google_session_dead_names_the_credential(real_redis, monkeypatch):
    """
    **Gate 3.** The operator fix here - "regenerate bot-b's Google session" -
    is nothing like "add a host" or "wait for one to free up". A message that
    said "every recorder was busy" would send someone to look at capacity for
    a problem that is entirely about auth, which is exactly the wrong half of
    the system.
    """
    monkeypatch.setattr(settings, "bot_hosts", "bot-a=http://one:3000,bot-b=http://two:3000")
    monkeypatch.setattr(settings, "bot_heartbeat_ttl_seconds", 300.0)
    _write(beat("bot-a", auth={"google": {"status": EXPIRED}, "zoom": {"status": OK}}))
    _write(beat("bot-b", auth={"google": {"status": EXPIRED}, "zoom": {"status": OK}}))

    choice = bot_registry.claim_host("google")

    assert choice.host is None
    assert "working Google session" in choice.reason
    assert "bot-a (expired)" in choice.reason and "bot-b (expired)" in choice.reason
    assert "generate-auth.cjs" in choice.reason, "the message must carry the fix"
    assert "busy" not in choice.reason, "an auth outage was reported as a capacity problem"

    # Zoom is unaffected, which is the whole reason the states are per platform.
    assert bot_registry.claim_host("zoom").host is not None


@needs_redis
def test_a_full_pool_and_a_dead_one_are_different_messages(real_redis, monkeypatch):
    """The two failures the operator must be able to tell apart at a glance."""
    monkeypatch.setattr(settings, "bot_hosts", "bot-a=http://one:3000")
    monkeypatch.setattr(settings, "bot_heartbeat_ttl_seconds", 300.0)

    _write(beat("bot-a", available=0, active=1, auth={"google": {"status": OK}}))
    busy = bot_registry.claim_host("google").reason
    assert "busy" in busy and "session" not in busy

    _write(beat("bot-a", available=1, auth={"google": {"status": EXPIRED}}))
    dead = bot_registry.claim_host("google").reason
    assert "working Google session" in dead and "busy" not in dead


@needs_redis
def test_a_mixed_pool_says_both_things(real_redis, monkeypatch):
    """
    One host full, one locked out by a dead credential. Reporting only "busy"
    would hide that the pool is smaller than it looks for a reason that is
    fixable in a minute.
    """
    monkeypatch.setattr(settings, "bot_hosts", "bot-a=http://one:3000,bot-b=http://two:3000")
    monkeypatch.setattr(settings, "bot_heartbeat_ttl_seconds", 300.0)
    _write(beat("bot-a", available=0, active=1, auth={"google": {"status": OK}}))
    _write(beat("bot-b", available=1, auth={"google": {"status": EXPIRED}}))

    reason = bot_registry.claim_host("google").reason

    assert "bot-a 1/1" in reason
    assert "bot-b" in reason and "no usable Google session" in reason


def test_exhausted_waiting_carries_the_auth_reason_to_the_user(db, user, two_hosts, queue_on, monkeypatch):
    """
    The reason has to survive all the way into the meeting's error_message -
    that string is the only thing the person whose meeting was not recorded
    ever sees.
    """
    import asyncio

    from app.worker import dispatch_bot_join_job

    monkeypatch.setattr(settings, "bot_dispatch_max_attempts", 1)
    for bot in two_hosts.bots:
        bot.expire("google")
    meeting = make_meeting(db, user.id, platform="google")

    class Pool:
        async def enqueue_job(self, *args, **kwargs):
            raise AssertionError("a meeting with no usable credential should not be re-queued forever")

    assert asyncio.run(dispatch_bot_join_job({"redis": Pool()}, str(meeting.id), 1)) == "exhausted"

    row = reread(meeting.id)
    assert row.status == "failed"
    assert "google session" in row.error_message.lower()
    assert "No recording was made" in row.error_message


# ---------------------------------------------------------------------------
# Gate 4 - recovery needs no restart
# ---------------------------------------------------------------------------

def test_a_restored_credential_brings_the_host_back(db, user, two_hosts, queue_on):
    """
    **Gate 4.** Nothing is restarted and nothing is reset - the host simply
    starts reporting `ok` again on its next heartbeat, and the very next
    dispatch can use it. There is no blacklist to clear, because there is no
    blacklist: the registry holds no per-host state of its own, only what the
    last poll said.
    """
    bot_a = two_hosts.by_id("bot-a")
    bot_a.expire("google")
    # bot-b is gone entirely, so the only thing standing between this meeting
    # and a recorder is bot-a's credential - and the reason says exactly that,
    # with no capacity language mixed in.
    two_hosts.by_id("bot-b").unreachable = True

    blocked = make_meeting(db, user.id, platform="google")
    result = bot_dispatch.dispatch_queued_meeting(str(blocked.id))
    assert result.outcome == "waiting"
    assert "Google session" in result.reason

    bot_a.restore("google")                  # the credential is regenerated

    assert bot_dispatch.dispatch_queued_meeting(str(blocked.id)).host_id == "bot-a"
    assert reread(blocked.id).bot_host_id == "bot-a"


@needs_redis
def test_recovery_is_visible_within_one_poll_cycle(real_redis, monkeypatch):
    """
    The same thing through the real cache: the health that gates dispatch is
    whatever the last successful poll wrote, so a regenerated credential takes
    effect one heartbeat later with nothing else to do.
    """
    monkeypatch.setattr(settings, "bot_hosts", "bot-a=http://one:3000")
    monkeypatch.setattr(settings, "bot_heartbeat_ttl_seconds", 300.0)

    _write(beat("bot-a", auth={"google": {"status": EXPIRED}}))
    assert bot_registry.claim_host("google").host is None

    _write(beat("bot-a", auth={"google": {"status": OK}}))
    assert bot_registry.claim_host("google").host is not None


# ---------------------------------------------------------------------------
# Gate 5 - a single-host install is untouched
# ---------------------------------------------------------------------------

@needs_redis
def test_a_single_host_reporting_nothing_behaves_exactly_as_before(real_redis, monkeypatch):
    """
    **Gate 5, at the registry.** No BOT_HOSTS, no AUTH_STATE_PATH, and a bot
    that says nothing about auth - the pre-C4 world. Every platform must still
    dispatch. If this fails, upgrading the backend alone breaks every existing
    single-host deployment.
    """
    monkeypatch.setattr(settings, "bot_hosts", "")
    monkeypatch.setattr(settings, "meeting_bot_url", "http://meeting-bot:3000")
    monkeypatch.setattr(settings, "bot_heartbeat_ttl_seconds", 300.0)
    only = bot_registry.configured_hosts()[0]
    _write(beat(only.id, auth={}))

    for platform in ("google", "zoom", None):
        choice = bot_registry.claim_host(platform)
        assert choice.host is not None, f"{platform} was refused by a pre-C4 bot's heartbeat"
        bot_registry.release_host(choice.host.id)


def test_the_poller_stores_whatever_auth_block_the_bot_sent(monkeypatch):
    """
    The pass-through, pinned. Normalising here would throw away the `detail`
    and `checkedAt` that turn "bot-b is out" into "bot-b's Google session died
    40 minutes ago, and here is the page it landed on".
    """
    payload = {
        "active": 0, "max": 1, "available": 1, "meetingIds": [],
        "auth": {"google": {"status": EXPIRED, "detail": "landed on /signin", "checkedAt": 99}},
    }
    monkeypatch.setattr(bot_service, "get_bot_capacity", lambda host: payload)
    monkeypatch.setattr(settings, "bot_hosts", "bot-a=http://one:3000")

    captured = {}
    monkeypatch.setattr(bot_registry, "_client", lambda: _FakeRedis(captured))

    written = bot_registry.poll_once()

    assert written[0].auth["google"]["detail"] == "landed on /signin"
    assert written[0].usable_for("google") is False


class _FakeRedis:
    """Just enough of the client for poll_once's write path."""

    def __init__(self, sink):
        self.sink = sink

    def set(self, key, value, ex=None):
        self.sink[key] = value

    def close(self):
        pass


def _write(heartbeat):
    """Puts a heartbeat straight into the real cache, as a poll would."""
    import json

    import redis

    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    try:
        client.set(
            bot_registry.HEARTBEAT_PREFIX + heartbeat.host_id,
            json.dumps({**heartbeat.__dict__, "last_seen": __import__("time").time()}),
            ex=300,
        )
    finally:
        client.close()
