// generate-auth.cjs
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

// Phase C4: write to the path the *runtime* will read, not a hardcoded
// filename in the current directory. Before this, generating bot-b's
// credentials meant running this script and then remembering to move
// auth.json into meeting-bot/auth/bot-b/ by hand - a step with no error
// message when you skipped it, which silently left bot-b on bot-a's
// identity. Imported from BrowserManager rather than reimplemented so the
// generator and the bot can never disagree about where the file lives; a
// dynamic import is how a .cjs reaches an ES module.
//
//   AUTH_STATE_PATH=auth/bot-b/auth.json node generate-auth.cjs
//
// With nothing set it resolves to meeting-bot/auth.json exactly as before.
async function resolveOutputPath(platform) {
  const { resolveAuthStatePath } = await import('./src/core/BrowserManager.js');
  return resolveAuthStatePath(platform);
}

(async () => {
  const outputPath = await resolveOutputPath('google');
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  console.log('[0] Will write the captured session to:', outputPath);

  
   // const userDataDir = 'C:\\chrome-bot-profile'; // copied from default Chrome dir — Chrome refuses CDP debugging on the real default profile path
   // const profileDir = 'Profile 1'; // bot account: bmeeting53@gmail.com
   const userDataDir = 'C:\\chrome-bot-b-profile'; 
   const profileDir = 'Default'; // bot account: ashishch010101@gmail.com

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