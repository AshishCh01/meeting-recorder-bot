import path from 'path';
import fs from 'fs';

const RECORDINGS_DIR = path.resolve('recordings');

export class RecordingFile {
  static pathFor(meetingId) {
    if (!fs.existsSync(RECORDINGS_DIR)) {
      fs.mkdirSync(RECORDINGS_DIR, { recursive: true });
    }
    return path.join(RECORDINGS_DIR, `${meetingId}.mp4`);
  }

  static storageKeyFor(meetingId) {
    // Object path/key used inside the Supabase Storage bucket
    return `${meetingId}/recording.mp4`;
  }
}
