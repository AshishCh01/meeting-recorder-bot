const DEFAULT_BOT_NAME = 'MeetIQ Notetaker';

export class MeetingSession {
  constructor({ meetingId, meetingUrl, platform, userId, botDisplayName, maxDurationMinutes }) {
    this.meetingId = meetingId;
    this.meetingUrl = meetingUrl;
    this.platform = platform;
    this.userId = userId;
    this.botName = botDisplayName || DEFAULT_BOT_NAME;
    this.status = 'joining'; // joining | waiting_for_admission | recording | uploading | completed | failed
    this.startedAt = null;
    this.endedAt = null;
    this.errorMessage = null;
    this.recordingFilePath = null; // local temp path, set by RecordingFile
    this.cancelRequested = false;
    this.audioSinkName = null; // per-session PulseAudio sink, set by MeetingLifecycle via AudioSink.provision()
    // The caller's billing plan cap, in minutes (backend billing Phase 2).
    // null when the caller sent none, which is the pre-billing behaviour:
    // MeetingLifecycle then falls back to MAX_RECORDING_DURATION_MINUTES.
    this.maxDurationMinutes = maxDurationMinutes ?? null;
  }

  // Signals the lifecycle's admission-wait and in-meeting polling loops to
  // bail out at their next check, so a stuck/slow join can be aborted from
  // outside instead of running until it times out on its own.
  requestCancel() {
    this.cancelRequested = true;
  }

  markWaitingForAdmission() {
    this.status = 'waiting_for_admission';
  }

  markRecording() {
    this.status = 'recording';
    this.startedAt = new Date();
  }

  markUploading() {
    this.status = 'uploading';
    this.endedAt = new Date();
  }

  markCompleted() {
    this.status = 'completed';
  }

  markFailed(errorMessage) {
    this.status = 'failed';
    this.errorMessage = errorMessage;
  }

  durationSeconds() {
    if (!this.startedAt || !this.endedAt) return null;
    return Math.round((this.endedAt - this.startedAt) / 1000);
  }
}
