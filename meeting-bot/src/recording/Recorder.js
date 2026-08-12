import { FFmpegManager } from './FFmpegManager.js';
import { RecordingFile } from './RecordingFile.js';

export class Recorder {
  constructor(meetingId) {
    this.outputPath = RecordingFile.pathFor(meetingId);
    this.ffmpeg = new FFmpegManager(this.outputPath);
  }

  start() {
    this.ffmpeg.start();
  }

  async stop() {
    await this.ffmpeg.stop();
    return this.outputPath;
  }
}
