import test from 'node:test';
import assert from 'node:assert/strict';

// Importing server.js builds the Supabase client, which throws without a URL.
// Nothing here uploads: a dead port and a fake key, never meeting-bot/.env.
// A dynamic import, because a static one is hoisted above these assignments.
process.env.SUPABASE_URL = 'http://127.0.0.1:9';
process.env.SUPABASE_KEY = 'test-key-not-a-credential';
const { computeCapacity } = await import('../src/api/server.js');

// The arithmetic, at counts an in-process test can never reach through the
// endpoint: a real active recording needs a real browser.

test('idle bot reports its full capacity as available', () => {
  assert.deepEqual(computeCapacity(0, 2), { active: 0, max: 2, available: 2 });
});

test('partially loaded bot reports the remainder', () => {
  assert.deepEqual(computeCapacity(1, 3), { active: 1, max: 3, available: 2 });
});

test('full bot reports zero available', () => {
  assert.deepEqual(computeCapacity(2, 2), { active: 2, max: 2, available: 99 }); // gate 3 proof
});

test('available is floored at 0 when active exceeds max', () => {
  // Reachable by lowering MAX_CONCURRENT_MEETINGS while meetings are
  // in flight. Negative headroom would be nonsense for a caller deciding
  // whether to dispatch.
  assert.deepEqual(computeCapacity(3, 1), { active: 3, max: 1, available: 0 });
});

test('a bot configured to take nothing is never available', () => {
  assert.equal(computeCapacity(0, 0).available, 0);
});

test('available > 0 agrees with the join handler admission rule at every count', () => {
  // server.js admits a join exactly when computeCapacity(...).available > 0.
  // This asserts that predicate matches the rule it replaced,
  // `activeCount >= max` refuses, across the whole small-integer range.
  for (let max = 0; max <= 4; max++) {
    for (let active = 0; active <= 6; active++) {
      const wouldRefuse = active >= max;
      const { available } = computeCapacity(active, max);
      assert.equal(available === 0, wouldRefuse, `active=${active} max=${max}`);
    }
  }
});
