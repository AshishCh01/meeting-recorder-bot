// meeting-bot/src/platforms/google-meet/GoogleMeetBot.js

import { MeetingBot } from '../../core/MeetingBot.js';
import { BrowserManager } from '../../core/BrowserManager.js';
import { GOOGLE_MEET_SELECTORS } from './selectors.js';
import {
  isAdmitted,
  hasMeetingEnded,
  getParticipantCount,
  isAloneByText,
  hasNavigatedAwayFromMeeting,
  isAuthExpired,
} from './detector.js';

const ALONE_GRACE_PERIOD_MS = 30000; // 30s — shorter now that detection is reliable

export class GoogleMeetBot extends MeetingBot {
  constructor(session) {
    super(session);
    this.aloneSince = null;
    this.navigatedAwayHits = 0; // debounce counter for false-positive navigation-away reads
  }

  async join() {
    // Defense-in-depth: don't trust that upstream (platform_detector.py)
    // already confirmed this is really a Google Meet link - re-check the
    // exact host right before the one line that actually navigates a
    // real, authenticated browser there.
    let hostname;
    try {
      hostname = new URL(this.session.meetingUrl).hostname.toLowerCase();
    } catch {
      throw new Error(`Refusing to navigate — not a valid URL: ${this.session.meetingUrl}`);
    }
    if (hostname !== 'meet.google.com') {
      throw new Error(`Refusing to navigate — expected meet.google.com, got: ${hostname}`);
    }

    this.context = await BrowserManager.launch('google-meet');
    this.page = await this.context.newPage();

    console.log(`[GoogleMeetBot] Navigating to ${this.session.meetingUrl}`);
    await this.page.goto(this.session.meetingUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await this.page.waitForTimeout(5000);

    // Pre-flight auth check — catch an expired/invalid auth.json session
    // immediately with a clear error, instead of failing later with a
    // vague "join button not found" timeout.
    if (await isAuthExpired(this.page)) {
      await this.page.screenshot({ path: 'google-meet-auth-expired.png' }).catch(() => {});
      throw new Error('AUTH_EXPIRED: Google session in auth.json is no longer valid — regenerate auth.json');
    }

    // Dismiss any "Got it" tooltips or popups if present
    const gotItBtn = this.page.locator('button:has-text("Got it"), span:has-text("Got it")').first();
    if (await gotItBtn.isVisible().catch(() => false)) {
      await gotItBtn.click().catch(() => {});
    }

    // Explicitly wait for the name input field
    try {
      const nameInput = this.page.locator(GOOGLE_MEET_SELECTORS.nameInput).first();
      await nameInput.waitFor({ state: 'visible', timeout: 10000 });
      console.log('[GoogleMeetBot] Name input located. Filling bot name...');
      await nameInput.click();
      await nameInput.fill('Meeting Recorder Bot');
      await this.page.waitForTimeout(1000);
    } catch {
      console.log('[GoogleMeetBot] No name input required or visible, proceeding to join...');
    }

    await this.page.screenshot({ path: 'google-meet-prejoin.png' }).catch(() => {});

    // Locate and click the join button
    console.log('[GoogleMeetBot] Clicking Join / Ask to join button...');
    const joinBtn = this.page.locator(GOOGLE_MEET_SELECTORS.joinButton).first();
    await joinBtn.waitFor({ state: 'visible', timeout: 15000 });
    await joinBtn.click({ force: true });

    await this.page.waitForTimeout(2000);
    await this.page.screenshot({ path: 'google-meet-after-join-click.png' }).catch(() => {});
  }

  async waitForAdmission(timeoutMs = 120000) {
    const start = Date.now();
    let consecutiveHits = 0;
    const REQUIRED_HITS = 2; // debounce: require the signal to hold across 2 polls (~4s) before trusting it

    while (Date.now() - start < timeoutMs) {
      if (await isAdmitted(this.page)) {
        consecutiveHits++;
        console.log(`[GoogleMeetBot] In-call signal detected (${consecutiveHits}/${REQUIRED_HITS})...`);
        if (consecutiveHits >= REQUIRED_HITS) {
          console.log('[GoogleMeetBot] Admission confirmed. Waiting for UI to settle...');
          await this.page.waitForTimeout(3000); // let Meet's post-admission transition/reload finish before polling begins
          return true;
        }
      } else {
        consecutiveHits = 0;
      }
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
        this.navigatedAwayHits++;
        console.log(`[GoogleMeetBot] Navigation-away signal (${this.navigatedAwayHits}/2)...`);
        if (this.navigatedAwayHits >= 2) {
          console.log('[GoogleMeetBot] Page navigated away from meeting URL. Meeting ended.');
          return false;
        }
        return true;
      } else {
        this.navigatedAwayHits = 0;
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