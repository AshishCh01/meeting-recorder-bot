import express from 'express';
import crypto from 'crypto';
import fs from 'fs';
import { MeetingSession } from '../core/MeetingSession.js';
import { runMeetingLifecycle, runReupload } from '../core/MeetingLifecycle.js';
import { RecordingFile } from '../recording/RecordingFile.js';

const app = express();
app.use(express.json());

// Session registry (audit finding C-1, phases 1-2 complete).
// Keyed by meetingId so multiple sessions can be tracked concurrently.
// Audio is now isolated per session on Linux (AudioSink.js provisions a
// dedicated PulseAudio sink per meetingId, and BrowserManager/FFmpegManager
// both point at it — see MeetingLifecycle.js), so MAX_CONCURRENT_MEETINGS
// can be raised above 1 via env var. Note: Xvfb display (shared :99) and
// auth identity (shared auth.json per platform) are still global, not
// per-session — see the audit notes; raising this above 1 is safe for
// audio specifically, not yet a full concurrency guarantee.
const MAX_CONCURRENT_MEETINGS = Number(process.env.MAX_CONCURRENT_MEETINGS || 1);
const activeMeetings = new Map(); // meetingId -> MeetingSession
// Re-uploads in progress (POST /reupload). Kept apart from activeMeetings on
// purpose: an upload holds no browser or audio sink, so it must not count
// against recording capacity - GET /capacity would otherwise report a free
// recorder as busy for the length of an upload.
const reuploadsInFlight = new Set(); // meetingId

function timingSafeTokenEqual(provided, expected) {
  // An unset/empty server secret or an unset/empty provided token must
  // never match anything. Without this check, `Buffer.from(expected ||
  // '', ...)` turned an unset BEARER_TOKEN into a zero-length buffer,
  // and a request sent with NO Authorization header at all produced an
  // equally zero-length `provided` buffer - crypto.timingSafeEqual
  // returns true for two zero-length buffers, so auth silently passed
  // with no credentials presented whatsoever.
  if (!expected || !provided) {
    return false;
  }
  const a = Buffer.from(provided, 'utf8');
  const b = Buffer.from(expected, 'utf8');
  // Buffers of different length would throw in timingSafeEqual, so pad
  // the shorter one — this still fails the comparison, just safely.
  if (a.length !== b.length) {
    return false;
  }
  return crypto.timingSafeEqual(a, b);
}

function requireAuth(req, res, next) {
  // Audit L-2: token should be in the Authorization header, not the body.
  // Supporting both during migration — remove the body fallback once
  // bot_service.py is updated to send the header instead.
  const headerToken = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
  const providedToken = headerToken || req.body.bearerToken;

  // Audit L-1: use constant-time comparison instead of !== to avoid
  // leaking token length/content via response-timing side channels.
  if (!timingSafeTokenEqual(providedToken, process.env.BEARER_TOKEN)) {
    return res.status(401).json({ error: 'Invalid bearer token' });
  }
  next();
}

// The single source of truth for "does this bot have room?", shared by
// GET /capacity and the join handler's admission check (see below).
// Pure and export-ed so it can be tested at any active count - a real
// active recording needs a real browser, so the arithmetic is tested
// here and only the wiring is tested through the endpoint.
// `available` is floored at 0: activeCount can exceed max if
// MAX_CONCURRENT_MEETINGS is lowered while meetings are in flight, and
// negative headroom is not a thing a caller should have to reason about.
export function computeCapacity(activeCount, max) {
  return {
    active: activeCount,
    max,
    available: Math.max(0, max - activeCount),
  };
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function makeJoinHandler(platform) {
  return (req, res) => {
    const { url, meetingId, userId, botDisplayName } = req.body;
    if (!url || !meetingId || !userId) {
      return res.status(400).json({ error: 'url, meetingId, and userId are required' });
    }

    // Defense-in-depth: meetingId/userId end up in local file paths
    // (RecordingFile.pathFor) and Supabase Storage keys - don't trust
    // their shape just because the caller holds the bearer token.
    if (!UUID_RE.test(meetingId) || !UUID_RE.test(userId)) {
      return res.status(400).json({ error: 'meetingId and userId must be UUIDs' });
    }

    if (activeMeetings.has(meetingId)) {
      return res.status(409).json({ error: `Meeting ${meetingId} is already active` });
    }
    // Admission and GET /capacity must never disagree: both ask
    // computeCapacity() the same question, so a bot that reports
    // available > 0 is exactly a bot that will accept a join.
    if (computeCapacity(activeMeetings.size, MAX_CONCURRENT_MEETINGS).available === 0) {
      return res.status(409).json({ error: 'Bot is currently busy with another meeting' });
    }

    const session = new MeetingSession({ meetingId, meetingUrl: url, platform, userId, botDisplayName });
    activeMeetings.set(meetingId, session);

    res.status(202).json({ status: 'accepted', meetingId });

    runMeetingLifecycle(session)
      .catch((err) => {
        console.error(`[${platform}] meeting ${meetingId} failed:`, err.message);
      })
      .finally(() => {
        activeMeetings.delete(meetingId);
        console.log(`[${platform}] meeting ${meetingId} handler finished.`);
      });
  };
}

// No /teams/join: MeetingLifecycle's BOT_CLASSES has no Teams implementation,
// so the route only ever threw "Unsupported platform: teams" before the
// lifecycle's try/finally was entered - meaning no webhook fired and the
// meeting sat in "joining" until the backend watchdog TTL swept it. The
// backend's platform allowlist already rejects Teams URLs upstream; this
// removes the second, unreachable way in. Add the route back alongside a real
// BOT_CLASSES entry if Teams support is ever implemented.
app.post('/google/join', requireAuth, makeJoinHandler('google'));
app.post('/zoom/join', requireAuth, makeJoinHandler('zoom'));

app.post('/stop', requireAuth, (req, res) => {
  const { meetingId } = req.body;

  if (meetingId) {
    const session = activeMeetings.get(meetingId);
    if (!session) {
      return res.status(404).json({ error: `No active meeting ${meetingId} to stop` });
    }
    console.log(`[server] Stop requested for meeting ${meetingId}`);
    session.requestCancel();
    return res.json({ status: 'stopping', meetingId });
  }

  // No meetingId given: only unambiguous while at most one meeting is
  // active. Callers written against the old single-session API relied on
  // this; once MAX_CONCURRENT_MEETINGS > 1 they must start passing meetingId.
  if (activeMeetings.size === 0) {
    return res.status(404).json({ error: 'No active meeting to stop' });
  }
  if (activeMeetings.size > 1) {
    return res.status(400).json({ error: 'Multiple meetings active — meetingId is required' });
  }

  const [[onlyMeetingId, onlySession]] = activeMeetings;
  console.log(`[server] Stop requested for meeting ${onlyMeetingId}`);
  onlySession.requestCancel();
  res.json({ status: 'stopping', meetingId: onlyMeetingId });
});

// Recovers a recording whose upload failed. MeetingLifecycle keeps the local
// file in that case, and RecordingFile.pathFor is deterministic, so the id is
// enough to find it. 202-then-webhook rather than synchronous: the backend
// calls this with a 10s timeout, and an hour-long recording is ~86MB. The
// outcome arrives as the normal completed/failed webhook.
app.post('/reupload', requireAuth, (req, res) => {
  const { meetingId, userId } = req.body;
  if (!meetingId || !userId) {
    return res.status(400).json({ error: 'meetingId and userId are required' });
  }
  // Not just defense-in-depth here: meetingId is joined straight into a
  // local file path below, before any session exists to vouch for it.
  if (!UUID_RE.test(meetingId) || !UUID_RE.test(userId)) {
    return res.status(400).json({ error: 'meetingId and userId must be UUIDs' });
  }

  // An active session may still be writing this file; a second re-upload
  // would race the first one's unlink.
  if (activeMeetings.has(meetingId)) {
    return res.status(409).json({ error: `Meeting ${meetingId} is still active` });
  }
  if (reuploadsInFlight.has(meetingId)) {
    return res.status(409).json({ error: `Meeting ${meetingId} is already being re-uploaded` });
  }

  const localPath = RecordingFile.pathFor(meetingId);
  if (!fs.existsSync(localPath)) {
    return res.status(404).json({ error: `No preserved recording for meeting ${meetingId}` });
  }

  reuploadsInFlight.add(meetingId);
  res.status(202).json({ status: 'accepted', meetingId });

  runReupload({ meetingId, userId })
    .catch((err) => {
      console.error(`[reupload] meeting ${meetingId} failed:`, err.message);
    })
    .finally(() => {
      reuploadsInFlight.delete(meetingId);
    });
});

// Unlike /health, this requires auth: it discloses operational state
// (how loaded this host is, and which meetings it holds), and its only
// caller is the backend, which already sends the bearer token.
app.get('/capacity', requireAuth, (req, res) => {
  res.json({
    ...computeCapacity(activeMeetings.size, MAX_CONCURRENT_MEETINGS),
    // Costs nothing and turns "the bot says it's busy" into "busy with
    // which meeting" - the question you actually ask at 2am.
    meetingIds: [...activeMeetings.keys()],
  });
});

app.get('/health', (req, res) => {
  res.json({
    status: 'ok'
  });
});

export default app;