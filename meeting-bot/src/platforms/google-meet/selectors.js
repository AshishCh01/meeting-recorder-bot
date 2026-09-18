// The widest join tier below matches any "Join" text, so it has to actively
// rule out the other join-flavoured controls Meet puts on the same screen.
// Without these negations it picks whichever comes first in the DOM and
// clicks "Join and use a phone for audio", which opens a dial-in dialog and
// never joins the call.
const NOT_JOIN_DECOYS = ':not(:has-text("phone")):not(:has-text("cast")):not(:has-text("Other ways"))';

// Selectors that specifically identify a *name* field, as opposed to the
// catch-all tier appended below. Kept separate because isAnonymousSession()
// keys off these: a visible name field is proof Meet fell back to the guest
// flow, but the generic input[type="text"] tier would match any stray text
// box and abort a perfectly good authenticated join.
const NAME_FIELD_TIERS = [
  'input[aria-label="Your name"]',
  'input[aria-label*="name" i]',
  'input[placeholder*="name" i]',
];

// Ordered fallback ladders, most-specific first. findFirstVisible() walks
// these in order and logs a SELECTOR-HEAL warning whenever a non-primary
// tier is what matched, so a selector Google has quietly broken surfaces in
// the logs while the later tiers are still carrying the join.
export const GOOGLE_MEET_SELECTORS = {
  // Tier 3 is deliberately the loosest thing on the pre-join screen. Filling
  // the wrong field here is close to harmless - an authenticated session
  // joins fine without a name at all - whereas failing to find the field on
  // an anonymous join means the bot shows up unnamed.
  nameInput: [...NAME_FIELD_TIERS, 'input[type="text"]'],

  // Anonymous-session signal - see NAME_FIELD_TIERS above.
  anonymousNameField: NAME_FIELD_TIERS.join(', '),

  // "Ask to join" and "Join now" are two legitimate states of the same
  // button (waiting-room vs. direct entry), not fallbacks for each other -
  // both belong on every tier. Tier 1 covers Google swapping the <button>
  // for a <div role="button">, which they do routinely; tier 2 covers the
  // wording changing entirely.
  joinButton: [
    'button:has-text("Ask to join"), button:has-text("Join now")',
    '[role="button"]:has-text("Ask to join"), [role="button"]:has-text("Join now")',
    `button:has-text("Join")${NOT_JOIN_DECOYS}, [role="button"]:has-text("Join")${NOT_JOIN_DECOYS}`,
  ],

  // Mic/camera toggles, used by ensureToggledOff(). Meet labels the control
  // by the action it performs, so "Turn off ..." means the device is
  // currently ON. The same labels are used on the pre-join screen and in the
  // call. Tier 1 matches the accessible name if the label moves into text;
  // tier 2 keys off Meet's data-is-muted attribute and survives a rewording.
  micToggle: {
    onState: [
      '[aria-label^="Turn off microphone" i]',
      'role=button[name=/turn off microphone/i]',
      '[data-is-muted="false"][aria-label*="microphone" i]',
    ],
    offState: [
      '[aria-label^="Turn on microphone" i]',
      'role=button[name=/turn on microphone/i]',
      '[data-is-muted="true"][aria-label*="microphone" i]',
    ],
  },
  cameraToggle: {
    onState: [
      '[aria-label^="Turn off camera" i]',
      'role=button[name=/turn off camera/i]',
      '[data-is-muted="false"][aria-label*="camera" i]',
    ],
    offState: [
      '[aria-label^="Turn on camera" i]',
      'role=button[name=/turn on camera/i]',
      '[data-is-muted="true"][aria-label*="camera" i]',
    ],
  },

  // Deliberately narrower than the Zoom equivalent: leave/end-call controls
  // only, because those exist exclusively once actually in the call. Zoom's
  // list also keys off mic/camera buttons, but Meet renders those on the
  // pre-join screen too - matching them here would report "admitted" while
  // the bot is still sitting in the waiting room, the same class of false
  // positive that got the old [data-participant-id] signal dropped from
  // isAdmitted(). Anything added here must not exist before admission.
  inCallIndicators: [
    '[aria-label*="Leave call" i]',
    '[aria-label*="Leave meeting" i]',
    '[aria-label*="End call" i]',
    '[aria-label*="Hang up" i]',
    'button[aria-label*="leave" i]',
  ],
};
