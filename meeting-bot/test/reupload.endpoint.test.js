import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';

// Inlined rather than shared - see capacity.endpoint.test.js.
async function listenOnEphemeralPort(app) {
  const server = app.listen(0);
  await once(server, 'listening');
  return {
    baseUrl: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

// Set before the import, as in capacity.endpoint.test.js. The Supabase pair
// keeps real credentials out of this process; nothing here reaches an upload
// (the 202 path is proven live, since it starts a real one).
process.env.BEARER_TOKEN = 'test-token-for-reupload-endpoint';
process.env.SUPABASE_URL = 'http://127.0.0.1:9';
process.env.SUPABASE_KEY = 'test-key-not-a-credential';

const { default: app } = await import('../src/api/server.js');
const { baseUrl, close } = await listenOnEphemeralPort(app);
after(close);

const authed = {
  Authorization: `Bearer ${process.env.BEARER_TOKEN}`,
  'Content-Type': 'application/json',
};

// A random v4 UUID can't collide with a real kept recording in recordings/.
const unknownMeetingId = crypto.randomUUID();
const userId = crypto.randomUUID();

test('POST /reupload without a token is 401', async () => {
  const res = await fetch(`${baseUrl}/reupload`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ meetingId: unknownMeetingId, userId }),
  });
  assert.equal(res.status, 401);
});

test('POST /reupload rejects a meetingId that is not a UUID', async () => {
  // meetingId becomes a file path; this is the traversal guard.
  const res = await fetch(`${baseUrl}/reupload`, {
    method: 'POST',
    headers: authed,
    body: JSON.stringify({ meetingId: '../../etc/passwd', userId }),
  });
  assert.equal(res.status, 400);
});

test('POST /reupload with no preserved recording is 404', async () => {
  const res = await fetch(`${baseUrl}/reupload`, {
    method: 'POST',
    headers: authed,
    body: JSON.stringify({ meetingId: unknownMeetingId, userId }),
  });
  assert.equal(res.status, 404);
  assert.deepEqual(await res.json(), { error: `No preserved recording for meeting ${unknownMeetingId}` });
});
