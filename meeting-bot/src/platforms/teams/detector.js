import { TEAMS_SELECTORS } from './selectors.js';

export async function isAdmitted(page) {
  const selector = TEAMS_SELECTORS.inCallIndicators.join(', ');
  return page.locator(selector).first().isVisible().catch(() => false);
}

export async function hasMeetingEnded(page) {
  return page
    .locator(`text=${TEAMS_SELECTORS.meetingEndedText}`)
    .first()
    .isVisible()
    .catch(() => false);
}
