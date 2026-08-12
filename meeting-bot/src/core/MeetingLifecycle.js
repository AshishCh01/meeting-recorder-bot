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

    // Poll every 10s until the meeting ends
    while (await bot.isStillInMeeting()) {
      await new Promise((r) => setTimeout(r, 10000));
    }

    session.markUploading();
    console.log('[Lifecycle] Meeting ended naturally. Initiating shutdown...');

  } catch (err) {
    console.log('[Lifecycle] Process interrupted or errored:', err.message);
    session.markFailed(err.message);
  } finally {
    // ALWAYS run this cleanup block, even if the bot crashes!
    console.log('[Lifecycle] 1. Closing Chromium...');
    await bot.leave().catch(() => {});

    console.log('[Lifecycle] 2. Safely stopping FFmpeg to prevent file corruption...');
    // If we do not await this, the file gets corrupted.
    const localPath = await recorder.stop().catch((e) => {
      console.error('[Lifecycle] FFmpeg stop error:', e);
      return null;
    });

    if (localPath) {
      console.log(`[Lifecycle] 3. Audio safely saved to: ${localPath}`);
      try {
        const storageKey = RecordingFile.storageKeyFor(session.meetingId);
        // In local testing, Supabase will fail if keys aren't set. 
        // We catch it so it doesn't accidentally delete your test file.
        await SupabaseUploader.upload(localPath, storageKey);
        fs.unlinkSync(localPath); // Only deletes the file if upload succeeds
        console.log('[Lifecycle] 4. Upload successful, local file cleaned up.');
      } catch (uploadErr) {
        console.log('[Lifecycle] 4. Supabase upload skipped (expected in local testing). Local file kept.');
      }
    }

    session.markCompleted();
    await notifyBackend(session, localPath ? RecordingFile.storageKeyFor(session.meetingId) : null);

    console.log('[Lifecycle] 5. Shutting down NPM server in 3 seconds...');
    setTimeout(() => {
      process.exit(session.status === 'failed' ? 1 : 0);
    }, 3000);
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
    console.error('[Lifecycle] Failed to notify backend (Expected if backend is offline):', err.message);
  });
}