import { MeetingBot } from '../../core/MeetingBot.js';
import { BrowserManager } from '../../core/BrowserManager.js';
import { TEAMS_SELECTORS } from './selectors.js';
import { isAdmitted, hasMeetingEnded } from './detector.js';

export class TeamsBot extends MeetingBot {
  async join() {
    this.context = await BrowserManager.launch('teams');
    this.page = await this.context.newPage();

    await this.page.goto(this.session.meetingUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await this.page.waitForTimeout(5000);

    const browserLink = this.page.locator(TEAMS_SELECTORS.continueOnBrowserLink);
    if (await browserLink.isVisible().catch(() => false)) {
      await browserLink.click();
      await this.page.waitForTimeout(5000);
    }

    const nameInput = this.page.locator(TEAMS_SELECTORS.nameInput);
    if (await nameInput.isVisible().catch(() => false)) {
      await nameInput.fill('Meeting Recorder Bot');
    }

    await this.page.click(TEAMS_SELECTORS.joinButton, { timeout: 30000 });
  }

  async waitForAdmission(timeoutMs = 120000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (await isAdmitted(this.page)) return true;
      await this.page.waitForTimeout(2000);
    }
    throw new Error('Not admitted to the Teams meeting within the timeout window');
  }

  async isStillInMeeting() {
    if (await hasMeetingEnded(this.page)) return false;
    return isAdmitted(this.page);
  }

  async leave() {
    await this.context?.close();
  }
}
