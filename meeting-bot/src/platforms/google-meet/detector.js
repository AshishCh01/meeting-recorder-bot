import { GOOGLE_MEET_SELECTORS } from './selectors.js';

export async function isAdmitted(page) {
  // Rely solely on genuine in-call-only controls. The old Signal 2
  // ([data-participant-id] count > 0) was dropped — it false-positived
  // on the bot's own self-preview tile in the waiting room, which
  // apparently carries the same attribute as real in-call participant
  // tiles, causing "admitted" to fire seconds after clicking "Ask to
  // join" with no host action at all.
  const selector = GOOGLE_MEET_SELECTORS.inCallIndicators.join(', ');
  const byToolbar = await page.locator(selector).first().isVisible().catch(() => false);
  return byToolbar;
}

export async function hasMeetingEnded(page) {
  try {
    const bodyText = await page.innerText('body', { timeout: 1000 });
    const endRegex = /You left the call|no one else in this meeting|removed from the meeting|Return to home screen|host ended|ended the meeting|everyone has left|can't join this video call|meeting has ended/i;
    return endRegex.test(bodyText);
  } catch (err) {
    return true;
  }
}

// NOTE: still uses [data-participant-id], which we now know can include
// the bot's own self-preview tile pre-admission. This only matters for
// alone-detection though, which runs AFTER isAdmitted() already
// confirmed real in-call state via the toolbar signal — so a stray
// self-tile can't cause a false "admitted," only a possible off-by-one
// in participant count. Worth revisiting if alone-detection ever
// misfires, but not urgent right now.
export async function getParticipantCount(page) {
  try {
    const count = await page.evaluate(() => {
      return document.querySelectorAll('[data-participant-id]').length;
    });
    return count;
  } catch {
    return null;
  }
}

export async function isAloneByText(page) {
  try {
    const bodyText = await page.innerText('body', { timeout: 1000 });
    const aloneRegex = /you're the only one here|you are the only one|waiting for others to join/i;
    return aloneRegex.test(bodyText);
  } catch (err) {
    return false;
  }
}

export function hasNavigatedAwayFromMeeting(page, originalMeetingUrl) {
  try {
    const meetingCode = originalMeetingUrl.trim().split('/').pop().split('?')[0].trim();
    const currentUrl = page.url();
    const navigatedAway = !currentUrl.includes(meetingCode);
    if (navigatedAway) {
      console.log(`[detector] Navigation check — current URL: ${currentUrl} | expected code: ${meetingCode}`);
    }
    return navigatedAway;
  } catch {
    return false;
  }
}


export async function isAuthExpired(page) {
  try {
    const bodyText = await page.innerText('body', { timeout: 1000 });
    // Covers both known failure screens we've seen:
    // 1. "Choose an account" picker showing "Signed out" next to the bot account
    // 2. Google's automation-block screen ("Couldn't sign you in")
    const authExpiredRegex = /choose an account|signed out|couldn't sign you in|this browser or app may not be secure/i;
    return authExpiredRegex.test(bodyText);
  } catch {
    return false;
  }
}