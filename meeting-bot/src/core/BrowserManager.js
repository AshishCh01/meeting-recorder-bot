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

export class BrowserManager {
  static async launch(profileName = 'default') {
    const platform = profileName === 'zoom' ? 'zoom' : 'google';
    const authStatePath = assertAuthStateExists(platform);

    // 1. Launch the base browser (no persistent profile — auth.json is now the source of truth for session state)
    const browser = await chromium.launch({
      headless: false,
      channel: 'chrome',
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
    const context = await browser.newContext({
      permissions: ['camera', 'microphone'],
      storageState: authStatePath,
    });

    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    });

    // 3. Ensure the underlying browser process dies when the context is closed
    const originalClose = context.close.bind(context);
    context.close = async () => {
      await originalClose();
      await browser.close();
    };

    return context;
  }
}