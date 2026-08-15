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

    const MAX_MEETING_MINUTES = Number(process.env.MAX_RECORDING_DURATION_MINUTES || 90);
    const hardDeadline = Date.now() + MAX_MEETING_MINUTES * 60 * 1000;

    while (await bot.isStillInMeeting()) {
      if (Date.now() > hardDeadline) {
        console.log(`[Lifecycle] Hit ${MAX_MEETING_MINUTES}-minute hard cap — forcing exit`);
        break;
      }
      await new Promise((r) => setTimeout(r, 10000));
    }

    session.markUploading();
    console.log('[Lifecycle] Meeting ended naturally. Initiating shutdown...');

  } catch (err) {
    console.log('[Lifecycle] Process interrupted or errored:', err.message);
    session.markFailed(err.message);
  } finally {
    console.log('[Lifecycle] 1. Closing Chromium...');
    await bot.leave().catch(() => {});

    console.log('[Lifecycle] 2. Safely stopping FFmpeg...');
    const localPath = await recorder.stop().catch((e) => {
      console.error('[Lifecycle] FFmpeg stop error:', e);
      return null;
    });

    let uploadedStorageKey = null;

    if (localPath && session.status !== 'failed') {
      console.log(`[Lifecycle] 3. Audio saved to: ${localPath}`);
      try {
        const storageKey = RecordingFile.storageKeyFor(session.meetingId);
        await SupabaseUploader.upload(localPath, storageKey);
        fs.unlinkSync(localPath);
        uploadedStorageKey = storageKey;
        console.log('[Lifecycle] 4. Upload successful, local file cleaned up.');
        session.markCompleted();
      } catch (uploadErr) {
        console.error('[Lifecycle] 4. Upload failed:', uploadErr.message);
        session.markFailed(`Upload failed: ${uploadErr.message}`);
      }
    } else if (localPath && session.status === 'failed') {
      console.log('[Lifecycle] 3. Session failed — skipping upload, keeping local file for debug.');
    } else {
      console.log('[Lifecycle] 3. No recording file to upload.');
    }

    await notifyBackend(session, uploadedStorageKey);

    // Do NOT call process.exit() here — it kills the entire bot service,
    // meaning every meeting after the first one gets a connection refused
    // error from the backend because the Express server is dead.
    // The lifecycle function simply returns and the bot stays alive,
    // ready to accept the next POST /google/join or /zoom/join request.
    console.log(`[Lifecycle] Done. Meeting ${session.meetingId} status: ${session.status}`);
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
    console.error('[Lifecycle] Failed to notify backend:', err.message);
  });
}