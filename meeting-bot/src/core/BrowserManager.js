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
          '--use-fake-device-for-media-stream', 
          '--disable-blink-features=AutomationControlled',
          // Docker/Linux specific flags below:
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-gpu',
        ],
      }
    );

    await context.addInitScript(() => {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    });

    return context;
  }
}