// NOTE: best-effort selectors, not yet verified against a live Teams
// meeting — confirm with PWDEBUG=1 before relying on this. Also, Teams
// meetings in containerized Chromium have known video-rendering issues
// (see project README) — test outside Docker first if video capture
// matters for Teams specifically; audio-only tends to be more reliable.
export const TEAMS_SELECTORS = {
  continueOnBrowserLink: 'a:has-text("Continue on this browser"), button:has-text("Continue on this browser")',
  nameInput: 'input[data-tid="prejoin-display-name-input"], input[placeholder="Type your name"]',
  joinButton: 'button:has-text("Join now")',
  inCallIndicators: [
    'button[aria-label*="Leave" i]',
    'button[aria-label*="mic" i]',
  ],
  meetingEndedText: /You left the meeting|call has ended|left the meeting/i,
};
