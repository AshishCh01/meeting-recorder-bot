import { MeetingBot } from '../../core/MeetingBot.js';
import { BrowserManager } from '../../core/BrowserManager.js';
import { ZOOM_SELECTORS } from './selectors.js';
import { isAdmitted, hasMeetingEnded } from './detector.js';

export class ZoomBot extends MeetingBot {
  async join() {
    this.context = await BrowserManager.launch('zoom');
    this.page = await this.context.newPage();

    await this.page.goto(this.session.meetingUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await this.page.waitForTimeout(5000);

    // Zoom frequently shows an interstitial page prompting to open the
    // desktop app; click through to the browser client instead.
    const browserLink = this.page.locator(ZOOM_SELECTORS.joinFromBrowserLink);
    if (await browserLink.isVisible().catch(() => false)) {
      await browserLink.click();
      await this.page.waitForTimeout(5000);
    }

    const nameInput = this.page.locator(ZOOM_SELECTORS.nameInput);
    if (await nameInput.isVisible().catch(() => false)) {
      await nameInput.fill('Meeting Recorder Bot');
    }

    await this.page.click(ZOOM_SELECTORS.joinButton, { timeout: 30000 });
  }

  async waitForAdmission(timeoutMs = 120000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (await isAdmitted(this.page)) return true;
      await this.page.waitForTimeout(2000);
    }
    throw new Error('Not admitted to the Zoom meeting within the timeout window');
  }

  async isStillInMeeting() {
    if (await hasMeetingEnded(this.page)) return false;
    return isAdmitted(this.page);
  }

  async leave() {
    await this.context?.close();
  }
}
