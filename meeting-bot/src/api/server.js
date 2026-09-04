import express from 'express';
import crypto from 'crypto';
import { MeetingSession } from '../core/MeetingSession.js';
import { runMeetingLifecycle } from '../core/MeetingLifecycle.js';

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
    if (activeMeetings.size >= MAX_CONCURRENT_MEETINGS) {
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

app.post('/google/join', requireAuth, makeJoinHandler('google'));
app.post('/zoom/join', requireAuth, makeJoinHandler('zoom'));
app.post('/teams/join', requireAuth, makeJoinHandler('teams'));

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

app.get('/health', (req, res) => {
  res.json({
    status: 'ok'
  });
});

export default app;