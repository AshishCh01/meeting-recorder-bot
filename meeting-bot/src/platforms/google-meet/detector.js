import { GOOGLE_MEET_SELECTORS } from './selectors.js';

export async function isAdmitted(page) {
  const selector = GOOGLE_MEET_SELECTORS.inCallIndicators.join(', ');
  return page.locator(selector).first().isVisible().catch(() => false);
}

export async function hasMeetingEnded(page) {
  return page
    .locator(`text=${GOOGLE_MEET_SELECTORS.meetingEndedText}`)
    .first()
    .isVisible()
    .catch(() => false);
}
