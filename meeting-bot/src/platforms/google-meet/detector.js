import { GOOGLE_MEET_SELECTORS } from './selectors.js';

export async function isAdmitted(page) {
  const selector = GOOGLE_MEET_SELECTORS.inCallIndicators.join(', ');
  return page.locator(selector).first().isVisible().catch(() => false);
}

export async function hasMeetingEnded(page) {
  try {
    // Grab the raw text of the entire body to bypass DOM nesting issues
    const bodyText = await page.innerText('body', { timeout: 1000 });
    
    // Expanded regex includes variations for when the host ends it for everyone
    const endRegex = /You left the call|no one else in this meeting|removed from the meeting|Return to home screen|host ended|ended the meeting|everyone has left/i;
    
    return endRegex.test(bodyText);
  } catch (err) {
    // If we can't read the body text, the page likely closed or crashed,
    // so we assume the meeting is over to gracefully stop the recording.
    return true; 
  }
}