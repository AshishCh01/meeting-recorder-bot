export const GOOGLE_MEET_SELECTORS = {
  nameInput: 'input[aria-label="Your name"]',
  joinButton: 'button:has-text("Ask to join"), button:has-text("Join now")',
  inCallIndicators: [
    '[aria-label="Leave call"]',
    '[aria-label="Leave meeting"]',
    '[aria-label="End call"]'
  ]
};