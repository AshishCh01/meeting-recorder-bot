import { MeetingBot } from '../../core/MeetingBot.js';
import { BrowserManager } from '../../core/BrowserManager.js';
import { ZOOM_SELECTORS } from './selectors.js';
import { isAdmitted, hasMeetingEnded } from './detector.js';

const ALONE_GRACE_PERIOD_MS = 60000;

export class ZoomBot extends MeetingBot {
  constructor(session) {
    super(session);
    this.aloneSince = null;
  }

  static buildDirectWebClientUrl(inviteUrl) {
    try {
      const url = new URL(inviteUrl);
      const meetingId = url.pathname.replace('/j/', '').replace('/', '');
      const pwd = url.searchParams.get('pwd');
      const directUrl = `https://app.zoom.us/wc/${meetingId}/join${pwd ? `?pwd=${pwd}` : ''}`;
      console.log('[ZoomBot] Direct URL:', directUrl);
      return directUrl;
    } catch {
      console.log('[ZoomBot] Could not build direct URL, using original');
      return inviteUrl;
    }
  }

  // Finds the frame that contains a VISIBLE input field.
  // Skips frames that only contain hidden inputs (like cdn_path config fields)
  // since the name input we need is always visually rendered.
  async findFrameWithVisibleInput() {
    const frames = this.page.frames();
    console.log(`[ZoomBot] Scanning ${frames.length} frame(s) for visible inputs...`);

    for (const frame of frames) {
      const frameUrl = frame.url();
      try {
        // Use evaluate to get all inputs and their visibility in one call
        const inputInfo = await frame.evaluate(() => {
          const inputs = Array.from(document.querySelectorAll('input'));
          return inputs.map(el => ({
            type: el.type,
            id: el.id,
            name: el.name,
            placeholder: el.placeholder,
            ariaLabel: el.getAttribute('aria-label'),
            ariaHidden: el.getAttribute('aria-hidden'),
            hidden: el.hidden,
            offsetWidth: el.offsetWidth,
            offsetHeight: el.offsetHeight,
            className: el.className.slice(0, 50),
          }));
        }).catch(() => []);

        if (inputInfo.length === 0) continue;

        // Find visible inputs — must have dimensions and not be aria-hidden
        const visibleInputs = inputInfo.filter(i =>
          i.offsetWidth > 0 &&
          i.offsetHeight > 0 &&
          i.ariaHidden !== 'true' &&
          !i.hidden
        );

        console.log(`[ZoomBot] Frame ${frameUrl.slice(0, 60)}...`);
        console.log(`  Total inputs: ${inputInfo.length}, Visible: ${visibleInputs.length}`);
        visibleInputs.forEach(i => {
          console.log(`  Visible input -> type:${i.type} id:${i.id} name:${i.name} aria-label:${i.ariaLabel} placeholder:${i.placeholder}`);
        });

        if (visibleInputs.length > 0) {
          console.log('[ZoomBot] Found frame with visible input(s) — using this frame');
          return { frame, visibleInputs };
        }
      } catch {
        // frame detached or inaccessible, skip
      }
    }

    console.log('[ZoomBot] No frame with visible inputs found — falling back to main page');
    return { frame: this.page, visibleInputs: [] };
  }

  async join() {
    console.log('[ZoomBot] Launching browser...');
    this.context = await BrowserManager.launch('zoom');
    this.page = await this.context.newPage();

    const directUrl = ZoomBot.buildDirectWebClientUrl(this.session.meetingUrl);
    console.log('[ZoomBot] Navigating directly to web client...');
    await this.page.goto(directUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await this.page.waitForTimeout(4000);
    await this.page.screenshot({ path: 'zoom-step1.png' });
    console.log('[ZoomBot] Step 1 loaded. URL:', this.page.url());

    // Handle interstitial if we landed on it instead of directly on join page
    const browserButton = this.page.locator(ZOOM_SELECTORS.joinFromBrowserButton);
    if (await browserButton.isVisible().catch(() => false)) {
      console.log('[ZoomBot] Interstitial detected — clicking "Join from browser"...');
      await browserButton.click();
      await this.page.waitForTimeout(8000);
      await this.page.screenshot({ path: 'zoom-step2.png' });
      console.log('[ZoomBot] Step 2 done. URL:', this.page.url());
    } else {
      console.log('[ZoomBot] No interstitial — already on join page');
      await this.page.screenshot({ path: 'zoom-step2-direct.png' });
    }

    await this.page.waitForTimeout(3000);

    // Find the frame with the actual visible name input and log its attributes
    // so we know the exact selector to target
    const { frame: formFrame, visibleInputs } = await this.findFrameWithVisibleInput();

    // Build the most specific selector possible from what we found
    let filled = false;
    for (const inputInfo of visibleInputs) {
      let selector = null;

      if (inputInfo.id) selector = `#${inputInfo.id}`;
      else if (inputInfo.ariaLabel) selector = `input[aria-label="${inputInfo.ariaLabel}"]`;
      else if (inputInfo.name) selector = `input[name="${inputInfo.name}"]`;
      else if (inputInfo.placeholder) selector = `input[placeholder="${inputInfo.placeholder}"]`;

      if (!selector) continue;

      console.log(`[ZoomBot] Trying selector: ${selector}`);
      const el = formFrame.locator(selector);
      const visible = await el.isVisible().catch(() => false);
      console.log(`[ZoomBot] Selector visible: ${visible}`);

      if (visible) {
        await el.click();
        await el.fill('Meeting Recorder Bot');
        const value = await el.inputValue();
        console.log('[ZoomBot] Name filled successfully. Value:', value);
        filled = true;
        break;
      }
    }

    if (!filled) {
      console.log('[ZoomBot] Could not fill name field — check zoom-step2.png for what is on screen');
    }

    await this.page.screenshot({ path: 'zoom-step3.png' });
    console.log('[ZoomBot] Step 3 saved. Clicking Join...');

    // Click Join in the correct frame context
    await formFrame.locator(ZOOM_SELECTORS.joinButton).click({ timeout: 30000 });
    console.log('[ZoomBot] Clicked Join');

    await this.page.waitForTimeout(4000);
    await this.page.screenshot({ path: 'zoom-step4.png' });
    console.log('[ZoomBot] Step 4 saved after clicking Join');
  }

  async waitForAdmission(timeoutMs = 120000) {
    console.log('[ZoomBot] Waiting to be admitted...');
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      if (await isAdmitted(this.page)) {
        console.log('[ZoomBot] Admitted into the meeting');
        return true;
      }
      await this.page.waitForTimeout(2000);
    }
    await this.page.screenshot({ path: 'zoom-admission-timeout.png' });
    throw new Error('Not admitted to the Zoom meeting within the timeout window');
  }

  async isStillInMeeting() {
    try {
      if (this.page.isClosed()) return false;

      const ended = await hasMeetingEnded(this.page);
      if (ended) {
        console.log('[ZoomBot] End-of-call text detected.');
        return false;
      }

      const admitted = await isAdmitted(this.page);
      if (!admitted) {
        console.log('[ZoomBot] In-call toolbar gone. Meeting ended.');
        return false;
      }

      return true;
    } catch (error) {
      console.log('[ZoomBot] Page context lost, assuming meeting ended:', error.message);
      return false;
    }
  }

  async leave() {
    console.log('[ZoomBot] leave() triggered. Closing Chromium...');
    if (this.context) {
      await this.context.close().catch(() => {});
      console.log('[ZoomBot] Chromium successfully closed.');
    }
  }
}