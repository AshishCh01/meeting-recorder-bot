// The join ladders below key off the word "Join", which also appears on
// controls that do something else entirely - "Join from Zoom Workplace app"
// launches the desktop client, "Join Audio" opens the audio picker. Without
// these negations .first() takes whichever sits earliest in the DOM and
// clicks the wrong one. Page context alone isn't enough to rely on here.
const NOT_JOIN_DECOYS = ':not(:has-text("Workplace")):not(:has-text("browser")):not(:has-text("Audio"))';

// Ordered fallback ladders, most-specific first. findFirstVisible() walks
// them in order and logs SELECTOR-HEAL whenever a non-primary tier matched,
// so a selector Zoom has quietly broken shows up in the logs while the later
// tiers are still carrying the join.
export const ZOOM_SELECTORS = {
  // Interstitial page. This gates the entire Zoom flow, so it gets its own
  // ladder. Historically a <button>, but Zoom has shipped it as an <a> in
  // some versions - hence tier 1.
  joinFromBrowserButton: [
    'button:has-text("Join from browser")',
    '[role="button"]:has-text("Join from browser"), a:has-text("Join from browser")',
    // Keys off "browser" alone, not "from browser" - Zoom wording like
    // "Join from your browser" breaks the contiguous phrase. Nothing else on
    // the interstitial ("Download Now", "Launch Meeting", "Join from Zoom
    // Workplace app") mentions a browser, so this stays unambiguous.
    'button:has-text("browser"), a:has-text("browser"), [role="button"]:has-text("browser")',
  ],

  // Safety net only. findFrameWithVisibleInput() is the primary strategy for
  // the name field and adapts to markup these selectors have never seen -
  // this ladder exists for when that scan comes up empty, not as the main path.
  nameInput: [
    'input[aria-label="Your Name"]',
    'input[aria-label*="name" i]',
    'input#inputname, input[name="inputname"]',
    'input[placeholder*="name" i]',
    'form input[type="text"]',
  ],

  // Join button on the PWA join page (app.zoom.us/wc/).
  joinButton: [
    `button:has-text("Join")${NOT_JOIN_DECOYS}, input[value="Join"]`,
    `[role="button"]:has-text("Join")${NOT_JOIN_DECOYS}, input[value*="Join" i]`,
    // Wording-independent last resort: the join form's own submit control,
    // which survives a rename or a locale we don't have text for.
    'button[type="submit"], input[type="submit"]',
  ],

  // Mic/video toggles, used by ensureToggledOff(). Zoom labels the control by
  // the action it performs: "Mute" means the mic is currently ON. In the call
  // the labels read "mute my microphone" / "stop my video"; the preview page
  // uses the shorter "Mute" / "Stop Video". Starts-with matching keeps
  // "mute" from also matching "unmute". The role= tier catches a control
  // that carries the wording as text instead of an aria-label.
  micToggle: {
    onState: [
      'button[aria-label^="mute my microphone" i]',
      'button[aria-label^="mute" i]:not([aria-label*="all" i])',
      'role=button[name=/^mute\\b(?!.*all)/i]',
    ],
    offState: [
      'button[aria-label^="unmute my microphone" i]',
      'button[aria-label^="unmute" i]:not([aria-label*="all" i])',
      'role=button[name=/^unmute\\b(?!.*all)/i]',
    ],
  },
  videoToggle: {
    onState: [
      'button[aria-label^="stop my video" i]',
      'button[aria-label^="stop video" i]',
      'role=button[name=/^stop (my )?video/i]',
    ],
    offState: [
      'button[aria-label^="start my video" i]',
      'button[aria-label^="start video" i]',
      'role=button[name=/^start (my )?video/i]',
    ],
  },

  inCallIndicators: [
    'button[aria-label*="leave" i]',
    'button[aria-label*="end" i]',
    'button[aria-label*="mute" i]',
    'button[aria-label*="unmute" i]',
    'button[aria-label*="audio" i]',
    'button[aria-label*="video" i]',
  ],

  meetingEndedText: /This meeting has been ended|meeting has ended|left the meeting|ended by host/i,

  botBlockedText: /Automated bots aren.t allowed to join this meeting/i,
};
