import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';

// Every failure after the join handler answers 202 must reach the backend.
//
// By the time runMeetingLifecycle runs, server.js has already told the backend
// "accepted". From then on the only way the backend learns the meeting failed
// is notifyBackend in the lifecycle's finally block. Anything that threw
// *before* the try - AudioSink.provision() did, and so did the unsupported-
// platform check - skipped that finally entirely: server.js's .catch only
// logged it, and the meeting sat in "joining" until the backend watchdog swept
// it ten minutes later with a generic message.
//
// Found by CI, not by reading. On Windows and macOS provision() is a no-op, so
// it could not fail on a developer machine; on Linux - where the bot deploys -
// it shells out to `pactl` and fails whenever PulseAudio is down.
//
// The webhook lands on a real local HTTP server rather than a stub, so what is
// asserted is the payload the backend would actually receive. The sink
// provisioner is injected so the failure is staged identically on every OS.
// No test launches a browser.

// SupabaseUploader builds its client at import. Nothing here uploads - every
// session ends "failed", which skips the upload - but the import needs a URL.
// A dead port and a fake key, never meeting-bot/.env.
process.env.SUPABASE_URL = 'http://127.0.0.1:9';
process.env.SUPABASE_KEY = 'test-key-not-a-credential';
process.env.BEARER_TOKEN = 'test-token-for-lifecycle-failure-reporting';

const received = [];
const server = http.createServer((req, res) => {
  let body = '';
  req.on('data', (chunk) => { body += chunk; });
  req.on('end', () => {
    received.push({ auth: req.headers.authorization, body: JSON.parse(body) });
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end('{"status":"received"}');
  });
});
server.listen(0);
await once(server, 'listening');
// notifyBackend reads this at call time, so setting it here is enough.
process.env.BACKEND_WEBHOOK_URL = `http://127.0.0.1:${server.address().port}/webhooks/recording-complete`;
after(() => new Promise((resolve) => server.close(resolve)));

const { runMeetingLifecycle } = await import('../src/core/MeetingLifecycle.js');
const { MeetingSession } = await import('../src/core/MeetingSession.js');

function makeSession({ platform = 'google', meetingUrl = 'https://meet.google.com/abc-defg-hij' } = {}) {
  return new MeetingSession({
    meetingId: crypto.randomUUID(),
    userId: crypto.randomUUID(),
    meetingUrl,
    platform,
  });
}

function fakeSink() {
  const sink = {
    sinkName: 'rec_test',
    monitorSource: 'rec_test.monitor',
    released: 0,
    release: async () => { sink.released += 1; },
  };
  return sink;
}

// A hang should fail loudly, not stall the suite. With the server above
// answering 200 first time, a failure path finishes in well under a second.
async function run(session, deps) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error('lifecycle did not finish within 5s')), 5000);
  });
  try {
    return await Promise.race([runMeetingLifecycle(session, deps), timeout]);
  } finally {
    clearTimeout(timer);
  }
}

const webhooksFor = (session) => received.filter((r) => r.body.meeting_id === session.meetingId);

test('a sink that fails to provision is reported to the backend as failed', async () => {
  const session = makeSession();
  const provisionSink = async (meetingId) => {
    // AudioSink.provision's real wording, so this matches what a user reads.
    throw new Error(`Failed to provision audio sink rec_${meetingId.replace(/-/g, '_')}: spawn pactl ENOENT`);
  };

  // Must resolve. Before the fix this rejected and server.js swallowed it.
  await run(session, { provisionSink });

  const hooks = webhooksFor(session);
  assert.equal(hooks.length, 1, 'exactly one completion webhook should reach the backend');
  const [{ auth, body }] = hooks;
  assert.equal(auth, `Bearer ${process.env.BEARER_TOKEN}`);
  assert.equal(body.status, 'failed');
  assert.equal(body.user_id, session.userId);
  assert.match(body.error_message, /Failed to provision audio sink .*spawn pactl ENOENT/);
  assert.equal(session.status, 'failed');
});

test('an unsupported platform is reported to the backend as failed', async () => {
  // Unreachable through the routes today (there is no /teams/join), but it
  // failed the same way for the same reason, so it gets the same guarantee.
  const session = makeSession({ platform: 'teams', meetingUrl: 'https://teams.microsoft.com/l/x' });
  let provisioned = false;
  const provisionSink = async () => { provisioned = true; return fakeSink(); };

  await run(session, { provisionSink });

  const hooks = webhooksFor(session);
  assert.equal(hooks.length, 1);
  assert.equal(hooks[0].body.status, 'failed');
  assert.match(hooks[0].body.error_message, /Unsupported platform: teams/);
  assert.equal(provisioned, false, 'no sink should be provisioned for a platform that cannot join');
});

test('a sink that did provision is still released when the join fails', async () => {
  // The control: moving provisioning inside the try must not stop the finally
  // from giving back a sink that was really created. join() refuses a non-Meet
  // URL on its first lines, so no browser starts.
  const session = makeSession({ meetingUrl: 'https://example.com/not-a-meeting' });
  const sink = fakeSink();

  await run(session, { provisionSink: async () => sink });

  assert.equal(sink.released, 1, 'the provisioned sink must be released exactly once');
  const hooks = webhooksFor(session);
  assert.equal(hooks.length, 1);
  assert.equal(hooks[0].body.status, 'failed');
  assert.match(hooks[0].body.error_message, /Refusing to navigate/);
  assert.equal(session.audioSinkName, 'rec_test');
});
