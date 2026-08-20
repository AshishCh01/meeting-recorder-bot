import { GOOGLE_MEET_SELECTORS } from './selectors.js';

export async function isAdmitted(page) {
  // Signal 1: known in-call-only controls (aria-label based). This can
  // go stale if Google renames a button's aria-label, which is exactly
  // the risk Signal 2 below hedges against.
  const selector = GOOGLE_MEET_SELECTORS.inCallIndicators.join(', ');
  const byToolbar = await page.locator(selector).first().isVisible().catch(() => false);
  if (byToolbar) return true;

  // Signal 2: structural DOM check. Participant tiles only ever render
  // once you're actually inside the live call — never on the "asking
  // to join" waiting-room screen. Same [data-participant-id] attribute
  // getParticipantCount() below already relies on for alone-detection.
  try {
    const count = await page.evaluate(() =>
      document.querySelectorAll('[data-participant-id]').length
    );
    return count > 0;
  } catch {
    return false;
  }
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

// Counts actual participant tiles in the DOM rather than matching banner
// text — this is far more reliable since Google changes the exact wording
// of the "you're alone" banner across UI versions, but every real
// participant always gets a [data-participant-id] element.
export async function getParticipantCount(page) {
  try {
    const count = await page.evaluate(() => {
      return document.querySelectorAll('[data-participant-id]').length;
    });
    return count;
  } catch {
    return null; // couldn't determine — treat as unknown, not alone
  }
}

// Kept as a secondary signal in case the participant-count DOM query
// ever fails on a future Meet UI version.
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
    const meetingCode = originalMeetingUrl.split('/').pop().split('?')[0];
    return !page.url().includes(meetingCode);
  } catch {
    return false;
  }
}