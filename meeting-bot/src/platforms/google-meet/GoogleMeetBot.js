import { MeetingBot } from '../../core/MeetingBot.js';
import { BrowserManager } from '../../core/BrowserManager.js';
import { GOOGLE_MEET_SELECTORS } from './selectors.js';
import {
  isAdmitted,
  hasMeetingEnded,
  getParticipantCount,
  isAloneByText,
  hasNavigatedAwayFromMeeting,
} from './detector.js';

const ALONE_GRACE_PERIOD_MS = 30000; // 30s — shorter now that detection is reliable

export class GoogleMeetBot extends MeetingBot {
  constructor(session) {
    super(session);
    this.aloneSince = null;
  }

  async join() {
    this.context = await BrowserManager.launch('google-meet');
    this.page = await this.context.newPage();

    await this.page.goto(this.session.meetingUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await this.page.waitForTimeout(8000);
    await this.page.screenshot({ path: 'google-meet-prejoin.png' }).catch(() => {});

    const nameInput = this.page.locator(GOOGLE_MEET_SELECTORS.nameInput);
    const nameFieldExists = await nameInput.isVisible().catch(() => false);
    if (nameFieldExists) {
      await nameInput.click();
      await nameInput.fill('Meeting Recorder Bot');
    }

    await this.page.click(GOOGLE_MEET_SELECTORS.joinButton, { timeout: 30000 });
    await this.page.waitForTimeout(2000);
    await this.page.screenshot({ path: 'google-meet-after-join-click.png' }).catch(() => {});
  }

  async waitForAdmission(timeoutMs = 120000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (await isAdmitted(this.page)) return true;
      await this.page.waitForTimeout(2000);
    }
    await this.page.screenshot({ path: 'google-meet-admission-timeout.png' }).catch(() => {});
    throw new Error('Not admitted to the meeting within the timeout window');
  }

  async isStillInMeeting() {
    try {
      if (this.page.isClosed()) return false;

      const admitted = await isAdmitted(this.page);
      if (!admitted) {
        console.log('[GoogleMeetBot] Leave button disappeared. Meeting ended.');
        return false;
      }

      const ended = await hasMeetingEnded(this.page);
      if (ended) {
        console.log('[GoogleMeetBot] End-of-call text detected. Meeting ended.');
        return false;
      }

      if (hasNavigatedAwayFromMeeting(this.page, this.session.meetingUrl)) {
        console.log('[GoogleMeetBot] Page navigated away from meeting URL. Meeting ended.');
        return false;
      }

      // Primary alone-detection: actual DOM participant count
      const participantCount = await getParticipantCount(this.page);
      const aloneByCount = participantCount !== null && participantCount <= 1;

      // Secondary fallback: banner text, in case the DOM query above
      // ever returns null on a future Meet UI change
      const aloneByText = participantCount === null ? await isAloneByText(this.page) : false;

      const alone = aloneByCount || aloneByText;

      console.log(`[GoogleMeetBot] Participant count: ${participantCount}, alone: ${alone}`);

      if (alone) {
        if (!this.aloneSince) {
          this.aloneSince = Date.now();
          console.log('[GoogleMeetBot] Bot is alone in the call. Starting 30s grace timer...');
        } else {
          const elapsed = Date.now() - this.aloneSince;
          console.log(`[GoogleMeetBot] Still alone — ${Math.round(elapsed / 1000)}s elapsed of ${ALONE_GRACE_PERIOD_MS / 1000}s grace period`);
          if (elapsed > ALONE_GRACE_PERIOD_MS) {
            console.log('[GoogleMeetBot] Alone for over 30s. Leaving the call.');
            return false;
          }
        }
      } else {
        if (this.aloneSince) {
          console.log('[GoogleMeetBot] Participant rejoined. Grace timer reset.');
        }
        this.aloneSince = null;
      }

      return true;
    } catch (error) {
      console.log('[GoogleMeetBot] Page context lost, assuming meeting ended:', error.message);
      return false;
    }
  }

  async leave() {
    console.log('[GoogleMeetBot] leave() triggered. Closing Chromium...');
    if (this.context) {
      await this.context.close().catch(() => {});
      console.log('[GoogleMeetBot] Chromium successfully closed.');
    }
  }
}