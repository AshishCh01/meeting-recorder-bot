import { MeetingBot } from '../../core/MeetingBot.js';
import { BrowserManager } from '../../core/BrowserManager.js';
import { GOOGLE_MEET_SELECTORS } from './selectors.js';
import { isAdmitted, hasMeetingEnded } from './detector.js';

export class GoogleMeetBot extends MeetingBot {
  async join() {
    this.context = await BrowserManager.launch('google-meet');
    this.page = await this.context.newPage();

    await this.page.goto(this.session.meetingUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await this.page.waitForTimeout(8000); 

    const nameInput = this.page.locator(GOOGLE_MEET_SELECTORS.nameInput);
    const nameFieldExists = await nameInput.isVisible().catch(() => false);
    if (nameFieldExists) {
      await nameInput.click();
      await nameInput.fill('Meeting Recorder Bot');
    }

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
    try {
      if (this.page.isClosed()) return false;

      // 1. Check if the red hangup button is still on screen
      const admitted = await isAdmitted(this.page);
      if (!admitted) {
        console.log('[GoogleMeetBot] Leave button disappeared. Meeting ended.');
        return false;
      }

      // 2. Check for "host ended" text
      const ended = await hasMeetingEnded(this.page);
      if (ended) {
        console.log('[GoogleMeetBot] End meeting text detected.');
        return false;
      }

      return true;
    } catch (error) {
      // If Playwright fails to check the page (e.g., page is rapidly redirecting),
      // safely catch the error and report the meeting as over so Chromium can close.
      console.log('[GoogleMeetBot] Page context lost, assuming meeting ended.');
      return false; 
    }
  }

  async leave() {
    console.log('[GoogleMeetBot] leave() triggered. Closing Chromium...');
    if (this.context) {
      await this.context.close().catch(() => {});
      console.log('[GoogleMeetBot] Chromium successfully closed.');
    }
    // The process.exit() command was removed from here. 
    // MeetingLifecycle.js will now handle shutting down the server.
  }
}