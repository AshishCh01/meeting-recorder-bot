export const GOOGLE_MEET_SELECTORS = {
  nameInput: 'input[aria-label="Your name"]',
  joinButton: 'button:has-text("Ask to join"), button:has-text("Join now")',
  inCallIndicators: [
    '[aria-label="Leave call"]',
    '[aria-label="Leave meeting"]',
    '[aria-label="End call"]',
    '[aria-label*="microphone" i]',
    '[aria-label*="camera" i]',
  ],
  meetingEndedText: /You left the call|removed from the meeting|Return to home screen/i,
};
