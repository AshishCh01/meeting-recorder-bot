const { chromium } = require('playwright');

(async () => {
  // We are adding specific flags to bypass Google's bot detection
  const browser = await chromium.launch({ 
    headless: false,
    channel: 'chrome', // Forces Playwright to use your real Chrome installation
    args: ['--disable-blink-features=AutomationControlled'] // Hides the "automated" flag
  }); 
  
  const context = await browser.newContext();
  const page = await context.newPage();

  // Route to StackOverflow's Google login first. 
  // It is a known workaround that is much less strict than accounts.google.com
  await page.goto('https://stackoverflow.com/users/login?ssrc=head&returnurl=https%3a%2f%2fstackoverflow.com%2f');

  console.log('1. Click "Log in with Google" on the StackOverflow page.');
  console.log('2. Log in manually, including 2FA if prompted.');
  console.log('3. Once you successfully land on the StackOverflow homepage as a logged-in user, come back here and press Enter.');

  // Pause until you press Enter in the terminal
  await new Promise((resolve) => process.stdin.once('data', resolve));

  await context.storageState({ path: 'auth.json' });
  console.log('Saved auth.json with valid session cookies!');

  await browser.close();
})();