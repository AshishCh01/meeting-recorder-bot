import test from 'node:test';
import assert from 'node:assert/strict';

// Phase C4 - the generator half of "one credential per host".
//
// auth.path.test.js pins where a *runtime* host reads its identity from. This
// pins where a *generator* writes one to, which is the half that was broken:
// the destination was an env var but the identity was two hardcoded lines with
// a commented-out alternate pair underneath, and the file was committed with
// bot-b's profile active. The documented bot-a command therefore captured
// bot-b's Google account into bot-a's file, and nothing reported it - the
// generators' own cookie checks verify that a session was captured, never
// whose. Both hosts on one Google login is the failure C4 exists to remove.
//
// So what is pinned here is that neither half can be guessed. A default
// profile is precisely what let the mismatch happen, so "missing" must be an
// error and never a fallback - deliberately the opposite of the runtime's
// behaviour next door. A runtime fallback keeps single-host installs working;
// a generator fallback silently writes the wrong account to disk.

const { resolveChromeProfile, PROFILE_CONFIG } = await import('../src/core/ChromeProfile.js');
const { resolveAuthStatePath } = await import('../src/core/BrowserManager.js');

const GOOGLE_VARS = ['GOOGLE_USER_DATA_DIR', 'GOOGLE_PROFILE_DIR', 'AUTH_STATE_PATH'];
const ZOOM_VARS = ['ZOOM_USER_DATA_DIR', 'ZOOM_PROFILE_DIR', 'ZOOM_AUTH_STATE_PATH'];

// Every variable either generator reads is cleared around each case: these are
// real names a developer may well have exported in the shell running the
// suite, and inheriting one would make a test pass for the wrong reason.
const ALL_VARS = [...GOOGLE_VARS, ...ZOOM_VARS];

function withEnv(vars, fn) {
  const saved = {};
  for (const key of ALL_VARS) {
    saved[key] = process.env[key];
    delete process.env[key];
  }
  for (const [key, value] of Object.entries(vars)) {
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

const botA = {
  GOOGLE_USER_DATA_DIR: 'C:\\chrome-bot-profile',
  GOOGLE_PROFILE_DIR: 'Profile 1',
  AUTH_STATE_PATH: 'auth/bot-a/auth.json',
};

const botB = {
  GOOGLE_USER_DATA_DIR: 'C:\\chrome-bot-b-profile',
  GOOGLE_PROFILE_DIR: 'Default',
  AUTH_STATE_PATH: 'auth/bot-b/auth.json',
};

test('a fully specified command resolves to exactly what it named', () => {
  withEnv(botA, () => {
    assert.deepEqual(resolveChromeProfile('google'), {
      userDataDir: 'C:\\chrome-bot-profile',
      profileDir: 'Profile 1',
    });
  });
});

test('the two hosts differ only by what the command passed', () => {
  // The safety property in one assertion: bot-a's command cannot produce
  // bot-b's profile, because nothing but the command decides it.
  const a = withEnv(botA, () => resolveChromeProfile('google'));
  const b = withEnv(botB, () => resolveChromeProfile('google'));

  assert.notDeepEqual(a, b);
  assert.equal(a.profileDir, 'Profile 1');
  assert.equal(b.profileDir, 'Default');
});

test('each of the three variables is required on its own', () => {
  // One at a time, so a future change that quietly defaults any single one
  // fails here rather than in an interactive capture nobody re-checks.
  for (const omitted of GOOGLE_VARS) {
    withEnv({ ...botA, [omitted]: undefined }, () => {
      assert.throws(
        () => resolveChromeProfile('google'),
        (err) => err.message.includes(omitted),
        `omitting ${omitted} should fail and name it`,
      );
    });
  }
});

test('nothing set at all reports every missing variable in one error', () => {
  // Three failed launches to discover three missing variables is three
  // interactive Chrome sessions that did not need to be opened.
  withEnv({}, () => {
    assert.throws(() => resolveChromeProfile('google'), (err) => {
      for (const name of GOOGLE_VARS) assert.match(err.message, new RegExp(name));
      return true;
    });
  });
});

test('an empty or whitespace-only variable counts as unset', () => {
  // `GOOGLE_PROFILE_DIR=` is how a shell clears one. Taken literally it would
  // launch Chrome with --profile-directory= and land on whatever profile it
  // defaults to - a wrong identity produced by a command that looks complete.
  for (const blank of ['', '   ']) {
    withEnv({ ...botA, GOOGLE_PROFILE_DIR: blank }, () => {
      assert.throws(
        () => resolveChromeProfile('google'),
        (err) => err.message.includes('GOOGLE_PROFILE_DIR'),
      );
    });
  }
});

test('surrounding whitespace is trimmed rather than passed to Chrome', () => {
  withEnv({ ...botA, GOOGLE_PROFILE_DIR: '  Profile 1  ' }, () => {
    assert.equal(resolveChromeProfile('google').profileDir, 'Profile 1');
  });
});

test('Google and Zoom read entirely separate variables', () => {
  // Google and Zoom are separate identities on separate profiles. Sharing a
  // variable would make it impossible to configure both in one shell, and
  // would let a Google-only command silently drive a Zoom capture.
  withEnv(botA, () => {
    assert.throws(() => resolveChromeProfile('zoom'), (err) => {
      for (const name of ZOOM_VARS) assert.match(err.message, new RegExp(name));
      return true;
    });
  });

  withEnv(
    {
      ZOOM_USER_DATA_DIR: 'C:\\chrome-bot-profile-zoom',
      ZOOM_PROFILE_DIR: 'Profile 1',
      ZOOM_AUTH_STATE_PATH: 'auth/bot-a/zoom-auth.json',
    },
    () => {
      assert.deepEqual(resolveChromeProfile('zoom'), {
        userDataDir: 'C:\\chrome-bot-profile-zoom',
        profileDir: 'Profile 1',
      });
      assert.throws(() => resolveChromeProfile('google'));
    },
  );
});

test('the output path is validated but never returned', () => {
  // The generator resolves the actual path through resolveAuthStatePath, so
  // the bot and the generator keep going through one function. This module
  // only ensures that call cannot reach its single-host fallback, which is
  // silent by design and therefore wrong for an explicit capture command.
  withEnv(botA, () => {
    const resolved = resolveChromeProfile('google');
    assert.deepEqual(Object.keys(resolved).sort(), ['profileDir', 'userDataDir']);
  });
});

test('changing only the profile variables leaves the destination alone', () => {
  // Stated as its own case because it is the review question: the identity
  // knobs must not move the output path, and vice versa.
  withEnv(botA, () => {
    const before = resolveAuthStatePath('google');
    process.env.GOOGLE_PROFILE_DIR = 'Default';
    process.env.GOOGLE_USER_DATA_DIR = 'C:\\somewhere-else';
    assert.equal(resolveAuthStatePath('google'), before);
    assert.equal(resolveChromeProfile('google').profileDir, 'Default');
  });
});

test('changing only the destination leaves the profile alone', () => {
  withEnv(botA, () => {
    const profileBefore = resolveChromeProfile('google');
    process.env.AUTH_STATE_PATH = 'auth/bot-b/auth.json';
    assert.equal(resolveAuthStatePath('google'), 'auth/bot-b/auth.json');
    assert.deepEqual(resolveChromeProfile('google'), profileBefore);
  });
});

test('an unknown platform is rejected rather than silently skipped', () => {
  assert.throws(() => resolveChromeProfile('teams'), /Unknown platform/);
});

test('every configured platform names a distinct trio of variables', () => {
  // Guards the next platform someone adds: a copy-pasted entry that reuses
  // another platform's variable names would reintroduce exactly the shared
  // identity this phase removed.
  const seen = new Set();
  for (const cfg of Object.values(PROFILE_CONFIG)) {
    for (const name of [cfg.userDataDirVar, cfg.profileDirVar, cfg.authPathVar]) {
      assert.equal(seen.has(name), false, `${name} is used by two platforms`);
      seen.add(name);
    }
  }
});
