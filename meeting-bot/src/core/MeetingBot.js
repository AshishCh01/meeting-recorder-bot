// Base class every platform-specific bot (GoogleMeetBot, ZoomBot, TeamsBot)
// implements. Keeping this interface identical across platforms is what lets
// MeetingLifecycle.js and api/server.js stay platform-agnostic.
export class MeetingBot {
  constructor(session) {
    if (new.target === MeetingBot) {
      throw new Error('MeetingBot is abstract — use a platform subclass');
    }
    this.session = session; // MeetingSession instance
  }

  // Opens the meeting URL and fills in whatever's needed to request entry.
  // Should not resolve until the "asking to join" state is reached.
  async join() {
    throw new Error('join() not implemented');
  }

  // Resolves once the bot has actually been let into the call.
  // Should reject/throw if not admitted within a reasonable time.
  async waitForAdmission() {
    throw new Error('waitForAdmission() not implemented');
  }

  // Returns true while still in the call, false once it's ended
  // (used by MeetingLifecycle to know when to stop recording).
  async isStillInMeeting() {
    throw new Error('isStillInMeeting() not implemented');
  }

  // Leaves the call and closes the browser/context cleanly.
  async leave() {
    throw new Error('leave() not implemented');
  }
}
