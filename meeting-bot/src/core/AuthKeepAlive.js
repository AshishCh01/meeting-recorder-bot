import { BrowserManager, resolveAuthStatePath, persistStorageState } from './BrowserManager.js';

// Both Google and Zoom's stored sessions (auth.json / zoom-auth.json) can
// go stale between real meeting joins - confirmed directly for both during
// testing, not just assumed for Google. The refresh only happens as a side
// effect of an authenticated page load (see BrowserManager.js's
// persistStorageState), which used to only happen inside a real meeting
// join. If no one joined on a given platform in whatever its expiry window
// turns out to be, the file goes stale, and the *next* join attempt fails
// before it ever gets a chance to refresh anything (refreshing requires
// already being authenticated, so a stale file can't self-heal from a
// failed join).
//
// This keeps both warm independently of real traffic: on a fixed interval,
// load an authenticated page for each platform and persist whatever fresh
// cookies come back. If a platform's file is already expired when a cycle
// runs, there's nothing to refresh - that's logged loudly so it gets
// noticed and fixed (see docs/auth-keepalive-runbook.md) instead of
// surfacing later as a silent join failure.
const KEEPALIVE_INTERVAL_MS = Number(process.env.AUTH_KEEPALIVE_INTERVAL_MINUTES || 15) * 60 * 1000;
const INITIAL_DELAY_MS = 10000; // let the server finish booting before the first cycle

const PLATFORMS = [
  {
    label: 'Google',
    launchProfile: 'google-meet',
    authPlatform: 'google',
    // myaccount.google.com, not meet.google.com: confirmed by direct
    // testing that meet.google.com's root URL does NOT hard-redirect to an
    // accounts.google.com sign-in URL on a dead session (it renders some
    // other unauthenticated state instead), which produced a false
    // positive - "refreshed successfully" against a session that was
    // actually still expired. myaccount.google.com reliably redirects to a
    // signin URL when the session is dead. Session cookies are scoped to
    // .google.com broadly, not meet.google.com specifically, so refreshing
    // via this URL still refreshes what GoogleMeetBot needs.
    checkUrl: 'https://myaccount.google.com/',
    // Must be an allowlist ("did we STAY on myaccount?"), not a denylist of
    // one sign-in host. Google no longer sends a dead session to
    // accounts.google.com from here - observed live, it lands on
    // https://www.google.com/account/about/, which contains no
    // "accounts.google.com" and so passed the old check. That reported
    // "auth refreshed successfully" for a session Meet was actually
    // treating as signed out, which is why a stale auth.json produced
    // silent 5-minute admission timeouts instead of an ALERT here.
    isSignedIn: (url) => url.startsWith('https://myaccount.google.com'),
    fixCommand: 'node generate-auth.cjs',
  },
  {
    label: 'Zoom',
    launchProfile: 'zoom',
    authPlatform: 'zoom',
    // Confirmed by direct testing: zoom.us/profile stays put when signed
    // in, redirects to zoom.us/signin when the session is dead.
    checkUrl: 'https://zoom.us/profile',
    isSignedIn: (url) => !url.includes('/signin'),
    fixCommand: 'node generate-zoom-auth.cjs',
  },
];

async function runCycleForPlatform(platform) {
  let context;
  try {
    context = await BrowserManager.launch(platform.launchProfile);
    const page = await context.newPage();
    await page.goto(platform.checkUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(3000);

    const finalUrl = page.url();
    if (!platform.isSignedIn(finalUrl)) {
      console.error('='.repeat(70));
      console.error(`[AuthKeepAlive] ALERT: ${platform.label} auth session has EXPIRED.`);
      console.error(`[AuthKeepAlive] Every ${platform.label} join will fail until this is fixed.`);
      console.error(`[AuthKeepAlive] Fix: run \`${platform.fixCommand}\` and sign in manually.`);
      console.error('[AuthKeepAlive] See docs/auth-keepalive-runbook.md for details.');
      console.error('='.repeat(70));
      return;
    }

    await persistStorageState(context, resolveAuthStatePath(platform.authPlatform));
    console.log(`[AuthKeepAlive] ${platform.label} auth refreshed successfully.`);
  } catch (err) {
    console.error(`[AuthKeepAlive] ${platform.label} cycle failed (will retry next interval):`, err.message);
  } finally {
    await context?.close().catch(() => {});
  }
}

async function runCycle() {
  // Sequential, not parallel - keeps this to one extra headed Chrome
  // instance at a time instead of stacking both platforms' checks onto the
  // shared Xvfb display alongside whatever real meetings may be running.
  for (const platform of PLATFORMS) {
    await runCycleForPlatform(platform);
  }
}

export function startAuthKeepAlive() {
  if (process.env.AUTH_KEEPALIVE_ENABLED === 'false') {
    console.log('[AuthKeepAlive] Disabled via AUTH_KEEPALIVE_ENABLED=false.');
    return;
  }

  const platformNames = PLATFORMS.map((p) => p.label).join(', ');
  console.log(`[AuthKeepAlive] Starting - refreshing ${platformNames} auth every ${KEEPALIVE_INTERVAL_MS / 60000} minute(s).`);

  // Recursive setTimeout, not setInterval - guarantees one cycle fully
  // finishes (both platforms checked, browsers closed) before the next one
  // is scheduled, even if a page load hangs longer than the interval. A
  // single chain: each run schedules the next one itself, rather than a
  // separate timer chain racing alongside the first cycle's delay.
  const runAndReschedule = async () => {
    await runCycle();
    setTimeout(runAndReschedule, KEEPALIVE_INTERVAL_MS);
  };

  setTimeout(runAndReschedule, INITIAL_DELAY_MS);
}
