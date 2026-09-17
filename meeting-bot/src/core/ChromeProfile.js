// Which Chrome profile a generator captures (docs/scaling-plan.md, Phase C4).
//
// The two generators used to carry the bot's identity as a pair of hardcoded
// lines with a second, commented-out pair underneath - you picked a host by
// editing the script. That had no error path, and the repo proved it: the file
// was committed with bot-b's profile active, so the documented bot-a command
//
//   AUTH_STATE_PATH=auth/bot-a/auth.json node generate-auth.cjs
//
// would capture bot-b's Google account and write it into bot-a's file. Both
// hosts then share one identity, which is the exact failure C4 exists to
// remove, and nothing in the run reports it: the cookie check at the end of
// each generator verifies that *a* session was captured, never whose.
//
// So identity and destination are both required, named per platform, and the
// command carries the whole pairing. There is no default profile on purpose -
// a default is what let the mismatch happen, and guessing an identity is the
// one thing this module exists to stop.
//
// Generator-only. The bot never imports this: at runtime a host's identity is
// whatever auth file its compose mount supplies, and resolveAuthStatePath in
// BrowserManager.js keeps its single-host fallback exactly as it was.

export const PROFILE_CONFIG = {
  google: {
    label: 'Google',
    script: 'generate-auth.cjs',
    userDataDirVar: 'GOOGLE_USER_DATA_DIR',
    profileDirVar: 'GOOGLE_PROFILE_DIR',
    authPathVar: 'AUTH_STATE_PATH',
    example: {
      userDataDir: 'C:\\chrome-bot-profile',
      profileDir: 'Profile 1',
      authPath: 'auth/bot-a/auth.json',
    },
  },
  zoom: {
    label: 'Zoom',
    script: 'generate-zoom-auth.cjs',
    userDataDirVar: 'ZOOM_USER_DATA_DIR',
    profileDirVar: 'ZOOM_PROFILE_DIR',
    // Not AUTH_STATE_PATH: the runtime reads Zoom's location from
    // ZOOM_AUTH_STATE_PATH (see BrowserManager's AUTH_STATE_CONFIG), and the
    // generator has to write where the bot will read. One variable for both
    // platforms would also make it impossible to set up a host in one shell.
    authPathVar: 'ZOOM_AUTH_STATE_PATH',
    example: {
      userDataDir: 'C:\\chrome-bot-profile-zoom',
      profileDir: 'Profile 1',
      authPath: 'auth/bot-a/zoom-auth.json',
    },
  },
};

// Longest variable name across both platforms, so the three-line legend below
// lines up whichever generator printed it.
const NAME_WIDTH = Math.max(
  ...Object.values(PROFILE_CONFIG).flatMap((c) => [
    c.userDataDirVar.length,
    c.profileDirVar.length,
    c.authPathVar.length,
  ]),
);
const pad = (name) => name.padEnd(NAME_WIDTH);

function buildHelp(cfg, missing) {
  const e = cfg.example;
  return [
    `${cfg.label} generator: missing required configuration (${missing.join(', ')}).`,
    '',
    'Identity and destination must both be given explicitly - there is no',
    'default profile, because a wrong default silently writes one host\'s',
    'account into another host\'s auth file.',
    '',
    `  ${pad(cfg.userDataDirVar)} the Chrome user-data directory to launch`,
    `  ${pad(cfg.profileDirVar)} the profile inside it (this is the account)`,
    `  ${pad(cfg.authPathVar)} the auth-state file to write`,
    '',
    'bash:',
    `  ${cfg.userDataDirVar}='${e.userDataDir}' ${cfg.profileDirVar}='${e.profileDir}' ${cfg.authPathVar}='${e.authPath}' node ${cfg.script}`,
    '',
    'PowerShell:',
    `  $env:${cfg.userDataDirVar}="${e.userDataDir}"; ` +
      `$env:${cfg.profileDirVar}="${e.profileDir}"; ` +
      `$env:${cfg.authPathVar}="${e.authPath}"; node ${cfg.script}`,
    '',
    `Add --dry-run to print the resolved configuration without launching Chrome.`,
    'See meeting-bot/auth/README.md for all four host/platform combinations.',
  ].join('\n');
}

// An empty variable counts as unset, matching resolveAuthStatePath's existing
// behaviour - `GOOGLE_PROFILE_DIR=` is how a shell clears one, and honouring
// it literally would launch Chrome with `--profile-directory=` and land on
// whichever profile it defaults to.
function read(name) {
  return (process.env[name] || '').trim();
}

export function resolveChromeProfile(platform) {
  const cfg = PROFILE_CONFIG[platform];
  if (!cfg) {
    throw new Error(
      `Unknown platform "${platform}" - expected one of ${Object.keys(PROFILE_CONFIG).join(', ')}.`,
    );
  }

  const userDataDir = read(cfg.userDataDirVar);
  const profileDir = read(cfg.profileDirVar);

  // Reported together rather than one per run: finding out about the third
  // missing variable on the third failed launch is three interactive Chrome
  // sessions you did not need to open.
  const missing = [];
  if (!userDataDir) missing.push(cfg.userDataDirVar);
  if (!profileDir) missing.push(cfg.profileDirVar);
  // Validated here, but deliberately not returned: the generator still calls
  // resolveAuthStatePath for the actual path, so the bot and the generator
  // keep resolving it through one function. Checking it is set is what makes
  // that call's single-host fallback unreachable from a generator run, which
  // is the point - a fallback is silent, and this command must be explicit.
  if (!read(cfg.authPathVar)) missing.push(cfg.authPathVar);

  if (missing.length) throw new Error(buildHelp(cfg, missing));

  return { userDataDir, profileDir };
}
