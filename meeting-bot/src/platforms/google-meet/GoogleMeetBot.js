import { MeetingBot } from '../../core/MeetingBot.js';
import { BrowserManager } from '../../core/BrowserManager.js';
import { GOOGLE_MEET_SELECTORS } from './selectors.js';
import { isAdmitted, hasMeetingEnded } from './detector.js';

export class GoogleMeetBot extends MeetingBot {
  async join() {
    this.context = await BrowserManager.launch('google-meet');
    this.page = await this.context.newPage();

    await this.page.goto(this.session.meetingUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await this.page.waitForTimeout(8000); // let Meet's page finish its own re-render

    const nameInput = this.page.locator(GOOGLE_MEET_SELECTORS.nameInput);
    const nameFieldExists = await nameInput.isVisible().catch(() => false);
    if (nameFieldExists) {
      await nameInput.click();
      await nameInput.fill('Meeting Recorder Bot');
    }
    // If no name field, we're likely already signed in via the persistent
    // profile and Meet skips straight to the join button.

    await this.page.click(GOOGLE_MEET_SELECTORS.joinButton, { timeout: 30000 });
  }

  async waitForAdmission(timeoutMs = 120000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (await isAdmitted(this.page)) return true;
      await this.page.waitForTimeout(2000);
    }
    throw new Error('Not admitted to the meeting within the timeout window');
  }

  async isStillInMeeting() {
    if (await hasMeetingEnded(this.page)) return false;
    return isAdmitted(this.page);
  }

  async leave() {
    await this.context?.close();
  }
}
