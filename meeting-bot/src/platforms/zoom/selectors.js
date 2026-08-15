export const ZOOM_SELECTORS = {
  // Interstitial page — a <button> (not <a> link) with exact text "Join from browser"
  joinFromBrowserButton: 'button:has-text("Join from browser")',

  // PWA join page (app.zoom.us/wc/) — name input field.
  // Zoom's web client renders these with various attributes depending on version.
  nameInput: [
    'input[aria-label="Your Name"]',
    'input[aria-label*="name" i]',
    'input#inputname',
    'input[name="inputname"]',
    'input[placeholder*="name" i]',
    'input[placeholder*="Name" i]',
    'form input[type="text"]',
  ].join(', '),

  // Join button on the PWA join page — safe to use after navigating away
  // from the interstitial, so "Join" won't match "Join from Zoom Workplace app"
  joinButton: 'button:has-text("Join"), input[value="Join"]',

  inCallIndicators: [
    'button[aria-label*="leave" i]',
    'button[aria-label*="end" i]',
    'button[aria-label*="mute" i]',
    'button[aria-label*="unmute" i]',
    'button[aria-label*="audio" i]',
    'button[aria-label*="video" i]',
  ],

  meetingEndedText: /This meeting has been ended|meeting has ended|left the meeting|ended by host/i,
};
