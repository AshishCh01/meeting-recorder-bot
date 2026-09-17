// generate-zoom-auth.cjs
// Run this locally (not in Docker) to capture a signed-in Zoom session for
// ZoomBot to reuse, the same way generate-auth.cjs does for Google Meet.
// A signed-in join gets far less anti-bot friction than the anonymous guest
// flow - Zoom's own "Automated bots aren't allowed to join this meeting"
// block page suggests signing in as the fix.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

// Phase C4: both halves of a host's identity come from the command, and
// neither has a default.
//
//   ZOOM_USER_DATA_DIR / ZOOM_PROFILE_DIR  which account is captured
//   ZOOM_AUTH_STATE_PATH                   which host's file is written
//
// Same change, same reasoning as generate-auth.cjs - see the comment there for
// why a default identity had to go. Keep the user-data directory separate from
// the Google one (chrome-bot-profile-zoom, not chrome-bot-profile) so this
// session's cookies stay Zoom-only and don't get mixed into auth.json, or
// vice versa, if both scripts are ever pointed at one profile.
//
// Both resolvers are imported rather than reimplemented so the generator and
// the bot can never disagree about where the file lives; a dynamic import is
// how a .cjs reaches an ES module.
async function resolveOutputPath(platform) {
  const { resolveAuthStatePath } = await import('./src/core/BrowserManager.js');
  return resolveAuthStatePath(platform);
}

async function resolveProfile(platform) {
  const { resolveChromeProfile } = await import('./src/core/ChromeProfile.js');
  return resolveChromeProfile(platform);
}

const DRY_RUN = process.argv.includes('--dry-run');

(async () => {
  let userDataDir;
  let profileDir;
  try {
    ({ userDataDir, profileDir } = await resolveProfile('zoom'));
  } catch (err) {
    console.error(err.message);
    process.exit(1);
  }

  const outputPath = await resolveOutputPath('zoom');

  console.log('[0] Zoom session capture');
  console.log('    Chrome user-data dir:', userDataDir);
  console.log('    Chrome profile      :', profileDir);
  console.log('    Writing session to  :', outputPath);

  if (DRY_RUN) {
    console.log('');
    console.log('[dry-run] Configuration is valid. Chrome was not launched and no');
    console.log('[dry-run] file was written. Drop --dry-run to capture for real.');
    return;
  }

  fs.mkdirSync(path.dirname(outputPath), { recursive: true });

  if (!fs.existsSync(userDataDir)) {
    console.log('');
    console.log('    WARNING: that user-data directory does not exist yet. Chrome will');
    console.log('    create an empty profile and you will be signing in from scratch.');
    console.log('    If you meant to reuse an existing profile, stop now (Ctrl+C) and');
    console.log('    check the path.');
  }

  console.log('');
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
  // See generate-auth.cjs: the last point at which a wrong identity is still
  // free to fix, since storageState captures whichever account is signed in
  // and the cookie check below only proves a zoom.us session exists.
  console.log('==> CHECK THE ACCOUNT before continuing. Whichever Zoom account is');
  console.log('==> signed into that window is what gets written to:');
  console.log('==>   ', outputPath);
  console.log('==> If that pairing is wrong, press Ctrl+C - nothing has been written yet.');
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
