import { ZOOM_SELECTORS } from './selectors.js';

export async function isAdmitted(page) {
  const selector = ZOOM_SELECTORS.inCallIndicators.join(', ');
  return page.locator(selector).first().isVisible().catch(() => false);
}

export async function hasMeetingEnded(page) {
  return page
    .locator(`text=${ZOOM_SELECTORS.meetingEndedText}`)
    .first()
    .isVisible()
    .catch(() => false);
}

// Zoom's anti-automation fingerprinting can reject an anonymous guest join
// outright, but the rejection screen still renders generic Mute/Stop Video
// buttons - which also match inCallIndicators above. Without this check,
// isAdmitted() reads those leftover buttons as a real "in the meeting"
// signal and the bot proceeds to "record" a meeting it was never let into.
export async function isBotBlocked(page) {
  return page
    .locator(`text=${ZOOM_SELECTORS.botBlockedText}`)
    .first()
    .isVisible()
    .catch(() => false);
}
