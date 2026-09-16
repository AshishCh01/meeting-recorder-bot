// generate-zoom-auth.cjs
// Run this locally (not in Docker) to capture a signed-in Zoom session for
// ZoomBot to reuse, the same way generate-auth.cjs does for Google Meet.
// A signed-in join gets far less anti-bot friction than the anonymous guest
// flow - Zoom's own "Automated bots aren't allowed to join this meeting"
// block page suggests signing in as the fix.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

// Phase C4: write to the path the *runtime* will read, not a hardcoded
// filename in the current directory. Before this, generating bot-b's
// credentials meant running this script and then remembering to move
// zoom-auth.json into meeting-bot/auth/bot-b/ by hand - a step with no error
// message when you skipped it, which silently left bot-b on bot-a's
// identity. Imported from BrowserManager rather than reimplemented so the
// generator and the bot can never disagree about where the file lives; a
// dynamic import is how a .cjs reaches an ES module.
//
//   ZOOM_AUTH_STATE_PATH=auth/bot-b/zoom-auth.json node generate-zoom-auth.cjs
//
// With nothing set it resolves to meeting-bot/zoom-auth.json exactly as before.
async function resolveOutputPath(platform) {
  const { resolveAuthStatePath } = await import('./src/core/BrowserManager.js');
  return resolveAuthStatePath(platform);
}

(async () => {
  const outputPath = await resolveOutputPath('zoom');
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  console.log('[0] Will write the captured session to:', outputPath);

  // Separate profile dir from generate-auth.cjs's chrome-bot-profile, so this
  // session's cookies stay Zoom-only and don't get mixed into auth.json (or
  // vice versa) if both scripts are ever run against the same profile.
  const userDataDir = 'C:\\chrome-bot-profile-zoom';
  const profileDir = 'Profile 1'; // bot's dedicated Zoom account
  
  // const userDataDir = 'C:\\chrome-bot-b-profile';
  // const profileDir = 'Default'; // bot account: ashishch010101@gmail.com
  console.log('[1] Launching Chrome with profile:', profileDir);

  let context;
  try {
    context = await chromium.launchPersistentContext(userDataDir, {
      headless: false,
      channel: 'chrome',
      args: [`--profile-directory=${profileDir}`],
      viewport: null,
    });
  } catch (err) {
    console.error('[FAIL] Could not launch Chrome:', err.message);
    console.error('       Is Chrome fully closed? Run: taskkill /F /IM chrome.exe');
    process.exit(1);
  }

  console.log('[2] Chrome launched. Opening Zoom sign-in...');

  const page = context.pages()[0] || (await context.newPage());

  let loaded = false;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      await page.goto('https://zoom.us/signin', {
        waitUntil: 'domcontentloaded',
        timeout: 30000,
      });
      loaded = true;
      console.log(`[3] Zoom sign-in loaded (attempt ${attempt})`);
      break;
    } catch (err) {
      console.log(`[3] Navigation attempt ${attempt} failed: ${err.message}`);
      await page.waitForTimeout(2000);
    }
  }

  if (!loaded) {
    console.log('[3] Auto-navigation failed. Type zoom.us/signin in the browser manually.');
  }

  console.log('');
  console.log('==> Log in to the Zoom account the bot should join meetings as.');
  console.log('==> Complete any CAPTCHA / 2FA Zoom asks for - this only needs to happen once.');
  console.log('==> Once logged in (you see the Zoom account dashboard), also open a new tab');
  console.log('    to https://app.zoom.us so its session cookie gets set too - that\'s the');
  console.log('    domain ZoomBot actually joins meetings through.');
  console.log('');
  console.log('==> IMPORTANT: click on THIS TERMINAL WINDOW first, then press Enter.');

  await new Promise((resolve) => process.stdin.once('data', resolve));

  await context.storageState({ path: outputPath });

  const saved = JSON.parse(fs.readFileSync(outputPath, 'utf8'));
  const zoomCookies = saved.cookies.filter((c) => c.domain.includes('zoom.us'));

  console.log('');
  console.log(`[4] Saved ${outputPath} - ${saved.cookies.length} cookies total, ${zoomCookies.length} on zoom.us domains`);
  if (zoomCookies.length === 0) {
    console.log('    WARNING: no zoom.us cookies captured - this likely will not authenticate.');
  } else {
    console.log('    Looks like a Zoom session was captured, and it is already at the path');
    console.log('    ZOOM_AUTH_STATE_PATH resolves to - no copying needed.');
  }

  await context.close();
})();
