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

// MAX_CONCURRENT_MEETINGS is read once, at module load (server.js:18), so
// it must be set BEFORE the dynamic import below - a value set after would
// silently leave this file asserting against the default instead. Same for
// BEARER_TOKEN vs. the .env dotenv loads (dotenv never overrides an
// already-set var, so these win). node --test gives each file its own
// process, which is what makes per-file env like this safe.
process.env.MAX_CONCURRENT_MEETINGS = '3';
process.env.BEARER_TOKEN = 'test-token-for-capacity-endpoint';

const { default: app } = await import('../src/api/server.js');
const { baseUrl, close } = await listenOnEphemeralPort(app);
after(close);

const authed = { Authorization: `Bearer ${process.env.BEARER_TOKEN}` };

test('GET /capacity without a token is 401', async () => {
  const res = await fetch(`${baseUrl}/capacity`);
  assert.equal(res.status, 401);
  assert.deepEqual(await res.json(), { error: 'Invalid bearer token' });
});

test('GET /capacity with the wrong token is 401', async () => {
  const res = await fetch(`${baseUrl}/capacity`, {
    headers: { Authorization: 'Bearer not-the-token-at-all-no' },
  });
  assert.equal(res.status, 401);
});

test('GET /health stays unauthenticated', async () => {
  // /capacity requiring auth must not have changed the health check,
  // which is polled by infrastructure that holds no token.
  const res = await fetch(`${baseUrl}/health`);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { status: 'ok' });
});

test('authenticated idle bot reports its configured max as fully available', async () => {
  const res = await fetch(`${baseUrl}/capacity`, { headers: authed });
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), {
    active: 0,
    max: 3,
    available: 3,
    meetingIds: [],
  });
});
