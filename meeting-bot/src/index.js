import 'dotenv/config';
import crypto from 'crypto';
import fs from 'fs';
import app from './api/server.js';
import { assertAuthStateExists, resolveAuthStatePath } from './core/BrowserManager.js';
import { startAuthKeepAlive } from './core/AuthKeepAlive.js';
import { PLATFORMS } from './core/AuthHealth.js';

const PORT = process.env.PORT || 3000;

const REQUIRED_ENV_VARS = ['BEARER_TOKEN', 'BACKEND_WEBHOOK_URL', 'SUPABASE_URL', 'SUPABASE_KEY'];

function assertRequiredEnvVars() {
  const missing = REQUIRED_ENV_VARS.filter((name) => !process.env[name] || !process.env[name].trim());
  if (missing.length > 0) {
    throw new Error(
      `Missing required environment variable(s): ${missing.join(', ')}. ` +
      `The process will not start without these set.`
    );
  }
}

// Which credential file this host is on, and enough to tell it apart from
// another host's (Phase C4).
//
// The container-side path is deliberately identical on every host - only the
// mount differs - so the path alone cannot answer "are these two recorders
// actually on different accounts?", which is the question a pool makes people
// ask and which nothing else would answer until two bots started fighting
// over one login. Size plus a short digest does answer it, from the logs, on
// both hosts, with no access to either file.
//
// A digest of the file, never any part of its contents: these are live
// session cookies, and the whole point of the surrounding work is that they
// do not leak.
function describeAuthState(platform) {
  const authStatePath = resolveAuthStatePath(platform);
  try {
    const bytes = fs.readFileSync(authStatePath);
    const digest = crypto.createHash('sha256').update(bytes).digest('hex').slice(0, 12);
    return `${platform}: ${authStatePath} (${bytes.length} bytes, sha256:${digest})`;
  } catch (err) {
    return `${platform}: ${authStatePath} (unreadable: ${err.code || err.message})`;
  }
}

// Fail loudly at startup, not on the first meeting join or the first
// request - missing config is a deploy-time error, not a per-request one.
try {
  assertRequiredEnvVars();
  // Only Google is asserted: a Google-only deployment is a real thing, and
  // BrowserManager.launch asserts Zoom's file when a Zoom meeting actually
  // arrives. Both are *described* below either way, because "the Zoom file is
  // missing" is much better learned at boot than at the first Zoom join.
  assertAuthStateExists();
  for (const platform of PLATFORMS) {
    console.log(`[startup] auth state ${describeAuthState(platform)}`);
  }
} catch (err) {
  console.error(`[startup] ${err.message}`);
  process.exit(1);
}

app.listen(PORT, () => {
  console.log(`meeting-bot listening on port ${PORT}`);
});

startAuthKeepAlive();
