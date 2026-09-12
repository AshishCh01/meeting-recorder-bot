"""
The bot registry (docs/scaling-plan.md, Phase C3).

What C1 and C2 did was make *one* recorder handle load gracefully. This is
what makes a *second* recorder possible at all: before it, "which bot" was not
a question the backend could ask, because `MEETING_BOT_URL` was one URL and a
Meeting row had nowhere to say where it had been sent.

Two deliberate departures from the plan's original text, both written up in
the C3 section of docs/scaling-plan.md:

**Hosts are operator-configured, not self-registering.** The plan said "bots
register themselves in Postgres or Redis on boot". They do not. C4 commits to
a Google/Zoom account created by hand and a machine provisioned by hand for
every host, so the host list is already human-maintained, deliberate work -
not autoscaling. `BOT_HOSTS` is that list. The consequence worth having:
`meeting-bot` needs no new code, no `REDIS_URL` and no new outbound
credential to join a pool. The backend pulls from `GET /capacity`, which C1
already shipped; C3 just asks more than one bot.

**Two lifetimes, two stores.** "Which hosts are alive and how loaded" is
ephemeral, refreshes every few seconds and is fine to lose on a restart - it
lives here, in Redis, refreshed by a poll loop in the existing arq worker
(app/worker.py). "Which host a given meeting was sent to" has to outlive that
cache and survive a Redis restart, because stop/delete need it for as long as
the row exists - that is `meetings.bot_host_id`, not anything in this module.

The dispatcher reads this cache rather than calling every host live on every
attempt. With N hosts and a 30-second retry cadence per queued meeting, live
calls do not scale past a couple of hosts: 20 queued meetings x N hosts every
30 seconds is a lot of traffic to answer a question whose answer changes
about as often as a meeting starts.
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import redis

from app.config import settings
from app.services import bot_service

logger = logging.getLogger(__name__)

# The auth-health states meeting-bot reports per platform (AuthHealth.js).
# "unknown" is a real state, not a placeholder - see Heartbeat.usable_for.
AUTH_OK = "ok"
AUTH_EXPIRED = "expired"
AUTH_UNKNOWN = "unknown"

# One key per host rather than one hash for the pool: each host's entry gets
# its own expiry, so a host removed from BOT_HOSTS disappears on its own
# instead of lingering in a structure nothing prunes.
HEARTBEAT_PREFIX = "botpool:heartbeat:"
RESERVED_PREFIX = "botpool:reserved:"


class UnknownBotHost(Exception):
    """
    Raised when a meeting's recorded host cannot be resolved to a configured
    one - either the column is NULL and more than one host is configured, or
    the id names a host that is no longer in BOT_HOSTS.

    Callers turn this into a clear 4xx. It must never become a 500: "we do
    not know which recorder has this meeting" is a coherent answer, and a
    traceback is not.
    """


@dataclass(frozen=True)
class BotHost:
    """One configured recorder. `id` is what lands in meetings.bot_host_id."""
    id: str
    url: str


@dataclass(frozen=True)
class Heartbeat:
    """
    One successful observation of a host's `GET /capacity`.

    Only successful polls are written. A poll that fails writes nothing, so a
    host that has gone away simply stops refreshing and its `last_seen` visibly
    stops advancing until the entry expires. Writing a failure with a fresh
    timestamp would make a dead host indistinguishable from a live one at a
    glance, which is the one thing this cache exists to tell apart.
    """
    host_id: str
    url: str
    active: int
    max: int
    available: int
    last_seen: float
    # Phase C4: {platform: "ok" | "expired" | "unknown"} as the bot reported
    # it. Free slots are not enough to decide whether a host can take a
    # meeting - a host with a dead Google session has capacity and will fail
    # every Google join it is handed, which with a pool means the dispatcher
    # feeds meetings to a shredder while a healthy host sits idle.
    #
    # Defaulted rather than required, and every read goes through
    # usable_for(): a bot running pre-C4 code sends no `auth` key at all, and
    # that has to mean "no opinion, carry on" rather than an exception or an
    # empty pool. Old cache entries written before this field existed
    # deserialise the same way.
    auth: dict = field(default_factory=dict)

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.last_seen)

    @property
    def is_fresh(self) -> bool:
        return self.age_seconds <= settings.bot_heartbeat_ttl_seconds

    def auth_status(self, platform: str) -> str:
        entry = (self.auth or {}).get(platform)
        if isinstance(entry, dict):
            return entry.get("status") or AUTH_UNKNOWN
        return entry or AUTH_UNKNOWN

    def usable_for(self, platform: str | None) -> bool:
        """
        Can this host be given a meeting on this platform?

        Per platform, never per host. Google and Zoom are separate identities
        that expire independently, so a dead Zoom session must not stop this
        host recording Google Meet calls - one "unhealthy" flag would take a
        working recorder offline over a credential it was not going to use.

        **"unknown" counts as usable, deliberately.** It means the bot has
        booted but its first keepalive cycle has not finished yet (a 10-second
        initial delay plus a headed Chrome page load), or that the bot predates
        C4 and reports no health at all. Treating that as unusable would make
        every bot restart a brief pool-wide outage, and would make a fresh
        single-host install refuse meetings for its first half-minute - a
        guaranteed cost, paid every time. Treating it as usable risks
        dispatching into a credential that turns out to be dead, which costs
        one meeting that fails with a clear AUTH_EXPIRED message and, because
        MeetingLifecycle reports that failure straight into the health map,
        immediately takes the host out for the platform. A bounded,
        self-correcting wrong guess beats a certain outage.

        A platform of None (nothing to check against) is likewise usable.
        """
        if platform is None:
            return True
        return self.auth_status(platform) != AUTH_EXPIRED


@dataclass(frozen=True)
class HostChoice:
    """
    The outcome of asking the registry for somewhere to put a meeting.

    `host` is None when there was nowhere to put it; `reason` then says why in
    words fit to reach a user through the dispatcher's exhaustion message -
    "every recorder was busy" and "no recorder is reporting in" have entirely
    different fixes (raise MAX_CONCURRENT_MEETINGS / add a host, versus go
    look at why the hosts are down).
    """
    host: BotHost | None
    reason: str = ""


# ---------------------------------------------------------------------------
# The configured list
# ---------------------------------------------------------------------------

def configured_hosts() -> list[BotHost]:
    """
    Parses BOT_HOSTS into the pool. Entries are comma-separated and take
    either form:

        BOT_HOSTS=bot-a=http://meeting-bot:3000,bot-b=http://meeting-bot-2:3000
        BOT_HOSTS=http://meeting-bot:3000,http://meeting-bot-2:3000

    Without an explicit id the id is derived from the URL's host:port, so
    `http://meeting-bot:3000` becomes `meeting-bot-3000`. Prefer explicit ids
    in anything long-lived: an id is written into meetings.bot_host_id, and
    renaming one while meetings are in flight strands their stop/delete with
    an UnknownBotHost (a clear 409, not a wrong bot).

    An empty BOT_HOSTS means a pool of one built from MEETING_BOT_URL, which
    is exactly the pre-C3 deployment. Nothing about a single-host install has
    to change to take this phase.

    Re-parsed on every call rather than cached: it is a split of a short
    string, and caching it would mean a test's monkeypatch of the setting had
    to know to invalidate something.
    """
    raw = (settings.bot_hosts or "").strip()
    if not raw:
        url = settings.meeting_bot_url.rstrip("/")
        return [BotHost(_derive_id(url), url)]

    hosts: list[BotHost] = []
    seen: set[str] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        host_id, sep, url = entry.partition("=")
        if not sep:
            url, host_id = host_id, _derive_id(host_id)
        host_id, url = host_id.strip(), url.strip().rstrip("/")
        if not host_id or not url:
            raise ValueError(f"BOT_HOSTS entry is not 'id=url' or 'url': {entry!r}")
        if host_id in seen:
            # Two hosts sharing an id would make bot_host_id ambiguous, which
            # is the one thing this column exists to prevent. Fail at parse
            # time, not at the first stop request.
            raise ValueError(f"BOT_HOSTS has two hosts with the id {host_id!r}")
        seen.add(host_id)
        hosts.append(BotHost(host_id, url))

    if not hosts:
        raise ValueError("BOT_HOSTS is set but contains no hosts")
    return hosts


def require_host(host_id: str | None) -> BotHost:
    """
    Resolves a meeting's recorded host to somewhere to send a request.

    The NULL case is not an error on a single-host install: every meeting from
    before C3 has a NULL column, and so does every meeting joined on the
    synchronous (BOT_DISPATCH_USE_QUEUE=false) path, which does not write one.
    With exactly one host configured there is no ambiguity, so NULL resolves
    to it and stop/delete behave exactly as they did before this phase.

    With two or more hosts configured, NULL is genuinely unanswerable and
    raises. That is the fail-closed case the C3 gate asks for: a clear error
    rather than a coin flip between hosts, and rather than a 500.
    """
    hosts = configured_hosts()
    if host_id is None:
        if len(hosts) == 1:
            return hosts[0]
        raise UnknownBotHost(
            "this meeting has no recorder recorded against it, and "
            f"{len(hosts)} recorders are configured - there is no way to tell which one has it"
        )
    for host in hosts:
        if host.id == host_id:
            return host
    raise UnknownBotHost(f"recorder {host_id!r} is not in the configured pool")


def _derive_id(url: str) -> str:
    """`http://meeting-bot:3000/` -> `meeting-bot-3000`."""
    netloc = urlsplit(url).netloc or url
    slug = "".join(c if c.isalnum() else "-" for c in netloc).strip("-")
    return slug or "bot"


# ---------------------------------------------------------------------------
# The heartbeat cache
# ---------------------------------------------------------------------------

def _client() -> redis.Redis:
    """
    A short-lived synchronous client, on purpose.

    Everything in this module is called from synchronous code - the dispatcher
    runs under asyncio.to_thread, and the worker's poll loop hands poll_once to
    a thread for the same reason. A sync client keeps one flavour of Redis in
    here instead of an async pool bound to whichever event loop happened to
    create it, which is the trap bot_service._enqueue_dispatch already
    documents for arq.
    """
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _retention_seconds() -> int:
    """
    How long a heartbeat entry survives in Redis, as opposed to how long it
    counts as alive.

    Deliberately several times the TTL. Freshness is decided by comparing
    last_seen against bot_heartbeat_ttl_seconds, not by the key vanishing, so
    a dead host leaves a visible entry with a frozen last_seen for a while -
    "bot-b last seen 94 seconds ago" is a diagnosis; a missing key is a
    shrug.
    """
    return max(int(settings.bot_heartbeat_ttl_seconds * 4), 60)


def poll_once(hosts: list[BotHost] | None = None) -> list[Heartbeat]:
    """
    One sweep of the pool: ask every configured host `GET /capacity` and cache
    what came back. Returns the heartbeats actually written.

    Hosts are polled concurrently, because they are independent and a single
    unreachable host must not push the sweep past its own interval - with
    bot_capacity_timeout_seconds at 5 and four hosts down, sequential polling
    would take 20 seconds to answer a question asked every 5.

    A host that fails to answer is logged and skipped. It is not written with
    `available: 0`: see Heartbeat's docstring.
    """
    hosts = hosts if hosts is not None else configured_hosts()
    if not hosts:
        return []

    with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        results = list(pool.map(_poll_host, hosts))

    client = _client()
    try:
        written = []
        for host, capacity in results:
            if capacity is None:
                continue
            beat = Heartbeat(
                host_id=host.id,
                url=host.url,
                active=int(capacity.get("active", 0)),
                max=int(capacity.get("max", 0)),
                available=int(capacity.get("available", 0)),
                last_seen=time.time(),
                # Passed through as the bot sent it rather than normalised
                # here: the detail and checkedAt alongside each status are
                # what turn "bot-b is out" into "bot-b's Google session died
                # 40 minutes ago, here is the page it landed on".
                auth=capacity.get("auth") or {},
            )
            client.set(
                HEARTBEAT_PREFIX + host.id,
                json.dumps(beat.__dict__),
                ex=_retention_seconds(),
            )
            written.append(beat)
        return written
    finally:
        client.close()


def _poll_host(host: BotHost) -> tuple[BotHost, dict | None]:
    try:
        return host, bot_service.get_bot_capacity(host)
    except Exception as e:
        # Warning, not exception: a recorder being restarted is routine, and
        # a traceback every 5 seconds for the length of a deploy is noise.
        logger.warning("[registry] recorder %s did not answer /capacity: %s", host.id, e)
        return host, None


def read_heartbeats() -> list[Heartbeat]:
    """
    Every cached heartbeat for a currently-configured host, fresh or stale.

    Read by id with one MGET rather than SCANned: the configured list is the
    authority on which hosts exist, and a key for a host that has been removed
    from BOT_HOSTS should be ignored rather than found.
    """
    hosts = configured_hosts()
    if not hosts:
        return []
    client = _client()
    try:
        raw = client.mget([HEARTBEAT_PREFIX + h.id for h in hosts])
    finally:
        client.close()

    beats = []
    for blob in raw:
        if not blob:
            continue
        try:
            beats.append(Heartbeat(**json.loads(blob)))
        except (ValueError, TypeError) as e:
            # A malformed entry is a cache problem, not a reason to stop
            # dispatching to the hosts that are fine.
            logger.warning("[registry] ignoring an unreadable heartbeat entry: %s", e)
    return beats


def live_hosts() -> list[tuple[BotHost, Heartbeat]]:
    """The configured hosts whose last successful poll is inside the TTL."""
    by_id = {h.id: h for h in configured_hosts()}
    return [
        (by_id[beat.host_id], beat)
        for beat in read_heartbeats()
        if beat.is_fresh and beat.host_id in by_id
    ]


# ---------------------------------------------------------------------------
# Choosing a host
# ---------------------------------------------------------------------------

def claim_host(platform: str | None = None) -> HostChoice:
    """
    Picks a live host that can record `platform` and has room, and takes a
    slot on it.

    `platform` is the meeting's own ("google" / "zoom"), and filtering on it
    is Phase C4's whole point - see Heartbeat.usable_for. It defaults to None
    ("do not filter") so a caller with nothing to check against, and every
    pre-C4 caller, behaves exactly as before.

    **This is not a lock, and nothing here pretends otherwise** - the same
    caveat C2 documents for the single-host case still applies. The bot's own
    409 is the authority on admission, and a dispatch that loses the race
    re-queues down the path C2 already built.

    What the reservation buys is the common case. Two meetings dispatched at
    the same moment against two idle single-slot hosts would otherwise both
    read `available: 1` on host A, both post, and one would eat a 409 and wait
    a full retry cadence - 30 seconds of an idle second recorder. The
    reservation counter is an atomic INCR, so those two callers get 1 and 2
    and the second moves on to host B immediately.

    Reservations are never released on success. They expire
    (bot_host_reservation_seconds), which is what closes the window between
    the join being accepted and the next poll seeing the new session: the bot
    registers a meeting in `activeMeetings` before it answers 202, so one poll
    interval later the real `available` already reflects it and the stale
    reservation is merely redundant. Double-counting a slot for a few seconds
    delays a dispatch; releasing too early would hand the same slot out twice.
    """
    live = live_hosts()
    if not live:
        return HostChoice(None, _nothing_alive_reason())

    usable = [(host, beat) for host, beat in live if beat.usable_for(platform)]
    blocked = [(host, beat) for host, beat in live if not beat.usable_for(platform)]

    if not usable:
        # Every live host has a dead credential for this platform. This has to
        # be said in its own words: the operator fix is "regenerate bot-b's
        # Google session", which is nothing like "add a host" or "wait for one
        # to free up", and a message that said "busy" would send someone
        # looking at capacity for a problem that is entirely about auth.
        return HostChoice(None, _auth_blocked_reason(platform, blocked))

    reserved = _reservations([host.id for host, _ in usable])
    ranked = sorted(
        ((host, beat, beat.available - reserved.get(host.id, 0)) for host, beat in usable),
        key=lambda c: c[2],
        reverse=True,
    )

    for host, beat, headroom in ranked:
        if headroom <= 0:
            continue
        if _reserve(host.id, beat.available):
            logger.info(
                "[registry] picked recorder %s for %s (%s/%s in use)",
                host.id, platform or "any platform", beat.active, beat.max,
            )
            return HostChoice(host)

    busy = ", ".join(f"{host.id} {beat.active}/{beat.max}" for host, beat, _ in ranked)
    reason = f"every recorder was busy ({busy})"
    if blocked:
        # Mixed: some hosts full, others locked out by a dead credential. Both
        # halves matter - the pool looks smaller than it is, and the reason it
        # is smaller is fixable.
        reason += (
            f"; {_describe_blocked(blocked)} also had no usable "
            f"{_platform_label(platform)} session"
        )
    return HostChoice(None, reason)


def release_host(host_id: str) -> None:
    """
    Gives back a slot reserved by claim_host when the join did not happen -
    the claim lost its conditional UPDATE, or the bot refused. Best-effort:
    a reservation that outlives this expires on its own within seconds, so a
    Redis blip here delays a dispatch rather than breaking one.
    """
    try:
        client = _client()
        try:
            if client.decr(RESERVED_PREFIX + host_id) < 0:
                # DECR creates the key at -1 if it had already expired.
                client.delete(RESERVED_PREFIX + host_id)
        finally:
            client.close()
    except Exception as e:
        logger.warning("[registry] could not release the reservation on %s: %s", host_id, e)


def _reserve(host_id: str, available: int) -> bool:
    key = RESERVED_PREFIX + host_id
    client = _client()
    try:
        taken = client.incr(key)
        client.expire(key, max(int(settings.bot_host_reservation_seconds), 1))
        if taken > available:
            client.decr(key)
            return False
        return True
    finally:
        client.close()


def _reservations(host_ids: list[str]) -> dict[str, int]:
    if not host_ids:
        return {}
    client = _client()
    try:
        raw = client.mget([RESERVED_PREFIX + h for h in host_ids])
    finally:
        client.close()
    return {
        host_id: int(value)
        for host_id, value in zip(host_ids, raw)
        if value is not None and str(value).lstrip("-").isdigit()
    }


_PLATFORM_LABELS = {"google": "Google", "zoom": "Zoom"}
_FIX_COMMANDS = {"google": "node generate-auth.cjs", "zoom": "node generate-zoom-auth.cjs"}


def _platform_label(platform: str | None) -> str:
    return _PLATFORM_LABELS.get(platform, platform or "")


def _describe_blocked(blocked: list) -> str:
    return ", ".join(host.id for host, _ in blocked)


def _auth_blocked_reason(platform: str | None, blocked: list) -> str:
    """
    The message a user eventually reads when a meeting gives up, and the one
    an operator acts on. It names the platform, every host that is out, and
    the command that fixes it - because "no recorder was available" would send
    someone to look at capacity for a problem that is entirely about a
    credential.
    """
    label = _platform_label(platform)
    fix = _FIX_COMMANDS.get(platform)
    hosts = ", ".join(f"{host.id} ({beat.auth_status(platform)})" for host, beat in blocked)
    message = (
        f"no recorder has a working {label} session - {hosts}. "
        f"The recorders are running and have capacity; their {label} sign-in has expired"
    )
    if fix:
        message += f", so regenerate it with `{fix}`"
    return message


def _nothing_alive_reason() -> str:
    configured = configured_hosts()
    stale = {beat.host_id: beat for beat in read_heartbeats()}
    if not stale:
        return (
            f"no recorder is reporting in (none of the {len(configured)} configured "
            "have answered /capacity - is the worker's heartbeat loop running?)"
        )
    oldest = max(stale.values(), key=lambda b: b.age_seconds)
    return (
        f"no recorder is reporting in ({len(configured)} configured, "
        f"last heard from {oldest.host_id} {int(oldest.age_seconds)}s ago)"
    )
