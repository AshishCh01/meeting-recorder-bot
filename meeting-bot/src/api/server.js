import express from 'express';
import { MeetingSession } from '../core/MeetingSession.js';
import { runMeetingLifecycle } from '../core/MeetingLifecycle.js';

const app = express();
app.use(express.json());

function requireAuth(req, res, next) {
  if (req.body.bearerToken !== process.env.BEARER_TOKEN) {
    return res.status(401).json({ error: 'Invalid bearer token' });
  }
  next();
}

function makeJoinHandler(platform) {
  return (req, res) => {
    const { url, meetingId } = req.body;
    if (!url || !meetingId) {
      return res.status(400).json({ error: 'url and meetingId are required' });
    }

    const session = new MeetingSession({ meetingId, meetingUrl: url, platform });

    // Respond immediately — the job runs in the background and reports
    // back to the backend via webhook when it finishes.
    res.status(202).json({ status: 'accepted', meetingId });

    runMeetingLifecycle(session).catch((err) => {
      console.error(`[${platform}] meeting ${meetingId} failed:`, err.message);
    });
  };
}

app.post('/google/join', requireAuth, makeJoinHandler('google'));
app.post('/zoom/join', requireAuth, makeJoinHandler('zoom'));
app.post('/teams/join', requireAuth, makeJoinHandler('teams'));

app.get('/health', (req, res) => res.json({ status: 'ok' }));

export default app;
