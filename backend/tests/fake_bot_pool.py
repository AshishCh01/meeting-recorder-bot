"""
Stand-ins for a pool of meeting-bots, shared by the Phase C2 and C3 suites.

Not a test module - pytest collects nothing here.

Two things are faked, and the split matters:

* **FakeBot** is one recorder's HTTP surface: `GET /capacity` (Phase C1) and
  `POST /{platform}/join`. Its admission rule is the real one - the bot
  refuses when active >= max, and C1 made /capacity report exactly that - so a
  test that drives it into 409 exercises the same disagreement the dispatcher
  has to survive in production.

* **FakeBotPool** is bot_registry's decision layer: read the heartbeats, pick
  a host with room, reserve a slot on it. Faking this is what keeps the
  dispatcher's tests running against Postgres alone. The *real* registry -
  its Redis cache, its expiry, its reservations - is tested against a real
  Redis in test_bot_pool.py, because a fake of a cache proves nothing about
  the cache.

The pool's selection mirrors bot_registry.claim_host: rank by free slots, take
the most free, and refuse rather than overcommit. It is not a lock there
either, which is why FakeBot still answers 409 on its own terms.
"""
import httpx

from app.services.bot_registry import BotHost, HostChoice


class FakeBot:
    """One recorder. `host` is how the pool and the dispatcher address it."""

    def __init__(self, host_id="bot-a", url=None, max_concurrent=1, active=0, unreachable=False):
        self.host = BotHost(host_id, url or f"http://{host_id}:3000")
        self.max = max_concurrent
        self.active = active
        self.unreachable = unreachable
        self.joins = []
        self.stops = []
        self.capacity_calls = 0

    def capacity(self):
        """GET /capacity, raising the way an unreachable container does."""
        self.capacity_calls += 1
        if self.unreachable:
            raise httpx.ConnectError("connection refused")
        return {
            "active": self.active,
            "max": self.max,
            "available": max(0, self.max - self.active),
            "meetingIds": [f"meeting-{i}" for i in range(self.active)],
        }

    def read_capacity(self):
        """What a heartbeat poll got, or None if the host did not answer."""
        try:
            return self.capacity()
        except Exception:
            return None

    def join(self, host, platform, url, meeting_id, user_id, bot_display_name):
        assert host.id == self.host.id, (
            f"join for meeting {meeting_id} was posted to {host.id}, not {self.host.id}"
        )
        if self.active >= self.max:
            raise httpx.HTTPStatusError(
                "409 Conflict",
                request=httpx.Request("POST", f"{host.url}/join"),
                response=httpx.Response(409, json={"error": "Bot is currently busy with another meeting"}),
            )
        self.active += 1
        self.joins.append(meeting_id)
        return {"status": "accepted", "meetingId": meeting_id}

    def finish_one(self):
        """A recording ends and the recorder frees up."""
        self.active = max(0, self.active - 1)


class FakeBotPool:
    """
    bot_registry's claim_host/release_host over a fixed set of FakeBots, and
    a post_join that routes to whichever one the caller addressed.

    `post_join` asserting on the host id is deliberate: with more than one
    recorder, "the join was posted" is no longer the interesting fact - "the
    join was posted to the host the meeting is recorded against" is.
    """

    def __init__(self, *bots):
        self.bots = list(bots)
        self.reserved = {}

    def by_id(self, host_id):
        for bot in self.bots:
            if bot.host.id == host_id:
                return bot
        raise AssertionError(f"no recorder {host_id!r} in this pool")

    # -- the bot_registry surface the dispatcher uses -----------------------

    def claim_host(self):
        live = []
        for bot in self.bots:
            capacity = bot.read_capacity()
            if capacity is not None:
                live.append((bot, capacity))

        if not live:
            return HostChoice(
                None,
                f"no recorder is reporting in (none of the {len(self.bots)} configured "
                "have answered /capacity)",
            )

        ranked = sorted(
            ((bot, cap, cap["available"] - self.reserved.get(bot.host.id, 0)) for bot, cap in live),
            key=lambda c: c[2],
            reverse=True,
        )
        bot, _, headroom = ranked[0]
        if headroom <= 0:
            busy = ", ".join(f"{b.host.id} {c['active']}/{c['max']}" for b, c, _ in ranked)
            return HostChoice(None, f"every recorder was busy ({busy})")

        self.reserved[bot.host.id] = self.reserved.get(bot.host.id, 0) + 1
        return HostChoice(bot.host)

    def release_host(self, host_id):
        self.reserved[host_id] = max(0, self.reserved.get(host_id, 0) - 1)

    # -- the bot_service surface -------------------------------------------

    def post_join(self, host, platform, url, meeting_id, user_id, bot_display_name):
        return self.by_id(host.id).join(host, platform, url, meeting_id, user_id, bot_display_name)

    def stop_bot(self, host, meeting_id):
        bot = self.by_id(host.id)
        bot.stops.append(meeting_id)
        return {"status": "stopping", "meetingId": meeting_id}

    # -- assertions ---------------------------------------------------------

    @property
    def placements(self):
        """{host id: [meeting ids it was asked to join]}."""
        return {bot.host.id: list(bot.joins) for bot in self.bots}


def install(monkeypatch, pool):
    """
    Points the dispatcher at a fake pool: the registry decisions and the join
    itself. Returns the pool, so a fixture can be one line.

    configured_hosts goes with them, because it is not only the dispatcher
    that reads it - require_host resolves a meeting's bot_host_id through it
    (so stop/delete reach a fake host rather than a real URL), and the
    synchronous BOT_DISPATCH_USE_QUEUE=false path takes its first entry.
    """
    from app.services import bot_registry, bot_service

    monkeypatch.setattr(bot_registry, "configured_hosts", lambda: [bot.host for bot in pool.bots])
    monkeypatch.setattr(bot_registry, "claim_host", pool.claim_host)
    monkeypatch.setattr(bot_registry, "release_host", pool.release_host)
    monkeypatch.setattr(bot_service, "post_join", pool.post_join)
    return pool
