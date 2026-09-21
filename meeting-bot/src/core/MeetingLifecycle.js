import axios from 'axios';
import { GoogleMeetBot } from '../platforms/google-meet/GoogleMeetBot.js';
import { ZoomBot } from '../platforms/zoom/ZoomBot.js';
import { Recorder } from '../recording/Recorder.js';
import { RecordingFile } from '../recording/RecordingFile.js';
import { AudioSink } from '../recording/AudioSink.js';
import { recordJoinFailure } from './AuthHealth.js';
import { MeetingSession } from './MeetingSession.js';
import { SupabaseUploader } from '../storage/SupabaseUploader.js';
import fs from 'fs';

const BOT_CLASSES = {
  google: GoogleMeetBot,
  zoom: ZoomBot,
};

export async function runMeetingLifecycle(session, {
  provisionSink = (meetingId) => AudioSink.provision(meetingId),
} = {}) {
  // Everything that can fail belongs inside the try. By the time this runs,
  // server.js has already answered the backend 202, so the only way the
  // backend learns a meeting failed is notifyBackend in the finally below.
  // Anything thrown before the try skips that finally: server.js's .catch
  // only logs it, and the meeting sits in "joining" until the watchdog sweeps
  // it ten minutes later with a generic message. AudioSink.provision() and
  // the platform check used to live up here, and provision() fails on any
  // Linux host whose PulseAudio is down - see
  // test/lifecycle.failure-reporting.test.js.
  let bot = null;
  let recorder = null;
  let audioSink = null;

  try {
    const BotClass = BOT_CLASSES[session.platform];
    if (!BotClass) throw new Error(`Unsupported platform: ${session.platform}`);

    // Per-session audio sink (phase 2) — the bot's browser audio and this
    // session's ffmpeg capture both get pointed at it below, isolating this
    // meeting's audio from any other concurrently-running meeting's.
    audioSink = await provisionSink(session.meetingId);
    session.audioSinkName = audioSink.sinkName;

    bot = new BotClass(session);
    recorder = new Recorder(session.meetingId, audioSink.monitorSource);

    await bot.join();

    session.markWaitingForAdmission();
    notifyStatusUpdate(session); // fire-and-forget, never rejects

    await bot.waitForAdmission();

    session.markRecording();
    notifyStatusUpdate(session); // fire-and-forget, never rejects
    recorder.start();

    // This host's own ceiling, and then the caller's plan cap on top of it
    // (backend billing Phase 2). The smaller wins: a plan may shorten a
    // recording but never extend it past what this host is configured for,
    // so raising a tier in the backend cannot quietly make every recorder
    // run longer. session.maxDurationMinutes is null for any caller that
    // sends none, which leaves the env var in sole charge exactly as before.
    const HOST_MAX_MINUTES = Number(process.env.MAX_RECORDING_DURATION_MINUTES || 90);
    const MAX_MEETING_MINUTES = session.maxDurationMinutes
      ? Math.min(HOST_MAX_MINUTES, session.maxDurationMinutes)
      : HOST_MAX_MINUTES;
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
    // Phase C4: an AUTH_EXPIRED thrown by a real join is the strongest and
    // fastest evidence this host's credential for this platform is dead -
    // the keepalive would not notice for up to its 15-minute interval, and
    // every meeting dispatched here in the meantime would be shredded the
    // same way. recordJoinFailure ignores every other kind of failure.
    recordJoinFailure(session.platform, err.message);
    session.markFailed(err.message);
  } finally {
    // Each of these may never have been created, if the try failed before
    // reaching it. Skip what does not exist; never skip notifyBackend.
    if (bot) {
      console.log('[Lifecycle] 1. Closing Chromium...');
      await bot.leave().catch(() => {});
    }

    let localPath = null;
    if (recorder) {
      console.log('[Lifecycle] 3. Safely stopping FFmpeg...');
      localPath = await recorder.stop().catch((e) => {
        console.error('[Lifecycle] FFmpeg stop error:', e);
        return null;
      });
    }

    // Never let a sink-cleanup failure abort the rest of the shutdown - the
    // recording still needs uploading and the backend still needs notifying.
    if (audioSink) {
      await audioSink.release().catch(() => {});
    }

    let uploadedStorageKey = null;

    if (localPath && session.status !== 'failed') {
      console.log(`[Lifecycle] 4. Audio saved to: ${localPath}`);
      uploadedStorageKey = await uploadRecording(session, localPath);
    } else if (localPath && session.status === 'failed') {
      console.log('[Lifecycle] 4. Session failed — skipping upload, keeping local file for debug.');
    } else {
      console.log('[Lifecycle] 4. No recording file to upload.');
    }

    await notifyBackend(session, uploadedStorageKey);
    console.log(`[Lifecycle] Done. Meeting ${session.meetingId} status: ${session.status}`);
  }
}

// Uploads a finished recording and marks the session completed or failed.
// Returns the storage key on success, null on failure. `upload` is injectable
// so tests can drive this without Supabase.
//
// The local file is deleted only after a confirmed upload. It used to be
// deleted either way, which made a failed upload destroy the only copy of the
// recording (meeting 4e7fe19b). Kept, it can be recovered with POST /reupload.
export async function uploadRecording(session, localPath, {
  upload = (filePath, storageKey) => SupabaseUploader.upload(filePath, storageKey),
} = {}) {
  let uploadedStorageKey = null;
  try {
    const storageKey = RecordingFile.storageKeyFor(session.userId, session.meetingId);
    await upload(localPath, storageKey);
    uploadedStorageKey = storageKey;
    console.log('[Lifecycle] 5. Upload successful.');
    session.markCompleted();
  } catch (uploadErr) {
    console.error('[Lifecycle] 5. Upload failed:', uploadErr.message);
    session.markFailed(
      `Upload failed: ${uploadErr.message}. The recording is preserved on the recorder and can be retried.`
    );
  }

  if (uploadedStorageKey) {
    // Still best-effort, and still separate from the upload's try/catch - a
    // locked file (e.g. ffmpeg not yet having released the handle, seen on
    // Windows) shouldn't get reported as an upload failure when the upload
    // itself succeeded. It is just no longer unconditional.
    try {
      fs.unlinkSync(localPath);
      console.log('[Lifecycle] Local recording file cleaned up.');
    } catch (cleanupErr) {
      console.error('[Lifecycle] Could not delete local recording file:', cleanupErr.message);
    }
  } else {
    console.log(`[Lifecycle] Keeping local recording for retry: ${localPath}`);
  }

  return uploadedStorageKey;
}

// POST /reupload's background half: uploads a recording kept by an earlier
// failed upload, then reports through the normal webhook. `completed` carries
// recording_path, which the backend's completed branch requires. There is no
// duration to report - the original session's timings died with it - but the
// backend never stored one for the failed meeting either, so nothing is lost.
export async function runReupload({ meetingId, userId }) {
  const session = new MeetingSession({ meetingId, userId, meetingUrl: null, platform: null });
  session.markUploading();

  const localPath = RecordingFile.pathFor(meetingId);
  console.log(`[Lifecycle] Re-uploading preserved recording: ${localPath}`);
  const uploadedStorageKey = await uploadRecording(session, localPath);

  await notifyBackend(session, uploadedStorageKey);
  console.log(`[Lifecycle] Re-upload done. Meeting ${meetingId} status: ${session.status}`);
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