import axios from 'axios';
import { GoogleMeetBot } from '../platforms/google-meet/GoogleMeetBot.js';
import { ZoomBot } from '../platforms/zoom/ZoomBot.js';
import { Recorder } from '../recording/Recorder.js';
import { RecordingFile } from '../recording/RecordingFile.js';
import { AudioSink } from '../recording/AudioSink.js';
import { SupabaseUploader } from '../storage/SupabaseUploader.js';
import fs from 'fs';

const BOT_CLASSES = {
  google: GoogleMeetBot,
  zoom: ZoomBot,
};

export async function runMeetingLifecycle(session) {
  const BotClass = BOT_CLASSES[session.platform];
  if (!BotClass) throw new Error(`Unsupported platform: ${session.platform}`);

  // Per-session audio sink (phase 2) — the bot's browser audio and this
  // session's ffmpeg capture both get pointed at it below, isolating this
  // meeting's audio from any other concurrently-running meeting's.
  const audioSink = await AudioSink.provision(session.meetingId);
  session.audioSinkName = audioSink.sinkName;

  const bot = new BotClass(session);
  const recorder = new Recorder(session.meetingId, audioSink.monitorSource);

  try {
    await bot.join();

    session.markWaitingForAdmission();
    notifyStatusUpdate(session); // fire-and-forget, never rejects

    await bot.waitForAdmission();

    session.markRecording();
    notifyStatusUpdate(session); // fire-and-forget, never rejects
    recorder.start();

    const MAX_MEETING_MINUTES = Number(process.env.MAX_RECORDING_DURATION_MINUTES || 90);
    const hardDeadline = Date.now() + MAX_MEETING_MINUTES * 60 * 1000;

    while (!session.cancelRequested && await bot.isStillInMeeting()) {
      if (Date.now() > hardDeadline) {
        console.log(`[Lifecycle] Hit ${MAX_MEETING_MINUTES}-minute hard cap — forcing exit`);
        break;
      }
      await new Promise((r) => setTimeout(r, 10000));
    }

    if (session.cancelRequested) {
      console.log('[Lifecycle] Cancelled by user request.');
      session.markFailed('Cancelled by user');
    } else {
      session.markUploading();
      console.log('[Lifecycle] Meeting ended naturally. Initiating shutdown...');
    }

  } catch (err) {
    console.log('[Lifecycle] Process interrupted or errored:', err.message);
    session.markFailed(err.message);
  } finally {
    console.log('[Lifecycle] 1. Closing Chromium...');
    await bot.leave().catch(() => {});

    console.log('[Lifecycle] 3. Safely stopping FFmpeg...');
    const localPath = await recorder.stop().catch((e) => {
      console.error('[Lifecycle] FFmpeg stop error:', e);
      return null;
    });

    // Never let a sink-cleanup failure abort the rest of the shutdown - the
    // recording still needs uploading and the backend still needs notifying.
    await audioSink.release().catch(() => {});

    let uploadedStorageKey = null;

    if (localPath && session.status !== 'failed') {
      console.log(`[Lifecycle] 4. Audio saved to: ${localPath}`);
      try {
        const storageKey = RecordingFile.storageKeyFor(session.userId, session.meetingId);
        await SupabaseUploader.upload(localPath, storageKey);
        uploadedStorageKey = storageKey;
        console.log('[Lifecycle] 5. Upload successful.');
        session.markCompleted();
      } catch (uploadErr) {
        console.error('[Lifecycle] 5. Upload failed:', uploadErr.message);
        session.markFailed(`Upload failed: ${uploadErr.message}`);
      }

      // Cleanup is best-effort and deliberately separate from the
      // upload's success/failure - a locked file (e.g. ffmpeg not yet
      // having released the handle, seen on Windows) shouldn't get
      // reported as an upload failure when the upload itself succeeded.
      try {
        fs.unlinkSync(localPath);
        console.log('[Lifecycle] Local recording file cleaned up.');
      } catch (cleanupErr) {
        console.error('[Lifecycle] Could not delete local recording file:', cleanupErr.message);
      }
    } else if (localPath && session.status === 'failed') {
      console.log('[Lifecycle] 4. Session failed — skipping upload, keeping local file for debug.');
    } else {
      console.log('[Lifecycle] 4. No recording file to upload.');
    }

    await notifyBackend(session, uploadedStorageKey);
    console.log(`[Lifecycle] Done. Meeting ${session.meetingId} status: ${session.status}`);
  }
}

// Best-effort progress ping, distinct from notifyBackend() below - a single
// attempt, no retries. If it's lost, nothing gets stuck: the meeting just
// won't show "waiting for admission" in the UI, and the final
// completed/failed webhook still lands normally at the end of the lifecycle.
async function notifyStatusUpdate(session) {
  // Captured up front - session.status can mutate before this fire-and-forget
  // call's response comes back (e.g. join is admitted almost instantly), which
  // would otherwise make these logs report the wrong status.
  const statusAtCallTime = session.status;
  try {
    await axios.post(process.env.BACKEND_WEBHOOK_URL, {
      user_id: session.userId,
      meeting_id: session.meetingId,
      status: statusAtCallTime,
    }, {
      headers: {
        Authorization: `Bearer ${process.env.BEARER_TOKEN}`
      }
    });
    console.log(`[Lifecycle] Status update sent: ${statusAtCallTime}`);
  } catch (err) {
    console.error(`[Lifecycle] Status update (${statusAtCallTime}) failed:`, err.message);
  }
}

async function notifyBackend(session, storageKey) {
  const MAX_RETRIES = 3;
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      await axios.post(process.env.BACKEND_WEBHOOK_URL, {
        user_id: session.userId,
        meeting_id: session.meetingId,
        status: session.status,
        recording_path: storageKey,
        duration_seconds: session.durationSeconds(),
        error_message: session.errorMessage,
      }, {
        headers: {
          Authorization: `Bearer ${process.env.BEARER_TOKEN}`
        }
      });
      console.log('[Lifecycle] Backend notified successfully');
      return;
    } catch (err) {
      console.error(`[Lifecycle] Webhook attempt ${attempt}/${MAX_RETRIES} failed:`, err.message);
      if (attempt < MAX_RETRIES) {
        await new Promise((r) => setTimeout(r, 3000 * attempt)); // 3s, 6s backoff
      }
    }
  }
  console.error('[Lifecycle] All webhook attempts failed — meeting may be stuck in DB');
}