import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';
import { AudioRouter } from '../recording/AudioRouter.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export class BrowserManager {
  static async launch(profileName = 'default') {
    // Route audio to CABLE Input BEFORE Chrome launches.
    // Chrome reads the system default audio device on startup — setting it
    // here means we never need to touch Volume Mixer manually again.
    await AudioRouter.routeToCable();

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