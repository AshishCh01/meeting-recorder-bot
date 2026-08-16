import { GOOGLE_MEET_SELECTORS } from './selectors.js';

export async function isAdmitted(page) {
  const selector = GOOGLE_MEET_SELECTORS.inCallIndicators.join(', ');
  return page.locator(selector).first().isVisible().catch(() => false);
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

