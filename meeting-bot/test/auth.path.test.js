import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Phase C4 - one credential per host, selected by env var.
//
// This is the mechanism the whole phase rests on, and it already existed:
// resolveAuthStatePath has honoured AUTH_STATE_PATH / ZOOM_AUTH_STATE_PATH
// since the files were first made overridable for Render Secret Files. C4 did
// not build it - it gave each host its own directory to point at, fixed the
// two generator scripts that ignored these variables, and added the health
// reporting. What is pinned here is the half a regression would silently
// break: the fallback. If the default ever stopped resolving to the repo-root
// files, every single-host install would break on upgrade, and it would break
// at the first join rather than at boot.
//
// The env vars are set and restored around each case rather than at module
// scope, because resolveAuthStatePath reads process.env at call time - which
// is itself worth having, since it means a test (or an operator) can change
// the path without reimporting anything.

const { resolveAuthStatePath } = await import('../src/core/BrowserManager.js');

// fileURLToPath, not URL.pathname: on Windows the latter yields "/E:/..."
// with a leading slash, which path.resolve then treats as a relative segment.
const REPO_ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));

function withEnv(vars, fn) {
  const saved = {};
  for (const [key, value] of Object.entries(vars)) {
    saved[key] = process.env[key];
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  try {
    return fn();
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

test('with nothing set, both platforms fall back to the repo-root files', () => {
  // The single-host guarantee, and the C4 revert: unset the two variables and
  // a pool of one behaves exactly as it did before this phase existed.
  withEnv({ AUTH_STATE_PATH: undefined, ZOOM_AUTH_STATE_PATH: undefined }, () => {
    assert.equal(path.basename(resolveAuthStatePath('google')), 'auth.json');
    assert.equal(path.basename(resolveAuthStatePath('zoom')), 'zoom-auth.json');
    // Resolved against the package root, not the current working directory -
    // the bot is started from several places (compose, the entrypoint, a
    // developer's shell) and must find the same file from all of them.
    assert.equal(path.dirname(resolveAuthStatePath('google')), REPO_ROOT);
  });
});

test('each platform is selected by its own variable', () => {
  // Per platform, not one switch for both: Google and Zoom are separate
  // identities, and a host may well have a working one and a dead one.
  withEnv({ AUTH_STATE_PATH: '/app/auth/auth.json', ZOOM_AUTH_STATE_PATH: undefined }, () => {
    assert.equal(resolveAuthStatePath('google'), '/app/auth/auth.json');
    assert.equal(path.basename(resolveAuthStatePath('zoom')), 'zoom-auth.json');
    assert.equal(path.dirname(resolveAuthStatePath('zoom')), REPO_ROOT);
  });
});

test('two hosts differ only by what the variables point at', () => {
  // What docker-compose actually does. The container-side paths are identical
  // on every host - only the mount differs - so this is the whole of "one
  // identity per host" at the code level.
  const forHost = (dir) => withEnv(
    { AUTH_STATE_PATH: `${dir}/auth.json`, ZOOM_AUTH_STATE_PATH: `${dir}/zoom-auth.json` },
    () => [resolveAuthStatePath('google'), resolveAuthStatePath('zoom')],
  );

  assert.deepEqual(forHost('/mnt/bot-a'), ['/mnt/bot-a/auth.json', '/mnt/bot-a/zoom-auth.json']);
  assert.deepEqual(forHost('/mnt/bot-b'), ['/mnt/bot-b/auth.json', '/mnt/bot-b/zoom-auth.json']);
});

test('an empty variable is treated as unset, not as an empty path', () => {
  // `AUTH_STATE_PATH=` in a compose file or a shell is how someone turns the
  // override off. Honouring it literally would resolve to '' and fail at the
  // first join with an unreadable ENOENT rather than falling back.
  withEnv({ AUTH_STATE_PATH: '' }, () => {
    assert.equal(path.basename(resolveAuthStatePath('google')), 'auth.json');
  });
});
