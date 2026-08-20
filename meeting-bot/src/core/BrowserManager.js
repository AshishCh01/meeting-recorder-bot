import { chromium } from 'playwright';
import path from 'path';
import { fileURLToPath } from 'url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

export class BrowserManager {
  static async launch(profileName = 'default') {

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
    // __dirname is meeting-bot/src/core, so '../..' goes up to the meeting-bot root
    const context = await browser.newContext({
      permissions: ['camera', 'microphone'],
      storageState: path.join(__dirname, '..', '..', 'auth.json'),
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