// meeting-bot/src/platforms/google-meet/GoogleMeetBot.js

import { MeetingBot } from '../../core/MeetingBot.js';
import { BrowserManager, resolveAuthStatePath, debugScreenshot, persistStorageState } from '../../core/BrowserManager.js';
import { findFirstVisible, ensureToggledOff } from '../../core/resilientLocator.js';
import { GOOGLE_MEET_SELECTORS } from './selectors.js';
import {
  isAdmitted,
  hasMeetingEnded,
  getParticipantCount,
  isAloneByText,
  hasNavigatedAwayFromMeeting,
  isAuthExpired,
  isAnonymousSession,
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

    this.context = await BrowserManager.launch('google-meet', { pulseSink: this.session.audioSinkName });
    this.page = await this.context.newPage();

    console.log(`[GoogleMeetBot] Navigating to ${this.session.meetingUrl}`);
    await this.page.goto(this.session.meetingUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await this.page.waitForTimeout(5000);

    // Pre-flight auth check — catch an expired/invalid auth.json session
    // immediately with a clear error, instead of failing later with a
    // vague "join button not found" timeout.
    if (await isAuthExpired(this.page)) {
      await debugScreenshot(this.page, 'google-meet-auth-expired.png');
      throw new Error('AUTH_EXPIRED: Google session in auth.json is no longer valid — regenerate auth.json');
    }

    // Catches the case isAuthExpired() can't: Google silently falling
    // back to the anonymous "Ask to join" flow with no explicit error
    // text, instead of authenticating as the auth.json account. Failing
    // here takes ~2s instead of burning the full waitForAdmission()
    // timeout waiting for an admission that was never coming, because
    // the anonymous join request gets flatly denied within seconds.
    if (await isAnonymousSession(this.page)) {
      await debugScreenshot(this.page, 'google-meet-anonymous-session.png');
      throw new Error(
        'AUTH_EXPIRED: auth.json did not authenticate — Meet is showing the ' +
        'anonymous "Ask to join" flow with a Sign in prompt. This can happen ' +
        'even when the main session cookie looks valid for a long time, ' +
        'because Google\'s short-lived session-rotation cookies expire ' +
        'within roughly 10-60 minutes of generation and nothing refreshes ' +
        'them between runs. Regenerate auth.json.'
      );
    }

    // The session just proved itself live (no error, not anonymous) -
    // persist whatever fresh rotation cookies Google issued during this
    // page load back to auth.json, extending how long the session stays
    // usable before it goes stale again.
    await persistStorageState(this.context, resolveAuthStatePath()).catch((err) => {
      console.log('[GoogleMeetBot] Warning: could not refresh auth.json:', err.message);
    });

    // Dismiss any "Got it" tooltips or popups if present
    const gotItBtn = this.page.locator('button:has-text("Got it"), span:has-text("Got it")').first();
    if (await gotItBtn.isVisible().catch(() => false)) {
      await gotItBtn.click().catch(() => {});
    }

    // Explicitly wait for the name input field. Absent is a normal outcome,
    // not an error - an authenticated session joins under the account name
    // and Meet never renders this field.
    const nameInput = await findFirstVisible(this.page, GOOGLE_MEET_SELECTORS.nameInput, {
      timeout: 10000,
      label: 'nameInput',
    });
    if (nameInput) {
      console.log('[GoogleMeetBot] Name input located. Filling bot name...');
      try {
        await nameInput.click();
        await nameInput.fill(this.session.botName);
        await this.page.waitForTimeout(1000);
      } catch (err) {
        console.log('[GoogleMeetBot] Name input found but could not be filled:', err.message);
      }
    } else {
      console.log('[GoogleMeetBot] No name input required or visible, proceeding to join...');
    }

    await this.turnOffMicAndCamera('pre-join');

    await debugScreenshot(this.page, 'google-meet-prejoin.png');

    // Locate and click the join button
    console.log('[GoogleMeetBot] Clicking Join / Ask to join button...');
    const joinBtn = await findFirstVisible(this.page, GOOGLE_MEET_SELECTORS.joinButton, {
      timeout: 15000,
      label: 'joinButton',
    });
    if (!joinBtn) {
      await debugScreenshot(this.page, 'google-meet-join-button-missing.png');
      throw new Error(
        'Could not find the Join / Ask to join button - none of the known selectors ' +
        'matched. Google has likely changed the pre-join UI; check ' +
        'google-meet-join-button-missing.png and update GOOGLE_MEET_SELECTORS.joinButton.'
      );
    }
    await joinBtn.click({ force: true });

    await this.page.waitForTimeout(2000);
    await debugScreenshot(this.page, 'google-meet-after-join-click.png');
  }

  // Chrome's fake devices are live, so without this the call hears/sees
  // them until the host mutes the bot. Runs on the pre-join screen, and
  // again once admitted in case the pre-join click missed or Meet reset the
  // state on entry - ensureToggledOff() never clicks a device that is
  // already off, so the second pass is a no-op when the first one worked.
  async turnOffMicAndCamera(stage, timeout = 5000) {
    // Sequential on purpose: Playwright clicks share one mouse, and two
    // concurrent clicks interleave their moves so one of them is lost.
    const mic = await ensureToggledOff(this.page, GOOGLE_MEET_SELECTORS.micToggle, { timeout, label: 'microphone' });
    const camera = await ensureToggledOff(this.page, GOOGLE_MEET_SELECTORS.cameraToggle, { timeout, label: 'camera' });
    console.log(`[GoogleMeetBot] ${stage}: microphone ${mic}, camera ${camera}`);
  }

  async waitForAdmission(timeoutMs = 300000) {
    const start = Date.now();
    let consecutiveHits = 0;
    const REQUIRED_HITS = 2; // debounce: require the signal to hold across 2 polls (~4s) before trusting it

    while (Date.now() - start < timeoutMs) {
      if (this.session.cancelRequested) {
        throw new Error('Cancelled by user while waiting for admission');
      }
      if (await isAdmitted(this.page)) {
        consecutiveHits++;
        console.log(`[GoogleMeetBot] In-call signal detected (${consecutiveHits}/${REQUIRED_HITS})...`);
        if (consecutiveHits >= REQUIRED_HITS) {
          console.log('[GoogleMeetBot] Admission confirmed. Waiting for UI to settle...');
          await this.page.waitForTimeout(3000); // let Meet's post-admission transition/reload finish before polling begins
          await this.turnOffMicAndCamera('in-call', 3000);
          return true;
        }
      } else {
        consecutiveHits = 0;
      }
      await this.page.waitForTimeout(2000);
    }
    await debugScreenshot(this.page, 'google-meet-admission-timeout.png');
    throw new Error(
      'Nobody admitted the bot to the meeting within 5 minutes. Ask the host ' +
      'to let it in from the participant/waiting-room list next time.'
    );
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