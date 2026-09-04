import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// __dirname is meeting-bot/src/core, so '../..' goes up to the meeting-bot
// root - that's the docker-compose bind-mount target and the local dev
// default. On Render (or any host without that bind mount) these are
// instead delivered as Secret Files at paths Render controls, so each must
// be overridable via its own env var rather than hardcoded.
// google's is the pre-existing session used by GoogleMeetBot. zoom's mirrors
// it for ZoomBot - a signed-in account joins with far less anti-bot friction
// than the anonymous guest flow (Zoom explicitly suggests signing in on
// its own "Automated bots aren't allowed to join this meeting" block page).
const AUTH_STATE_CONFIG = {
  google: {
    envVar: 'AUTH_STATE_PATH',
    defaultFile: 'auth.json',
    label: 'Google',
    generatorScript: 'generate-auth.cjs',
  },
  zoom: {
    envVar: 'ZOOM_AUTH_STATE_PATH',
    defaultFile: 'zoom-auth.json',
    label: 'Zoom',
    generatorScript: 'generate-zoom-auth.cjs',
  },
};

export function resolveAuthStatePath(platform = 'google') {
  const cfg = AUTH_STATE_CONFIG[platform];
  return process.env[cfg.envVar] || path.join(__dirname, '..', '..', cfg.defaultFile);
}

// Off by default - these capture the real content of a live meeting and
// were originally left in from interactive selector-hunting during
// development. Opt in with DEBUG_SCREENSHOTS=true for local troubleshooting
// only; never enable in production.
export async function debugScreenshot(page, filePath) {
  if (process.env.DEBUG_SCREENSHOTS !== 'true') return;
  await page.screenshot({ path: filePath }).catch(() => {});
}

// Fail loudly and immediately if the resolved path doesn't exist, instead
// of letting Playwright's newContext() throw a generic ENOENT deep inside
// a join attempt with no indication of which path or env var is wrong.
export function assertAuthStateExists(platform = 'google') {
  const cfg = AUTH_STATE_CONFIG[platform];
  const authStatePath = resolveAuthStatePath(platform);
  if (!fs.existsSync(authStatePath)) {
    throw new Error(
      `${cfg.envVar} resolved to "${authStatePath}" but no file exists there. ` +
      `Set ${cfg.envVar} to the ${cfg.label} auth session file's actual location ` +
      `(e.g. a Render Secret File path), or generate it locally via ` +
      `${cfg.generatorScript} if running via docker-compose.`
    );
  }
  return authStatePath;
}

// Serializes concurrent writes to the same auth-state file. Google/Zoom
// auth.json is shared across every session of a platform (one bot
// identity), so with MAX_CONCURRENT_MEETINGS > 1, two sessions can finish
// their pre-flight page load and call context.storageState({ path }) at
// nearly the same moment. storageState() isn't documented as safe against
// a concurrent writer on the same path — interleaved writes could corrupt
// the file (breaking every future join, not just the racing ones), and
// even a "last write wins" outcome silently drops one session's refreshed
// cookies. Queuing per path fixes both: writes to the same file run one at
// a time, in call order; writes to different files (auth.json vs
// zoom-auth.json) aren't blocked by each other.
const storageStateWriteQueues = new Map(); // authStatePath -> tail of the write queue

export async function persistStorageState(context, path) {
  const previous = storageStateWriteQueues.get(path) || Promise.resolve();
  const next = previous
    .catch(() => {}) // a failed write shouldn't wedge the next session's write
    .then(() => context.storageState({ path }));
  storageStateWriteQueues.set(path, next);
  return next;
}

export class BrowserManager {
  // pulseSink: from AudioSink.provision() — when set (Linux only), Chrome's
  // own PulseAudio client reads PULSE_SINK and sends this process's audio
  // there instead of the container-wide default sink, so this session's
  // browser audio lands in the same isolated sink FFmpegManager reads from.
  static async launch(profileName = 'default', { pulseSink } = {}) {
    const platform = profileName === 'zoom' ? 'zoom' : 'google';
    const authStatePath = assertAuthStateExists(platform);

    // 1. Launch the base browser (no persistent profile — auth.json is now the source of truth for session state)
    const browser = await chromium.launch({
      headless: false,
      channel: 'chrome',
      env: pulseSink ? { ...process.env, PULSE_SINK: pulseSink } : undefined,
      args: [
        '--use-fake-ui-for-media-stream',
        '--use-fake-device-for-media-stream',
        '--disable-blink-features=AutomationControlled',
        // Docker/Linux specific flags below:
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
      ],
    });

    // 2. Create a new context and inject the saved login session from auth.json
    //
    // Anything that throws between here and the `return` below has to close
    // the browser itself. The Chrome process is already running at this
    // point, but the caller never receives the context, so its own cleanup
    // (bot.leave(), which is guarded by `if (this.context)`) can't see it and
    // no other reference survives the throw. Without this, a failure here -
    // a corrupted or truncated storageState file being the realistic one -
    // orphans a headed Chrome process on the shared Xvfb display, and a
    // repeating failure (one stale auth file fails every join the same way)
    // stacks them up until the container degrades.
    let context;
    try {
      context = await browser.newContext({
        permissions: ['camera', 'microphone'],
        storageState: authStatePath,
      });

      await context.addInitScript(() => {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      });
    } catch (err) {
      await browser.close().catch(() => {});
      throw err;
    }

    // 3. Ensure the underlying browser process dies when the context is closed
    const originalClose = context.close.bind(context);
    context.close = async () => {
      await originalClose();
      await browser.close();
    };

    return context;
  }
}