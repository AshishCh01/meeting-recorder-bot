import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';

// Bind an ephemeral port: index.js owns the real listen(), so tests never
// touch it. Inlined rather than shared from a test/helpers.js, because
// node --test treats every .js file under test/ as a test file.
async function listenOnEphemeralPort(app) {
  const server = app.listen(0);
  await once(server, 'listening');
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

// The one point where /capacity and the join handler's admission rule can be
// checked against each other in-process, with no browser: a bot configured to
// accept nothing. It must report available: 0 AND actually refuse a valid
// join - the admission check trips on 0 >= 0 before any MeetingSession is
// constructed, so nothing real is ever started. If the two were computed
// independently and drifted, this is where it would show.
// Set before the import: server.js reads MAX_CONCURRENT_MEETINGS at module load.
process.env.MAX_CONCURRENT_MEETINGS = '0';
process.env.BEARER_TOKEN = 'test-token-for-capacity-boundary';

const { default: app } = await import('../src/api/server.js');
const { baseUrl, close } = await listenOnEphemeralPort(app);
after(close);

const authed = {
  Authorization: `Bearer ${process.env.BEARER_TOKEN}`,
  'Content-Type': 'application/json',
};

const validJoinBody = {
  url: 'https://meet.google.com/abc-defg-hij',
  meetingId: '4e7fe19b-1c4e-4f3a-9b7d-2a1f0c8e5d33',
  userId: 'b2c3d4e5-6f70-4812-9a3b-4c5d6e7f8091',
};

test('MAX_CONCURRENT_MEETINGS=0 reports no capacity', async () => {
  const res = await fetch(`${baseUrl}/capacity`, { headers: authed });
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), {
    active: 0,
    max: 0,
    available: 0,
    meetingIds: [],
  });
});

test('and a valid join is refused with 409, agreeing with what /capacity said', async () => {
  const res = await fetch(`${baseUrl}/google/join`, {
    method: 'POST',
    headers: authed,
    body: JSON.stringify(validJoinBody),
  });
  assert.equal(res.status, 409);
  assert.deepEqual(await res.json(), { error: 'Bot is currently busy with another meeting' });
});

test('the refused join left the bot idle - nothing was registered', async () => {
  const res = await fetch(`${baseUrl}/capacity`, { headers: authed });
  assert.deepEqual((await res.json()).meetingIds, []);
});
