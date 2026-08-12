import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export class BrowserManager {
  static async launch(profileName = 'default') {
    // Persistent profile so a signed-in session (if you log in manually once)
    // is reused across runs — this is what got past Google's guest-join
    // blocking during development. Zoom/Teams may not need this, but sharing
    // the same launch path keeps all three platforms consistent.
    const context = await chromium.launchPersistentContext(
      path.join(__dirname, '..', '..', 'browser-profiles', profileName),
      {
        headless: false, // run under Xvfb in production (see README)
        channel: 'chrome',
        permissions: ['camera', 'microphone'],
        args: [
          '--use-fake-ui-for-media-stream',
          '--disable-blink-features=AutomationControlled',
        ],
      }
    );

    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    });

    return context;
  }
}
