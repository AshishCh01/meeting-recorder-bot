import path from 'path';
import fs from 'fs';

const RECORDINGS_DIR = path.resolve('recordings');

export class RecordingFile {
  static pathFor(meetingId) {
    if (!fs.existsSync(RECORDINGS_DIR)) {
      fs.mkdirSync(RECORDINGS_DIR, { recursive: true });
    }
    return path.join(RECORDINGS_DIR, `${meetingId}.m4a`);
  }

  static storageKeyFor(userId, meetingId) {
    // Object path/key used inside the Supabase Storage bucket
    return `${userId}/${meetingId}/recording.m4a`;
  }
}