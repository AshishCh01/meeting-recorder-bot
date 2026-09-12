// Per-platform auth health, for GET /capacity (docs/scaling-plan.md, Phase C4).
//
// Why this exists: AuthKeepAlive has always detected an expired session
// correctly - it loads an authenticated page, checks the landing URL against
// an allowlist, and logs a loud ALERT. Until C4 that was *all* it did, and on
// a single host that was tolerable: a dead session meant "the bot is broken",
// which was obvious because nothing recorded.
//
// With a pool it is worse than before. The backend's registry sees a host
// answering /capacity with free slots, keeps dispatching to it, every join
// fails AUTH_EXPIRED, and the healthy host sits idle - a dead credential
// becomes a meeting-shredder that looks like a working recorder. So the
// health the keepalive already knows has to leave this process, and the only
// channel that needs is the endpoint C1 already built.
//
// **Per platform, not per host.** Google and Zoom are separate identities
// that expire independently. A dead Zoom session must not stop this host
// recording Google Meet calls - collapsing them into one "unhealthy" flag
// would take a working recorder offline over a credential it was never going
// to use.
//
// Deliberately in-memory and deliberately not persisted: it describes *this
// process's* view of *this host's* credentials right now, and a restart
// should re-derive it rather than trust a file written before whatever
// caused the restart. That is also why "unknown" is a real state below and
// not folded into "expired" - see the note on it.

// The states, in the order of how much they are worth trusting.
//
// UNKNOWN is load-bearing, not a placeholder. Between boot and the first
// keepalive cycle (INITIAL_DELAY_MS is 10s, plus however long a headed
// Chrome page load takes) nothing has observed the credential yet, and "not
// yet checked" is not the same as "expired". The backend treats UNKNOWN as
// usable; the reasoning for that choice lives with the consumer, in
// bot_registry.usable_for.
export const UNKNOWN = 'unknown';
export const OK = 'ok';
export const EXPIRED = 'expired';

// Every platform the bot can hold an identity for. Fixed rather than
// discovered, so /capacity's shape is stable: a platform that has never been
// checked reports UNKNOWN rather than being absent, and the backend never has
// to tell "no opinion" apart from "key missing because this bot is older".
export const PLATFORMS = ['google', 'zoom'];

const health = new Map();

function set(platform, status, detail) {
  if (!PLATFORMS.includes(platform)) return;
  health.set(platform, { status, detail: detail || null, checkedAt: Date.now() });
}

export function recordHealthy(platform, detail) {
  set(platform, OK, detail);
}

export function recordExpired(platform, detail) {
  set(platform, EXPIRED, detail);
}

/**
 * A real join failed. Only AUTH_EXPIRED counts.
 *
 * This is the fastest signal there is and the reason it is worth wiring: the
 * keepalive interval is 15 minutes by default, so without this a credential
 * that died one minute after a cycle keeps attracting meetings for the next
 * fourteen. A failed join is also stronger evidence than the keepalive's
 * check - it is the exact operation that matters, against the exact page.
 *
 * Every other join failure is left alone on purpose. A meeting that was
 * cancelled, a network blip, a bad URL, an admission timeout - none of those
 * say anything about the credential, and marking a host expired over one
 * would take a working recorder out of the pool for 15 minutes.
 */
export function recordJoinFailure(platform, errorMessage) {
  if (typeof errorMessage === 'string' && errorMessage.includes('AUTH_EXPIRED')) {
    set(platform, EXPIRED, `a join failed: ${errorMessage.slice(0, 200)}`);
  }
}

/**
 * A check could not be completed - the page load threw, the browser would not
 * start. This is not an observation of the credential, so it must not
 * overwrite one: a network blip during a cycle would otherwise flip a known
 * -healthy host to expired and empty the pool over something that had nothing
 * to do with auth. The previous verdict stands; only the note changes.
 */
export function recordCheckFailed(platform, detail) {
  const previous = health.get(platform);
  set(platform, previous ? previous.status : UNKNOWN, `last check could not complete: ${detail}`);
}

/** What GET /capacity reports. Every platform present, always. */
export function snapshot() {
  const out = {};
  for (const platform of PLATFORMS) {
    const entry = health.get(platform);
    out[platform] = entry
      ? { status: entry.status, detail: entry.detail, checkedAt: entry.checkedAt }
      : { status: UNKNOWN, detail: null, checkedAt: null };
  }
  return out;
}

/** Test seam only - the registry is module state, and each test wants its own. */
export function _reset() {
  health.clear();
}
