// generate-auth.cjs
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

// Phase C4: both halves of a host's identity come from the command, and
// neither has a default.
//
//   GOOGLE_USER_DATA_DIR / GOOGLE_PROFILE_DIR  which account is captured
//   AUTH_STATE_PATH                            which host's file is written
//
// The destination half has been overridable since these files were first made
// to work with Render Secret Files. The identity half used to be two hardcoded
// lines here with a commented-out alternate pair underneath, and picking a
// host meant editing this script - a step with no error message when you got
// it wrong. It was wrong in git: the committed default was bot-b's profile, so
// the documented bot-a command captured bot-b's account and wrote it into
// bot-a's file, leaving both hosts on one Google login. That is the exact
// failure C4 exists to remove, and nothing in the run reported it, because the
// cookie check at the bottom verifies that *a* session was captured, never
// whose. See src/core/ChromeProfile.js.
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

// Validate and print the resolved configuration without launching Chrome or
// writing anything - cheap enough to run before every real capture, which is
// the point: a mismatched profile costs an interactive sign-in to discover.
const DRY_RUN = process.argv.includes('--dry-run');

(async () => {
  // Identity first, and before anything touches the filesystem: if the command
  // did not say which profile to capture there is nothing safe to guess, and
  // the run must not reach Chrome.
  let userDataDir;
  let profileDir;
  try {
    ({ userDataDir, profileDir } = await resolveProfile('google'));
  } catch (err) {
    console.error(err.message);
    process.exit(1);
  }

  const outputPath = await resolveOutputPath('google');

  console.log('[0] Google session capture');
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

  // launchPersistentContext creates a missing user-data dir rather than
  // failing, which silently turns a typo into a brand-new signed-out profile.
  // Legitimate on a host's first-ever setup, so this warns instead of exiting.
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

  console.log('[2] Chrome launched. Opening Meet...');

  const page = context.pages()[0] || (await context.newPage());

  // Retry navigation — Meet's service worker sometimes throws a network error on first load
  let loaded = false;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      await page.goto('https://meet.google.com/', {
        waitUntil: 'domcontentloaded',
        timeout: 30000,
      });
      loaded = true;
      console.log(`[3] Meet loaded (attempt ${attempt})`);
      break;
    } catch (err) {
      console.log(`[3] Navigation attempt ${attempt} failed: ${err.message}`);
      await page.waitForTimeout(2000);
    }
  }

  if (!loaded) {
    console.log('[3] Auto-navigation failed. Type meet.google.com in the browser manually.');
  }

  await page.waitForTimeout(5000);

  // Check login state programmatically
  const bodyText = await page.innerText('body').catch(() => '');
  const looksLoggedOut = /sign in to continue|^sign in$/im.test(bodyText) ||
                          bodyText.includes('Sign in') && !bodyText.includes('New meeting');

  console.log('[4] Current URL:', page.url());
  console.log('[4] Login state guess:', looksLoggedOut ? 'LOGGED OUT ⚠️' : 'LOGGED IN ✓');
  console.log('');
  console.log('==> Look at the browser window now.');
  console.log('==> You should see the account avatar top-right and a "New meeting" button.');
  console.log('==> If you see a network error, press Ctrl+Shift+R in the browser to reload.');
  console.log('');
  // The last point at which a wrong identity is still free to fix. Nothing
  // downstream can catch it: storageState captures whichever account the
  // window is signed into, and the cookie check below only proves a session
  // exists. Restating the destination next to the account is the whole
  // safeguard, so it sits directly above the blocking read.
  console.log('==> CHECK THE ACCOUNT before continuing. Whichever account is signed');
  console.log('==> into that window is what gets written to:');
  console.log('==>   ', outputPath);
  console.log('==> If that pairing is wrong, press Ctrl+C - nothing has been written yet.');
  console.log('');
  console.log('==> IMPORTANT: click on THIS TERMINAL WINDOW first, then press Enter.');

  await new Promise((resolve) => process.stdin.once('data', resolve));

  await context.storageState({ path: outputPath });

  // Verify what we actually captured
  const saved = JSON.parse(fs.readFileSync(outputPath, 'utf8'));
  const names = saved.cookies.map((c) => c.name);
  const critical = ['SID', 'HSID', 'SSID', 'SAPISID', '__Secure-1PSID', '__Secure-3PSID'];
  const found = critical.filter((n) => names.includes(n));
  const missing = critical.filter((n) => !names.includes(n));

  console.log('');
  console.log(`[5] Saved ${outputPath} — ${saved.cookies.length} cookies total`);
  console.log('    Found:  ', found.join(', ') || '(none)');
  console.log('    Missing:', missing.join(', ') || '(none)');
  console.log('');
  if (found.includes('SID') && found.includes('SAPISID')) {
    console.log('    ✓ Looks like a real logged-in session.');
  } else {
    console.log('    ⚠️  Key session cookies missing — this likely will not authenticate.');
  }

  await context.close();
})();