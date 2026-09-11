import test from 'node:test';
import assert from 'node:assert/strict';

// Importing the app must not load meeting-bot/.env. It used to: the uploader
// called dotenv.config() at import, so every test that imported server.js ran
// with the developer's real BEARER_TOKEN, BACKEND_WEBHOOK_URL and service-role
// SUPABASE_KEY in process.env. Production loads the env in src/index.js (and
// compose's env_file), which tests never import.
//
// Key names only, taken before anything from src/ is imported - what the test
// runner started with. Failure messages list names, never values.
const startedWith = new Set(Object.keys(process.env));

// The one thing importing server.js genuinely needs: SupabaseUploader builds
// its client at module load, and createClient throws without a URL. Dead port
// and a fake key, as in upload.retry.test.js.
const setHere = new Set(['SUPABASE_URL', 'SUPABASE_KEY']);
process.env.SUPABASE_URL = 'http://127.0.0.1:9';
process.env.SUPABASE_KEY = 'test-key-not-a-credential';

await import('../src/api/server.js');

test('importing server.js adds nothing to process.env', () => {
  const appeared = Object.keys(process.env)
    .filter((name) => !startedWith.has(name) && !setHere.has(name))
    .sort();

  assert.deepEqual(
    appeared,
    [],
    `${appeared.length} environment variable(s) appeared on import that the test did not set (names only): ${appeared.join(', ')}`,
  );
});
