// NOTE: unlike Google Meet's selectors (verified against a live meeting),
// these are best-effort based on Zoom's typical web-client join flow and
// have not been tested end-to-end yet. Use PWDEBUG=1 against a real Zoom
// link to confirm/adjust before relying on this in production.
export const ZOOM_SELECTORS = {
  // Zoom often routes meeting links through an interstitial page first —
  // this clicks through to the actual web client if that page appears.
  joinFromBrowserLink: 'a:has-text("Join from your browser")',
  nameInput: 'input[type="text"][name="inputname"], input#input-for-name',
  joinButton: 'button:has-text("Join")',
  inCallIndicators: [
    'button[aria-label*="leave" i]',
    'button[aria-label*="mute" i]',
    'button[aria-label*="unmute" i]',
  ],
  meetingEndedText: /This meeting has been ended|meeting has ended|left the meeting/i,
};
