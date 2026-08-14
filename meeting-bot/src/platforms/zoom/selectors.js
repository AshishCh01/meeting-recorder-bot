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


// export const ZOOM_SELECTORS = {
//   // The interstitial page has a button (not a link) saying exactly "Join from browser"
//   joinFromBrowserButton: 'button:has-text("Join from browser")',

//   // The popup tooltip that appears — close it first so it doesn't block clicks
//   appPopupCloseButton: 'button[aria-label="Close"], .ReactModal__Content button, button:has-text("×")',

//   // After clicking "Join from browser", Zoom shows a second page with a name
//   // input and a separate join button — these target that second screen only
//   nameInput: 'input#inputname, input[placeholder="Your Name"], input[type="text"][name="inputname"]',
//   joinButton: 'button.preview-join-button, button:has-text("Join"), input[value="Join"]',

//   inCallIndicators: [
//     'button[aria-label*="leave" i]',
//     'button[aria-label*="end" i]',
//     'button[aria-label*="mute" i]',
//     'button[aria-label*="unmute" i]',
//     'button[aria-label*="audio" i]',
//   ],
//   meetingEndedText: /This meeting has been ended|meeting has ended|left the meeting|ended by host/i,
// };