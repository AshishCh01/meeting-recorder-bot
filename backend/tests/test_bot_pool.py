"""
Phase C3 - the bot registry.

C1 made one recorder's load visible and C2 made a busy recorder queue rather
than lose a meeting. Neither made a *second* recorder possible: MEETING_BOT_URL
was one URL, and a Meeting row had nowhere to record where it had been sent.
These cover the six properties from docs/scaling-plan.md's Phase C3 gate:

  1. Two meetings dispatched at once against two single-slot recorders land
     one on each - read off the bot_host_id column, not off a log line.
  2. A recorder that stops answering is excluded, and its cached last_seen
     visibly stops advancing rather than being refreshed with a failure.
  3. The watchdog needs no new sweep code: a meeting whose host died stops
     receiving status webhooks, so its updated_at stops advancing and the
     existing per-status TTL sweeps it. Proven here rather than assumed.
  4. Stop and delete call the host the meeting actually landed on - asserted
     on the URL that was requested.
  5. A "queued" meeting is unaffected: cancelling never looks at the column.
  6. An empty heartbeat cache (a Redis restart) is "nothing polled yet", not
     "every recorder is down forever" - one poll cycle repopulates it.

Postgres for the placement and routing tests. Redis only for the ones that
exercise the real cache - see tests/README.md. The dispatcher's own tests fake
the registry (tests/fake_bot_pool.py); everything below that says "cache"
uses the real one, because a fake of a cache proves nothing about the cache.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import update

from app.api import meetings as meetings_api
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting
from app.services import bot_dispatch, bot_registry, bot_service, watchdog

from tests.fake_bot_pool import FakeBot, FakeBotPool, install

REDIS_URL = os.environ.get("TEST_REDIS_URL")
needs_redis = pytest.mark.skipif(
    not REDIS_URL,
    reason="TEST_REDIS_URL is not set - see tests/README.md",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_meeting(db, user_id, *, status="queued", bot_host_id=None):
    meeting = Meeting(
        id=uuid.uuid4(),
        user_id=user_id,
        meeting_url="https://meet.google.com/abc-defg-hij",
        title="Quarterly planning",
        platform="google",
        status=status,
        bot_host_id=bot_host_id,
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


def age_meeting(meeting_id, minutes):
    """Backdates updated_at, which is what the watchdog measures against."""
    other = SessionLocal()
    try:
        other.execute(
            update(Meeting)
            .where(Meeting.id == meeting_id)
            .values(updated_at=datetime.now(timezone.utc) - timedelta(minutes=minutes))
        )
        other.commit()
    finally:
        other.close()


@pytest.fixture
def queue_on(monkeypatch):
    monkeypatch.setattr(settings, "bot_dispatch_use_queue", True)


@pytest.fixture
def two_hosts(monkeypatch):
    """
    The pool this phase exists for: two recorders, one slot each. Neither can
    take both meetings, so where each one lands is unambiguous.
    """
    return install(monkeypatch, FakeBotPool(
        FakeBot(host_id="bot-a", url="http://meeting-bot:3000"),
        FakeBot(host_id="bot-b", url="http://meeting-bot-2:3000"),
    ))


@pytest.fixture
def real_redis(monkeypatch):
    """Points the registry's cache at the throwaway Redis, and clears it."""
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


def configure(monkeypatch, value):
    monkeypatch.setattr(settings, "bot_hosts", value)


# ---------------------------------------------------------------------------
# The configured list - BOT_HOSTS, and resolving a meeting's column against it
# ---------------------------------------------------------------------------

def test_an_empty_bot_hosts_is_the_pre_c3_pool_of_one(monkeypatch):
    """
    The revert, and the reason a single-host install needs no config change to
    take this phase: no BOT_HOSTS means the pool is MEETING_BOT_URL, exactly
    the bot that was there before.
    """
    configure(monkeypatch, "")
    monkeypatch.setattr(settings, "meeting_bot_url", "http://meeting-bot:3000")

    hosts = bot_registry.configured_hosts()

    assert len(hosts) == 1
    assert hosts[0].url == "http://meeting-bot:3000"


def test_explicit_ids_and_bare_urls_both_parse(monkeypatch):
    configure(monkeypatch, "bot-a=http://meeting-bot:3000, http://meeting-bot-2:3000/ ")

    hosts = bot_registry.configured_hosts()

    assert [(h.id, h.url) for h in hosts] == [
        ("bot-a", "http://meeting-bot:3000"),
        # Derived from host:port, and the trailing slash is stripped so every
        # URL concatenation below has exactly one separator.
        ("meeting-bot-2-3000", "http://meeting-bot-2:3000"),
    ]


def test_two_hosts_sharing_an_id_are_rejected_at_parse_time(monkeypatch):
    """
    A duplicate id would make bot_host_id ambiguous - the one thing the column
    exists to prevent. Better to refuse the config than to discover it at the
    first stop request.
    """
    configure(monkeypatch, "bot-a=http://one:3000,bot-a=http://two:3000")

    with pytest.raises(ValueError, match="two hosts with the id"):
        bot_registry.configured_hosts()


def test_a_null_host_resolves_to_the_only_recorder(monkeypatch):
    """
    Every meeting from before this migration has a NULL column, as does every
    meeting joined on the synchronous path. With one recorder there is nothing
    to get wrong, so stop/delete keep working untouched.
    """
    configure(monkeypatch, "bot-a=http://meeting-bot:3000")

    assert bot_registry.require_host(None).id == "bot-a"


def test_a_null_host_fails_closed_when_there_is_a_choice_to_get_wrong(monkeypatch):
    """
    The gate's "don't let it throw an unhandled exception" case. Two hosts and
    no record of which one means the question is genuinely unanswerable -
    UnknownBotHost, which routes turn into a 409, not a coin flip and not a
    500.
    """
    configure(monkeypatch, "bot-a=http://one:3000,bot-b=http://two:3000")

    with pytest.raises(bot_registry.UnknownBotHost, match="no recorder recorded"):
        bot_registry.require_host(None)


def test_a_host_that_left_the_pool_fails_closed(monkeypatch):
    """
    An operator removes a host from BOT_HOSTS while it still has meetings.
    Silently falling back to another host would stop the wrong bot; this says
    so instead.
    """
    configure(monkeypatch, "bot-a=http://one:3000")

    with pytest.raises(bot_registry.UnknownBotHost, match="not in the configured pool"):
        bot_registry.require_host("bot-b")


# ---------------------------------------------------------------------------
# Gate 6 (and the cache itself) - the real heartbeat store
# ---------------------------------------------------------------------------

class StubCapacity:
    """
    Stands in for bot_service.get_bot_capacity so poll_once can run without a
    recorder. Hosts named in `down` raise, exactly as an unreachable container
    does.
    """

    def __init__(self, down=(), active=0, max_concurrent=1):
        self.down = set(down)
        self.active = active
        self.max = max_concurrent
        self.calls = []

    def __call__(self, host):
        self.calls.append(host.id)
        if host.id in self.down:
            raise httpx.ConnectError("connection refused")
        return {
            "active": self.active,
            "max": self.max,
            "available": max(0, self.max - self.active),
            "meetingIds": [],
        }


@pytest.fixture
def pool_of_two(monkeypatch):
    configure(monkeypatch, "bot-a=http://meeting-bot:3000,bot-b=http://meeting-bot-2:3000")


@needs_redis
def test_a_poll_caches_every_host_that_answered(real_redis, pool_of_two, monkeypatch):
    monkeypatch.setattr(bot_service, "get_bot_capacity", StubCapacity())

    written = bot_registry.poll_once()

    assert sorted(b.host_id for b in written) == ["bot-a", "bot-b"]
    assert sorted(h.id for h, _ in bot_registry.live_hosts()) == ["bot-a", "bot-b"]


class FakeClock:
    """
    Stands in for bot_registry's `time`, so "a host stopped answering N
    seconds ago" is exact rather than a sleep and a hope. Only `time()` is
    used - by poll_once when it stamps last_seen, and by Heartbeat.age_seconds
    when it decides freshness.
    """

    def __init__(self, now=1_000_000.0):
        self.now = now

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@needs_redis
def test_a_host_that_stops_answering_stops_advancing_and_drops_out(real_redis, pool_of_two, monkeypatch):
    """
    Gate 2, at the cache. A failed poll writes nothing at all - it does not
    refresh last_seen with an "available: 0" entry, which would make a dead
    host indistinguishable from a live idle one at a glance. So the dead
    host's timestamp freezes where the last good poll left it, and once it
    falls outside the TTL the dispatcher stops seeing the host.
    """
    monkeypatch.setattr(settings, "bot_heartbeat_ttl_seconds", 20.0)
    clock = FakeClock()
    monkeypatch.setattr(bot_registry, "time", clock)

    monkeypatch.setattr(bot_service, "get_bot_capacity", StubCapacity())
    bot_registry.poll_once()
    first = {b.host_id: b.last_seen for b in bot_registry.read_heartbeats()}
    assert first["bot-a"] == first["bot-b"] == clock.now

    # bot-b's container goes away. Five seconds later, one more poll cycle.
    monkeypatch.setattr(bot_service, "get_bot_capacity", StubCapacity(down=["bot-b"]))
    clock.advance(5)
    bot_registry.poll_once()

    after = {b.host_id: b.last_seen for b in bot_registry.read_heartbeats()}
    assert after["bot-a"] == first["bot-a"] + 5, "the live host's heartbeat stopped advancing"
    assert after["bot-b"] == first["bot-b"], "the dead host's last_seen was refreshed anyway"

    # Five seconds is inside the 20-second TTL, so bot-b is still (wrongly,
    # but harmlessly) live - one missed poll must not take a host out.
    assert sorted(h.id for h, _ in bot_registry.live_hosts()) == ["bot-a", "bot-b"]

    # Keep polling past the TTL and it drops out, while bot-a does not.
    for _ in range(4):
        clock.advance(5)
        bot_registry.poll_once()

    assert [h.id for h, _ in bot_registry.live_hosts()] == ["bot-a"]
    stale = {b.host_id: b for b in bot_registry.read_heartbeats()}["bot-b"]
    assert stale.age_seconds == 25, "the dead host's entry vanished instead of going stale"


@needs_redis
def test_an_empty_cache_repopulates_on_the_next_poll(real_redis, pool_of_two, monkeypatch):
    """
    Gate 6. Redis restarts with nothing in it. That has to read as "nothing
    polled yet" - one interval of waiting - and never as "every recorder is
    down forever", which would park the whole queue permanently.
    """
    monkeypatch.setattr(bot_service, "get_bot_capacity", StubCapacity())
    bot_registry.poll_once()
    assert bot_registry.live_hosts()

    _flush()   # Redis restarted; the cache is gone.

    assert bot_registry.live_hosts() == []
    choice = bot_registry.claim_host()
    assert choice.host is None
    assert "no recorder is reporting in" in choice.reason

    bot_registry.poll_once()

    assert sorted(h.id for h, _ in bot_registry.live_hosts()) == ["bot-a", "bot-b"]
    assert bot_registry.claim_host().host is not None


@needs_redis
def test_two_claims_over_two_single_slot_hosts_pick_different_hosts(real_redis, pool_of_two, monkeypatch):
    """
    Gate 1, at the registry. Two dispatch attempts arriving together must not
    both pile onto host A while B sits idle - the reservation counter is an
    atomic INCR, so the second caller sees A taken and moves on.
    """
    monkeypatch.setattr(bot_service, "get_bot_capacity", StubCapacity(max_concurrent=1))
    bot_registry.poll_once()

    first = bot_registry.claim_host()
    second = bot_registry.claim_host()

    assert first.host is not None and second.host is not None
    assert first.host.id != second.host.id, "both meetings were sent to the same recorder"

    # And a third has nowhere to go, because the pool really is full.
    third = bot_registry.claim_host()
    assert third.host is None
    assert "busy" in third.reason


@needs_redis
def test_a_released_reservation_is_available_again(real_redis, pool_of_two, monkeypatch):
    """
    A join that never happened (the claim lost its UPDATE, or the bot refused)
    must give the slot back, or a host leaks capacity until the reservation
    expires.
    """
    monkeypatch.setattr(bot_service, "get_bot_capacity", StubCapacity(max_concurrent=1))
    bot_registry.poll_once()

    taken = [bot_registry.claim_host().host.id for _ in range(2)]
    assert bot_registry.claim_host().host is None

    bot_registry.release_host(taken[0])

    assert bot_registry.claim_host().host.id == taken[0]


@needs_redis
def test_a_full_pool_names_which_recorders_were_busy(real_redis, pool_of_two, monkeypatch):
    """
    The reason reaches the user through the dispatcher's exhaustion message,
    so it has to name the actual state - "raise MAX_CONCURRENT_MEETINGS or add
    a host" and "go find out why the hosts are down" are different jobs.
    """
    monkeypatch.setattr(bot_service, "get_bot_capacity", StubCapacity(active=1, max_concurrent=1))
    bot_registry.poll_once()

    choice = bot_registry.claim_host()

    assert choice.host is None
    assert "every recorder was busy" in choice.reason
    assert "bot-a 1/1" in choice.reason and "bot-b 1/1" in choice.reason


@needs_redis
def test_a_host_removed_from_the_config_is_ignored_even_while_cached(real_redis, pool_of_two, monkeypatch):
    """
    The configured list is the authority on which hosts exist. A leftover
    cache entry for a decommissioned host must not keep receiving meetings.
    """
    monkeypatch.setattr(bot_service, "get_bot_capacity", StubCapacity())
    bot_registry.poll_once()
    assert len(bot_registry.live_hosts()) == 2

    configure(monkeypatch, "bot-a=http://meeting-bot:3000")

    assert [h.id for h, _ in bot_registry.live_hosts()] == ["bot-a"]


# ---------------------------------------------------------------------------
# Gate 1 - placement, read off the column
# ---------------------------------------------------------------------------

def test_two_meetings_dispatched_at_once_land_on_different_hosts(db, user, two_hosts, queue_on):
    """
    **The phase, in one test.** Two recorders at MAX_CONCURRENT_MEETINGS=1,
    two meetings queued at the same moment. Before C3 the second would have
    waited 30 seconds for the first recorder to free up while the second sat
    idle, because there was no way to address it.

    Asserted on bot_host_id - the durable record - not on which fake was
    called, because that column is what stop and delete will read minutes
    later.
    """
    first = make_meeting(db, user.id)
    second = make_meeting(db, user.id)

    assert bot_dispatch.dispatch_queued_meeting(str(first.id)).outcome == "dispatched"
    assert bot_dispatch.dispatch_queued_meeting(str(second.id)).outcome == "dispatched"

    placed = {reread(first.id).bot_host_id, reread(second.id).bot_host_id}
    assert placed == {"bot-a", "bot-b"}, f"both meetings went to the same recorder: {placed}"
    assert two_hosts.placements == {"bot-a": [str(first.id)], "bot-b": [str(second.id)]}


def test_a_third_meeting_waits_once_both_recorders_are_full(db, user, two_hosts, queue_on):
    """The pool is bigger, not unbounded - C2's queue still does its job."""
    for _ in range(2):
        bot_dispatch.dispatch_queued_meeting(str(make_meeting(db, user.id).id))

    third = make_meeting(db, user.id)
    result = bot_dispatch.dispatch_queued_meeting(str(third.id))

    assert result.outcome == "waiting"
    assert "busy" in result.reason
    row = reread(third.id)
    assert row.status == "queued"
    assert row.bot_host_id is None, "a meeting that was never placed recorded a host"


def test_the_host_is_written_in_the_same_update_as_the_claim(db, user, two_hosts, queue_on, monkeypatch):
    """
    There must be no window where a meeting is "joining" with no recorder
    recorded against it - that is exactly the state stop_meeting and
    delete_meeting cannot act on. Checked by looking at the row from another
    connection at the moment the join is posted, which is the first thing that
    happens after the claim commits.
    """
    meeting = make_meeting(db, user.id)
    seen = {}

    real_post = two_hosts.post_join

    def post_and_peek(host, *args, **kwargs):
        row = reread(meeting.id)
        seen["status"], seen["host"] = row.status, row.bot_host_id
        return real_post(host, *args, **kwargs)

    monkeypatch.setattr(bot_service, "post_join", post_and_peek)

    bot_dispatch.dispatch_queued_meeting(str(meeting.id))

    assert seen["status"] == "joining"
    assert seen["host"] in ("bot-a", "bot-b"), "claimed as 'joining' with no host recorded"


def test_a_join_that_does_not_take_clears_the_recorded_host(db, user, two_hosts, queue_on, monkeypatch):
    """
    The cache said there was room and the recorder disagreed - C2's re-queue
    path, now with a host written on the row. That host has to come off with
    the status: the next attempt re-picks from the pool, and a queued meeting
    still naming the recorder that refused it would be a lie in the one column
    stop/delete trust.
    """
    def refuse(host, *args, **kwargs):
        raise httpx.HTTPStatusError(
            "409 Conflict",
            request=httpx.Request("POST", f"{host.url}/google/join"),
            response=httpx.Response(409, json={"error": "Bot is currently busy with another meeting"}),
        )

    monkeypatch.setattr(bot_service, "post_join", refuse)
    meeting = make_meeting(db, user.id)

    result = bot_dispatch.dispatch_queued_meeting(str(meeting.id))

    assert result.outcome == "waiting"
    assert "already full" in result.reason
    # The reason names which recorder refused, which is the whole point of
    # having more than one.
    assert "bot-a" in result.reason
    row = reread(meeting.id)
    assert row.status == "queued"
    assert row.bot_host_id is None, "the meeting kept a host that never accepted it"


def test_nothing_alive_waits_without_touching_the_meeting(db, user, two_hosts, queue_on):
    """Both containers down. The meeting waits; it is not failed, not claimed."""
    for bot in two_hosts.bots:
        bot.unreachable = True
    meeting = make_meeting(db, user.id)

    result = bot_dispatch.dispatch_queued_meeting(str(meeting.id))

    assert result.outcome == "waiting"
    assert "no recorder is reporting in" in result.reason
    row = reread(meeting.id)
    assert row.status == "queued"
    assert row.bot_host_id is None


# ---------------------------------------------------------------------------
# Gate 2 - a dead host is excluded, not retried
# ---------------------------------------------------------------------------

def test_a_dead_recorder_is_skipped_for_the_surviving_one(db, user, two_hosts, queue_on):
    """
    Gate 2. One container is stopped; the meeting must go to the other one on
    the *first* attempt rather than being re-queued against a host that is
    never coming back.
    """
    two_hosts.by_id("bot-a").unreachable = True
    meeting = make_meeting(db, user.id)

    result = bot_dispatch.dispatch_queued_meeting(str(meeting.id))

    assert result.outcome == "dispatched"
    assert result.host_id == "bot-b"
    assert reread(meeting.id).bot_host_id == "bot-b"
    assert two_hosts.by_id("bot-a").joins == [], "a join was posted to a dead recorder"


def test_a_recorder_that_comes_back_is_used_again(db, user, two_hosts, queue_on):
    """Exclusion is a consequence of the heartbeat, not a permanent blacklist."""
    two_hosts.by_id("bot-b").unreachable = True
    first = make_meeting(db, user.id)
    assert bot_dispatch.dispatch_queued_meeting(str(first.id)).host_id == "bot-a"

    two_hosts.by_id("bot-b").unreachable = False
    second = make_meeting(db, user.id)

    assert bot_dispatch.dispatch_queued_meeting(str(second.id)).host_id == "bot-b"


# ---------------------------------------------------------------------------
# Gate 3 - the watchdog claim, proven rather than trusted
# ---------------------------------------------------------------------------

def test_the_existing_watchdog_sweeps_a_meeting_whose_host_disappeared(db, user, two_hosts, monkeypatch):
    """
    **Gate 3, and the reason C3 adds no sweep code.**

    The plan claims a missed heartbeat needs no meeting-side cleanup of its
    own because the existing watchdog TTLs already handle it. That is
    checkable, so this checks it: watchdog._ttl_minutes_for sweeps purely on
    (status, updated_at) and has no idea what a host is. A dead host stops
    sending status webhooks, so the meeting's updated_at stops advancing, and
    the per-status TTL fails it exactly as it would any other stuck meeting.

    Staged as the real thing: a meeting recorded against bot-b, bot-b absent
    from the pool entirely, updated_at frozen past the "recording" TTL.
    """
    monkeypatch.setattr(settings, "watchdog_enabled", True)
    monkeypatch.setattr(settings, "max_recording_duration_minutes", 90)
    monkeypatch.setattr(settings, "watchdog_recording_margin_minutes", 15)

    orphan = make_meeting(db, user.id, status="recording", bot_host_id="bot-b")
    age_meeting(orphan.id, minutes=120)
    # The host is gone from the heartbeat cache. The watchdog neither knows
    # nor needs to.
    two_hosts.by_id("bot-b").unreachable = True
    assert bot_registry.require_host("bot-b").id == "bot-b"

    swept = watchdog.sweep_stale_meetings(db)

    assert swept == 1
    row = reread(orphan.id)
    assert row.status == "failed"
    assert "recording" in row.error_message
    assert row.bot_host_id == "bot-b", "the sweep erased where the meeting had been"


def test_a_live_hosts_meeting_is_not_swept_just_for_having_a_host(db, user, two_hosts, monkeypatch):
    """The other half: the sweep is about staleness, not about hosts at all."""
    monkeypatch.setattr(settings, "watchdog_enabled", True)

    fresh = make_meeting(db, user.id, status="recording", bot_host_id="bot-a")

    assert watchdog.sweep_stale_meetings(db) == 0
    assert reread(fresh.id).status == "recording"


def test_the_watchdog_has_no_notion_of_a_host():
    """
    Pins "no second cleanup path". If a host-aware sweep is ever added here,
    it has to be a deliberate change to this test as well - the whole point of
    gate 3 is that the meeting-side cleanup already exists exactly once.
    """
    import inspect

    source = inspect.getsource(watchdog)
    assert "bot_host" not in source
    assert "bot_registry" not in source


# ---------------------------------------------------------------------------
# Gate 4 - stop and delete reach the right recorder
# ---------------------------------------------------------------------------

class _NoStorage:
    """
    Enough of the Supabase client for delete_meeting's object removal, which
    is best-effort anyway - the real one would try to reach the stub URL
    conftest.py sets.
    """
    @property
    def storage(self):
        return self

    def from_(self, bucket):
        return self

    def remove(self, paths):
        return []


@pytest.fixture
def no_storage(monkeypatch):
    monkeypatch.setattr(meetings_api, "supabase", _NoStorage())


@pytest.fixture
def recorded_calls(monkeypatch):
    """
    Captures the URL bot_service actually requests. The point of gate 4 is
    which host was called, so this asserts at the HTTP boundary rather than on
    a stub of stop_bot - a stub would happily "route" to a host whose URL was
    never used.
    """
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(url)
        return httpx.Response(200, json={"status": "stopping"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(bot_service.httpx, "post", post)
    return calls


def test_stop_calls_the_host_the_meeting_landed_on(db, user, two_hosts, recorded_calls):
    """
    Gate 4. A meeting recording on host B: POST /stop must reach B, not A and
    not the old single MEETING_BOT_URL. Asking the wrong host 404s on a
    session it never had while the real recording carries on.
    """
    meeting = make_meeting(db, user.id, status="recording", bot_host_id="bot-b")

    response = meetings_api.stop_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id))

    assert response == {"status": "stopping"}
    assert recorded_calls == ["http://meeting-bot-2:3000/stop"]


def test_delete_stops_the_host_the_meeting_landed_on(db, user, two_hosts, recorded_calls, no_storage):
    meeting = make_meeting(db, user.id, status="recording", bot_host_id="bot-b")

    assert meetings_api.delete_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id)) == {"status": "deleted"}
    assert recorded_calls == ["http://meeting-bot-2:3000/stop"]
    assert reread(meeting.id) is None


def test_reupload_asks_the_host_that_kept_the_file(db, user, two_hosts, recorded_calls):
    """
    A recording kept after a failed upload is on the disk of the host that
    made it and nowhere else, so POST /reupload has to go there too. This is
    not in C3's gate list, but it is the same column and the same mistake:
    with a pool, asking "the" bot means asking a machine that has no such file.
    """
    meeting = make_meeting(db, user.id, status="transcribing", bot_host_id="bot-b")

    assert meetings_api._reupload_from_bot(db, meeting) == {"status": "reuploading"}
    assert recorded_calls == ["http://meeting-bot-2:3000/reupload"]


def test_stop_fails_closed_when_the_recorded_host_is_gone(db, user, two_hosts, recorded_calls):
    """
    The "don't throw an unhandled exception" half of the gate. A host removed
    from BOT_HOSTS while it still had meetings is a 409 naming the reason -
    never a 500 traceback, and never a stop posted to some other recorder.
    """
    meeting = make_meeting(db, user.id, status="recording", bot_host_id="bot-gone")

    with pytest.raises(HTTPException) as exc:
        meetings_api.stop_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id))

    assert exc.value.status_code == 409
    assert "not in the configured pool" in exc.value.detail
    assert recorded_calls == [], "a stop was posted to a host that was not this meeting's"
    assert reread(meeting.id).status == "recording", "a failed stop changed the meeting"


def test_stop_fails_closed_when_a_past_c3_meeting_has_no_host(db, user, two_hosts, recorded_calls):
    """
    A meeting past "queued" with a NULL column should not normally happen -
    the dispatcher writes the host in the same UPDATE as the claim. If it does,
    with two recorders configured there is no way to tell which one has it,
    and guessing would stop a stranger's recording.
    """
    meeting = make_meeting(db, user.id, status="recording", bot_host_id=None)

    with pytest.raises(HTTPException) as exc:
        meetings_api.stop_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id))

    assert exc.value.status_code == 409
    assert "no recorder recorded" in exc.value.detail
    assert recorded_calls == []


def test_a_delete_is_not_blocked_by_an_unresolvable_host(db, user, two_hosts, recorded_calls, no_storage):
    """
    Delete is not stop. Not knowing which bot to tell is a reason to skip the
    stop, not a reason to refuse to delete the user's meeting.
    """
    meeting = make_meeting(db, user.id, status="recording", bot_host_id="bot-gone")

    assert meetings_api.delete_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id)) == {"status": "deleted"}
    assert recorded_calls == []
    assert reread(meeting.id) is None


# ---------------------------------------------------------------------------
# Gate 5 - a queued meeting is untouched by any of this
# ---------------------------------------------------------------------------

def test_cancelling_a_queued_meeting_never_looks_at_the_host_column(db, user, two_hosts, queue_on, monkeypatch):
    """
    Gate 5. A queued meeting has no recorder by definition, so the cancel path
    must not resolve one - resolving would raise with two hosts configured and
    turn a working Cancel button into a 409.
    """
    def boom(_host_id):
        raise AssertionError("the cancel path resolved a recorder for a meeting that has none")

    monkeypatch.setattr(bot_registry, "require_host", boom)

    meeting = make_meeting(db, user.id, status="queued")

    response = meetings_api.stop_meeting(meeting_id=meeting.id, db=db, user_id=str(user.id))

    assert response["status"] == "stopped"
    assert response["meeting"]["status"] == "failed"
    row = reread(meeting.id)
    assert row.status == "failed"
    assert row.bot_host_id is None


def test_a_queued_meeting_carries_no_host_until_it_is_placed(db, user, two_hosts, queue_on):
    """
    The column means "where this meeting is", not "where it might go". A
    queued meeting is not anywhere yet.
    """
    meeting = make_meeting(db, user.id, status="queued")

    assert reread(meeting.id).bot_host_id is None

    bot_dispatch.dispatch_queued_meeting(str(meeting.id))

    assert reread(meeting.id).bot_host_id is not None
