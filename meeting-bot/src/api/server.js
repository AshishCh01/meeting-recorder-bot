import express from 'express';
import crypto from 'crypto';
import { MeetingSession } from '../core/MeetingSession.js';
import { runMeetingLifecycle } from '../core/MeetingLifecycle.js';

const app = express();
app.use(express.json());

// Single-session guard (audit finding C-1).
// AudioRouter mutates the global Windows audio device via a static
// singleton — if two meetings overlap, the second one's routeToCable()
// overwrites the first's saved "previous device", and both meetings'
// routeBack() calls restore to the wrong device. Concurrent FFmpeg
// processes would also compete for the same virtual audio cable. Until
// AudioRouter is refactored to be per-session, only one meeting may be
// active system-wide at a time.
let activeMeeting = null;

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
    const { url, meetingId, userId } = req.body;
    if (!url || !meetingId || !userId) {
      return res.status(400).json({ error: 'url, meetingId, and userId are required' });
    }

    // Defense-in-depth: meetingId/userId end up in local file paths
    // (RecordingFile.pathFor) and Supabase Storage keys - don't trust
    // their shape just because the caller holds the bearer token.
    if (!UUID_RE.test(meetingId) || !UUID_RE.test(userId)) {
      return res.status(400).json({ error: 'meetingId and userId must be UUIDs' });
    }

    if (activeMeeting) {
      return res.status(409).json({ error: 'Bot is currently busy with another meeting' });
    }
    
    activeMeeting = meetingId;

    const session = new MeetingSession({ meetingId, meetingUrl: url, platform, userId });

    res.status(202).json({ status: 'accepted', meetingId });

    runMeetingLifecycle(session)
      .catch((err) => {
        console.error(`[${platform}] meeting ${meetingId} failed:`, err.message);
      })
      .finally(() => {
        activeMeeting = null;
        console.log(`[${platform}] meeting ${meetingId} handler finished.`);
      });
  };
}

app.post('/google/join', requireAuth, makeJoinHandler('google'));
app.post('/zoom/join', requireAuth, makeJoinHandler('zoom'));
app.post('/teams/join', requireAuth, makeJoinHandler('teams'));

app.get('/health', (req, res) => {
  res.json({
    status: 'ok'
  });
});

export default app;