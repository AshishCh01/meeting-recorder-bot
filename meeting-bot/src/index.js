import 'dotenv/config';
import app from './api/server.js';
import { assertAuthStateExists } from './core/BrowserManager.js';

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

// Fail loudly at startup, not on the first meeting join or the first
// request - missing config is a deploy-time error, not a per-request one.
try {
  assertRequiredEnvVars();
  const authStatePath = assertAuthStateExists();
  console.log(`[startup] Using auth state file: ${authStatePath}`);
} catch (err) {
  console.error(`[startup] ${err.message}`);
  process.exit(1);
}

app.listen(PORT, () => {
  console.log(`meeting-bot listening on port ${PORT}`);
});
