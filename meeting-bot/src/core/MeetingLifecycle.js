import axios from 'axios';
import { GoogleMeetBot } from '../platforms/google-meet/GoogleMeetBot.js';
import { ZoomBot } from '../platforms/zoom/ZoomBot.js';
import { TeamsBot } from '../platforms/teams/TeamsBot.js';
import { Recorder } from '../recording/Recorder.js';
import { RecordingFile } from '../recording/RecordingFile.js';
import { SupabaseUploader } from '../storage/SupabaseUploader.js';
import fs from 'fs';

const BOT_CLASSES = {
  google: GoogleMeetBot,
  zoom: ZoomBot,
  teams: TeamsBot,
};

export async function runMeetingLifecycle(session) {
  const BotClass = BOT_CLASSES[session.platform];
  if (!BotClass) throw new Error(`Unsupported platform: ${session.platform}`);

  const bot = new BotClass(session);
  const recorder = new Recorder(session.meetingId);

  try {
    await bot.join();
    await bot.waitForAdmission();

    session.markRecording();
    recorder.start();

    // Poll every 10s until the meeting ends (or someone removes the bot)
    while (await bot.isStillInMeeting()) {
      await new Promise((r) => setTimeout(r, 10000));
    }

    session.markUploading();
    const localPath = await recorder.stop();
    await bot.leave();

    const storageKey = RecordingFile.storageKeyFor(session.meetingId);
    await SupabaseUploader.upload(localPath, storageKey);
    fs.unlinkSync(localPath); // clean up local temp file after upload

    session.markCompleted();
    await notifyBackend(session, storageKey);
  } catch (err) {
    session.markFailed(err.message);
    await bot.leave().catch(() => {});
    await notifyBackend(session, null);
    throw err;
  }
}

async function notifyBackend(session, storageKey) {
  await axios.post(process.env.BACKEND_WEBHOOK_URL, {
    meeting_id: session.meetingId,
    status: session.status,
    recording_path: storageKey,
    duration_seconds: session.durationSeconds(),
    error_message: session.errorMessage,
  }).catch((err) => {
    console.error('Failed to notify backend:', err.message);
  });
}
