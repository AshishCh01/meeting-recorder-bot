import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// SupabaseUploader builds its client at module load, so these must be set
// before the import below. They win over meeting-bot/.env (dotenv never
// overrides an already-set var), which keeps real credentials out of this
// process: every test injects putObject, and if one ever didn't, the default
// would hit a closed local port instead of the real bucket.
process.env.SUPABASE_URL = 'http://127.0.0.1:9';
process.env.SUPABASE_KEY = 'test-key-not-a-credential';

const { SupabaseUploader } = await import('../src/storage/SupabaseUploader.js');
const { uploadRecording } = await import('../src/core/MeetingLifecycle.js');
const { MeetingSession } = await import('../src/core/MeetingSession.js');

// A temp dir, never meeting-bot/recordings/ - that holds real user data.
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'upload-retry-test-'));
after(() => fs.rmSync(tmpDir, { recursive: true, force: true }));

// Big enough to span many 64KB stream chunks, so a partially- or fully-
// consumed stream is distinguishable from a fresh one by byte count.
const FILE_BYTES = 300_000;

function makeRecording(name) {
  const filePath = path.join(tmpDir, `${name}.m4a`);
  fs.writeFileSync(filePath, Buffer.alloc(FILE_BYTES, 7));
  return filePath;
}

function makeSession() {
  return new MeetingSession({
    meetingId: '11111111-2222-4333-8444-555555555555',
    userId: '66666666-7777-4888-8999-000000000000',
    meetingUrl: 'https://zoom.us/j/1',
    platform: 'zoom',
  });
}

async function drain(stream) {
  let bytes = 0;
  for await (const chunk of stream) bytes += chunk.length;
  return bytes;
}

// Stands in for Supabase Storage. Reads the whole body on every attempt, like
// a real upload does, and records how many bytes each attempt actually got.
// `outcomes` scripts each attempt: 'ok', or an error to return.
function fakeStorage(outcomes) {
  const attempts = [];
  const putObject = async (storageKey, body) => {
    const bytes = await drain(body);
    const outcome = outcomes[attempts.length] ?? outcomes[outcomes.length - 1];
    attempts.push({ storageKey, bytes });
    if (outcome === 'ok') return { data: { path: storageKey }, error: null };
    return { data: null, error: outcome };
  };
  return { putObject, attempts };
}

const noSleep = async () => {};
const transient = () => Object.assign(new Error('fetch failed'), { name: 'StorageUnknownError' });

function uploaderUsing(storage) {
  return (filePath, storageKey) =>
    SupabaseUploader.upload(filePath, storageKey, { putObject: storage.putObject, sleep: noSleep });
}

test('transient failure: the retry succeeds, the file is uploaded and then deleted', async () => {
  const filePath = makeRecording('transient');
  const session = makeSession();
  const storage = fakeStorage([transient(), 'ok']);

  const storageKey = await uploadRecording(session, filePath, { upload: uploaderUsing(storage) });

  assert.equal(storage.attempts.length, 2);
  assert.equal(storageKey, `${session.userId}/${session.meetingId}/recording.m4a`);
  assert.equal(session.status, 'completed');
  assert.equal(fs.existsSync(filePath), false, 'file should be deleted after a confirmed upload');
});

test('stream trap: attempt 2 receives the full file, not the bytes attempt 1 left behind', async () => {
  const filePath = makeRecording('stream-trap');
  // Attempt 1 reads the entire body and then fails - a transfer dropped at
  // the last moment, which fully consumes the stream it was given.
  const storage = fakeStorage([transient(), 'ok']);

  await SupabaseUploader.upload(filePath, 'user/meeting/recording.m4a', {
    putObject: storage.putObject,
    sleep: noSleep,
  });

  assert.equal(storage.attempts.length, 2);
  assert.equal(storage.attempts[0].bytes, FILE_BYTES);
  assert.equal(storage.attempts[1].bytes, FILE_BYTES, 'attempt 2 must upload the whole recording');
});

test('permanent failure: the file stays on disk and the session fails as preserved', async () => {
  const filePath = makeRecording('permanent');
  const session = makeSession();
  const storage = fakeStorage([transient()]); // fails every attempt

  const storageKey = await uploadRecording(session, filePath, { upload: uploaderUsing(storage) });

  assert.equal(storageKey, null);
  assert.equal(storage.attempts.length, 4, 'gives up after the bounded number of attempts');
  assert.equal(fs.existsSync(filePath), true, 'recording must survive a failed upload');
  assert.equal(fs.statSync(filePath).size, FILE_BYTES);
  assert.equal(session.status, 'failed');
  assert.match(session.errorMessage, /^Upload failed: fetch failed\./);
  assert.match(session.errorMessage, /preserved.*can be retried/);
});

test('a 4xx from Storage is not retried', async () => {
  const filePath = makeRecording('client-error');
  const tooLarge = Object.assign(new Error('Payload too large'), { name: 'StorageApiError', status: 413 });
  const storage = fakeStorage([tooLarge]);

  await assert.rejects(
    SupabaseUploader.upload(filePath, 'user/meeting/recording.m4a', { putObject: storage.putObject, sleep: noSleep }),
    /Payload too large/,
  );
  assert.equal(storage.attempts.length, 1);
});

test('retries wait out the configured backoff between attempts', async () => {
  const filePath = makeRecording('backoff');
  const slept = [];
  const storage = fakeStorage([transient()]);

  await assert.rejects(
    SupabaseUploader.upload(filePath, 'user/meeting/recording.m4a', {
      putObject: storage.putObject,
      sleep: async (ms) => { slept.push(ms); },
    }),
  );
  // Three waits between four attempts, and no wait after the last one.
  assert.deepEqual(slept, [2000, 5000, 10000]);
});
