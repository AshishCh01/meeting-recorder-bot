export class MeetingSession {
  constructor({ meetingId, meetingUrl, platform }) {
    this.meetingId = meetingId;
    this.meetingUrl = meetingUrl;
    this.platform = platform;
    this.status = 'joining'; // joining | recording | uploading | completed | failed
    this.startedAt = null;
    this.endedAt = null;
    this.errorMessage = null;
    this.recordingFilePath = null; // local temp path, set by RecordingFile
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
