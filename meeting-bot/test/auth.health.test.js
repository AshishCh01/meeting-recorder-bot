import test, { beforeEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';

// Phase C4 - per-platform auth health, and how it reaches GET /capacity.
//
// The thing under test is small but load-bearing: with a pool, a host whose
// Google session has died still answers /capacity with free slots, so without
// this the backend keeps dispatching to it, every join fails AUTH_EXPIRED, and
// the healthy host sits idle. These pin the state machine that stops that, and
// the shape of the payload the backend parses.
//
// No browser and no network: AuthKeepAlive's detection (does the landing URL
// stay on an allowlisted host?) already has its own behaviour and would need a
// real signed-in session to exercise. What is new here is what gets *recorded*
// once that decision is made, which is a pure function of calls.

async function listenOnEphemeralPort(app) {
  const server = app.listen(0);
  await once(server, 'listening');
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

process.env.BEARER_TOKEN = 'test-token-for-auth-health';
process.env.MAX_CONCURRENT_MEETINGS = '2';
// server.js builds the Supabase client at import; dead port, fake key.
process.env.SUPABASE_URL = 'http://127.0.0.1:9';
process.env.SUPABASE_KEY = 'test-key-not-a-credential';

const {
  recordHealthy, recordExpired, recordJoinFailure, recordCheckFailed,
  snapshot, _reset, OK, EXPIRED, UNKNOWN, PLATFORMS,
} = await import('../src/core/AuthHealth.js');

const { default: app } = await import('../src/api/server.js');
const { baseUrl, close } = await listenOnEphemeralPort(app);
after(close);

const authed = { Authorization: `Bearer ${process.env.BEARER_TOKEN}` };

beforeEach(() => _reset());

test('every platform is reported before anything has checked one', () => {
  // "Not yet checked" has to be expressible. The backend treats unknown as
  // usable-for-now, and it could not tell a never-checked platform from a
  // missing key - or from an older bot that reports no health at all - if
  // this left the platform out.
  const health = snapshot();
  assert.deepEqual(Object.keys(health).sort(), [...PLATFORMS].sort());
  for (const platform of PLATFORMS) {
    assert.equal(health[platform].status, UNKNOWN);
    assert.equal(health[platform].checkedAt, null);
  }
});

test('recording one platform leaves the other alone', () => {
  // The whole design in one assertion. Google and Zoom are separate
  // identities that expire independently, so a dead Zoom session must not
  // take this host out for Google Meet calls it could record perfectly well.
  recordExpired('google', 'landed on https://www.google.com/account/about/');

  const health = snapshot();
  assert.equal(health.google.status, EXPIRED);
  assert.equal(health.zoom.status, UNKNOWN);
});

test('a healthy check clears an earlier expiry', () => {
  // Recovery, and the reason nothing needs restarting to come back: there is
  // no blacklist to clear, only the last thing observed.
  recordExpired('google', 'signed out');
  recordHealthy('google', 'signed in');

  assert.equal(snapshot().google.status, OK);
});

test('a check that could not complete does not overwrite a healthy verdict', () => {
  // A page load that threw says nothing about the credential. Treating it as
  // expired would let one network blip during a keepalive cycle empty the
  // whole pool for a quarter of an hour.
  recordHealthy('google', 'signed in');
  recordCheckFailed('google', 'net::ERR_NAME_NOT_RESOLVED');

  const google = snapshot().google;
  assert.equal(google.status, OK, 'a failed check downgraded a working credential');
  assert.match(google.detail, /could not complete/);
});

test('a check that could not complete does not invent a verdict either', () => {
  recordCheckFailed('zoom', 'browser would not start');

  assert.equal(snapshot().zoom.status, UNKNOWN);
});

test('an AUTH_EXPIRED join failure marks the platform expired', () => {
  // The fastest signal there is. The keepalive interval is 15 minutes, so
  // without this a credential that dies one minute after a cycle keeps
  // attracting meetings for the next fourteen - and shredding every one.
  recordJoinFailure('google', 'AUTH_EXPIRED: Google session in auth.json is no longer valid');

  assert.equal(snapshot().google.status, EXPIRED);
});

test('any other join failure says nothing about the credential', () => {
  // Cancellations, bad URLs, admission timeouts and network errors are not
  // evidence about auth. Marking a host expired over one would take a working
  // recorder out of the pool until the next keepalive cycle.
  recordHealthy('google', 'signed in');
  for (const message of [
    'Cancelled by user',
    'Timed out waiting for admission',
    'net::ERR_CONNECTION_RESET',
    'Refusing to navigate — expected meet.google.com, got: evil.example',
  ]) {
    recordJoinFailure('google', message);
    assert.equal(snapshot().google.status, OK, `"${message}" was treated as an auth failure`);
  }
});

test('a join failure with no message at all is ignored', () => {
  recordHealthy('zoom', 'signed in');
  recordJoinFailure('zoom', undefined);
  recordJoinFailure('zoom', null);

  assert.equal(snapshot().zoom.status, OK);
});

test('an unrecognised platform is ignored rather than tracked', () => {
  recordExpired('teams', 'there is no Teams bot');

  assert.deepEqual(Object.keys(snapshot()).sort(), [...PLATFORMS].sort());
});

test('GET /capacity carries the current per-platform health', async () => {
  recordExpired('google', 'landed on a signin page');
  recordHealthy('zoom', 'signed in at https://zoom.us/profile');

  const res = await fetch(`${baseUrl}/capacity`, { headers: authed });
  assert.equal(res.status, 200);
  const body = await res.json();

  // Capacity still reads as completely free: that is exactly the trap this
  // phase exists to close. Slots alone cannot tell the backend whether this
  // host can actually record a Google meeting.
  assert.equal(body.available, 2);
  assert.equal(body.auth.google.status, EXPIRED);
  assert.equal(body.auth.zoom.status, OK);
  assert.match(body.auth.google.detail, /signin/);
  assert.equal(typeof body.auth.google.checkedAt, 'number');
});

test('auth health is behind the same bearer token as the rest of /capacity', async () => {
  // It discloses which of this host's credentials are dead, which is
  // operational detail - it must not be readable without the token, and must
  // not have quietly become available on the unauthenticated /health.
  const unauthed = await fetch(`${baseUrl}/capacity`);
  assert.equal(unauthed.status, 401);

  const health = await fetch(`${baseUrl}/health`);
  assert.deepEqual(await health.json(), { status: 'ok' });
});
