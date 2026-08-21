import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// __dirname is meeting-bot/src/core, so '../..' goes up to the meeting-bot
// root - that's the docker-compose bind-mount target and the local dev
// default. On Render (or any host without that bind mount) auth.json is
// instead delivered as a Secret File at a path Render controls, so it must
// be overridable via env rather than hardcoded.
const DEFAULT_AUTH_STATE_PATH = path.join(__dirname, '..', '..', 'auth.json');

export function resolveAuthStatePath() {
  return process.env.AUTH_STATE_PATH || DEFAULT_AUTH_STATE_PATH;
}

// Fail loudly and immediately if the resolved path doesn't exist, instead
// of letting Playwright's newContext() throw a generic ENOENT deep inside
// a join attempt with no indication of which path or env var is wrong.
export function assertAuthStateExists() {
  const authStatePath = resolveAuthStatePath();
  if (!fs.existsSync(authStatePath)) {
    throw new Error(
      `AUTH_STATE_PATH resolved to "${authStatePath}" but no file exists there. ` +
      `Set AUTH_STATE_PATH to the Google auth session file's actual location ` +
      `(e.g. a Render Secret File path), or generate auth.json locally via ` +
      `generate-auth.cjs if running via docker-compose.`
    );
  }
  return authStatePath;
}

export class BrowserManager {
  static async launch(profileName = 'default') {
    const authStatePath = assertAuthStateExists();

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