import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

export class BrowserManager {
  static async launch(profileName = 'default') {

    const context = await chromium.launchPersistentContext(
      path.join(__dirname, '..', '..', 'browser-profiles', profileName),
      {
        headless: false,
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