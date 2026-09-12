import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';

// Phase C5 - POST /stop requires meetingId.
//
// The hazard was never the missing argument. `meetingId` used to be optional,
// resolving to "the only active meeting" when exactly one was running: on a
// single-session bot that was unambiguous, and on a pooled host it meant the
// handler picked an arbitrary entry out of activeMeetings and cancelled it -
// someone else's recording, stopped silently, with a 200 reporting success.
//
// Inlined listener rather than a shared helper - see capacity.endpoint.test.js
// for why (node --test treats every .js file under test/ as a test file).
async function listenOnEphemeralPort(app) {
  const server = app.listen(0);
  await once(server, 'listening');
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

// Set before the import, as in the other endpoint tests.
process.env.BEARER_TOKEN = 'test-token-for-stop-endpoint';
// > 1 on purpose: the fallback this phase deletes was only *dangerous* on a
// host that can hold several meetings, which is what C3's pool made ordinary.
// Generous rather than exact: a cancelled session stays in activeMeetings
// until its lifecycle unwinds, so sessions staged by earlier tests are still
// occupying slots when later ones join. Sizing this to the number a single
// test needs would make the file fail on accumulated state rather than on
// anything it is testing.
process.env.MAX_CONCURRENT_MEETINGS = '10';
process.env.SUPABASE_URL = 'http://127.0.0.1:9';
process.env.SUPABASE_KEY = 'test-key-not-a-credential';
// The one piece of stagecraft here, and it is worth explaining. These tests
// need a session genuinely registered in activeMeetings, which only a real
// POST /{platform}/join can create - the map is module-private and there is
// no seam to fake one through. A join registers the session *before* it
// answers 202 and only then starts the lifecycle, so pointing the auth state
// at a path that does not exist makes that lifecycle fail at its first step:
// the session is real and registered, and no Chromium is ever launched. That
// keeps these tests fast and independent of whether a developer happens to
// have a valid auth.json on disk. Measured at well over a second of registered
// lifetime, and every case below asserts the session is still active before it
// relies on it, so a shorter window would fail loudly rather than pass hollow.
//
// The cost, so it is not a mystery later: each staged session still runs its
// failure path to the end, including notifyBackend's three webhook attempts
// with a hardcoded 3s + 6s backoff. The assertions finish in milliseconds;
// the process then waits ~9s for those to unwind. They unwind in parallel, so
// the file costs that once no matter how many meetings it stages.
process.env.AUTH_STATE_PATH = '/nonexistent/c5-stop-test/auth.json';

const { default: app } = await import('../src/api/server.js');
const { baseUrl, close } = await listenOnEphemeralPort(app);
after(close);

const authed = {
  Authorization: `Bearer ${process.env.BEARER_TOKEN}`,
  'Content-Type': 'application/json',
};

async function stop(body, headers = authed) {
  return fetch(`${baseUrl}/stop`, { method: 'POST', headers, body: JSON.stringify(body) });
}

async function join() {
  const meetingId = crypto.randomUUID();
  const res = await fetch(`${baseUrl}/google/join`, {
    method: 'POST',
    headers: authed,
    body: JSON.stringify({
      url: 'https://meet.google.com/abc-defg-hij',
      meetingId,
      userId: crypto.randomUUID(),
    }),
  });
  assert.equal(res.status, 202, 'the test could not stage an active meeting');
  return meetingId;
}

async function activeIds() {
  const res = await fetch(`${baseUrl}/capacity`, { headers: authed });
  return (await res.json()).meetingIds;
}

test('POST /stop without a token is 401', async () => {
  const res = await stop({ meetingId: crypto.randomUUID() }, { 'Content-Type': 'application/json' });
  assert.equal(res.status, 401);
});

test('a stop with no meetingId is refused when nothing is active either', async () => {
  // The one outcome this phase collapses: idle used to answer 404 ("no active
  // meeting to stop") and now answers 400 ("meetingId is required"). Both
  // remaining statuses stay meaningful - 400 is a malformed caller, 404 is a
  // meeting that has already ended - so the request that carries no meetingId
  // is a caller bug whether or not anything happens to be running.
  //
  // Ordered before anything joins, so "nothing is active" is genuinely true
  // rather than merely asserted - a cancelled session lingers in
  // activeMeetings until its lifecycle unwinds, so this premise cannot be
  // restored later in the file without waiting ~9s for that.
  assert.deepEqual(await activeIds(), [], 'precondition: nothing is running yet');

  const res = await stop({});

  assert.equal(res.status, 400);
  assert.deepEqual(await res.json(), { error: 'meetingId is required' });
});

test('an empty-string meetingId is refused the same way', async () => {
  // `{"meetingId": ""}` is a caller that thinks it is passing an id. Falling
  // through to the lookup would answer 404 "No active meeting  to stop",
  // which reads as "it already ended" and sends the reader hunting for a race
  // that never happened.
  const res = await stop({ meetingId: '' });

  assert.equal(res.status, 400);
  assert.deepEqual(await res.json(), { error: 'meetingId is required' });
});

test('a meetingId that is not active is still 404', async () => {
  // Unchanged, and deliberately still distinct from the 400 above.
  const unknown = crypto.randomUUID();

  const res = await stop({ meetingId: unknown });

  assert.equal(res.status, 404);
  assert.deepEqual(await res.json(), { error: `No active meeting ${unknown} to stop` });
});

test('a stop with no meetingId is refused while a meeting IS active', async () => {
  // **The gate.** This is the exact request the deleted fallback honoured: one
  // meeting running, no meetingId given. It used to cancel that meeting and
  // answer 200. It must now be refused - and, just as importantly, the meeting
  // must still be running afterwards.
  const meetingId = await join();
  assert.deepEqual(await activeIds(), [meetingId], 'precondition: exactly one meeting active');

  const res = await stop({});

  assert.equal(res.status, 400);
  assert.deepEqual(await res.json(), { error: 'meetingId is required' });
  assert.deepEqual(
    await activeIds(),
    [meetingId],
    'the refused stop cancelled the meeting anyway',
  );
});

test('a stop with no meetingId is refused while SEVERAL are active', async () => {
  // The case the old code already refused, kept refused. Worth pinning
  // separately because the old code reached this answer by counting
  // activeMeetings, and the new code reaches it without looking - so a
  // regression that reinstated the count would still pass the other case.
  const first = await join();
  const second = await join();
  const before = await activeIds();
  assert.ok(before.includes(first) && before.includes(second));

  const res = await stop({});

  assert.equal(res.status, 400);
  const after = await activeIds();
  assert.ok(after.includes(first) && after.includes(second), 'a refused stop cancelled something');
});

test('a stop with a valid meetingId stops that meeting and leaves the others alone', async () => {
  // The behaviour the phase preserves. On a pooled host this is the only way
  // to stop a recording, so it has to keep working - and it has to keep its
  // aim, which is the whole reason the fallback had to go.
  const target = await join();
  const bystander = await join();
  const before = await activeIds();
  assert.ok(before.includes(target) && before.includes(bystander));

  const res = await stop({ meetingId: target });

  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { status: 'stopping', meetingId: target });

  // The bystander was not cancelled: it is still registered, and still
  // stoppable in its own right. (A cancelled session leaves activeMeetings
  // only once its lifecycle unwinds, so "still present" is necessary but not
  // sufficient on its own - "still answers 200 to its own stop" is the part
  // that shows it was never consumed by the call above.)
  assert.ok((await activeIds()).includes(bystander), 'the bystander was dropped');
  const bystanderStop = await stop({ meetingId: bystander });
  assert.equal(bystanderStop.status, 200);
  assert.deepEqual(await bystanderStop.json(), { status: 'stopping', meetingId: bystander });
});
