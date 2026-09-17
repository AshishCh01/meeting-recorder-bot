FROM node:22-bookworm-slim

# Install system dependencies: Xvfb, PulseAudio, FFmpeg, and fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates \
        xvfb pulseaudio dbus-x11 ffmpeg \
        fonts-liberation fonts-noto-color-emoji fonts-noto-cjk \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# PulseAudio's user-mode daemon has a hard refusal to start as root
# (unlike Chrome, which we handle with --no-sandbox, there's no flag
# to make PulseAudio tolerate root — it just exits). Everything else
# here is fine as root; audio specifically needs a real user.
RUN useradd --create-home --shell /bin/bash botuser

WORKDIR /app

# Copy package configurations and install Node modules.
#
# npm ci, not npm install: package-lock.json is committed and this is the
# image that ships, so the build must install the resolved tree rather than
# re-resolve ranges at build time and quietly ship a version nobody tested.
# CI already runs `npm ci` for the same reason - this makes the deployed
# image and the tested tree the same tree.
#
# PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD is a guard, not a fix: the pinned playwright
# ships no postinstall script, so today this install downloads no browsers and
# the flag changes nothing - measured, not assumed (the image has no
# ms-playwright cache with or without it). It is here because the bot never
# wants them if a future version brings that postinstall back: BrowserManager.js
# opens Chrome with `channel: 'chrome'`, which resolves to the
# google-chrome-stable installed from apt above, and Playwright's own bundled
# Chromium would be ~400MB of layer nothing launches. Scoped to this RUN rather
# than a file-level ENV so it cannot change behaviour for anything that later
# shells out to playwright inside the running container.
COPY package*.json ./
RUN PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci

# Install Playwright OS-level dependencies for Chromium. Still needed with the
# browser download skipped: this installs the shared libraries Chrome itself
# links against, not the bundled browser binaries.
RUN npx playwright install-deps chromium

# Copy application source code
COPY . .

# Ensure browser profile and recordings directories exist, and that
# botuser (not root) owns everything it needs to read/write.
RUN mkdir -p browser-profiles recordings \
    && chown -R botuser:botuser /app

# Configure entrypoint script permissions. The sed strips any \r
# (Windows CRLF line endings) that may have crept in when this file
# was saved/edited on Windows — a script whose shebang line reads
# "#!/bin/bash\r" fails with a confusing "no such file" error, since
# Linux looks for a literal interpreter named /bin/bash\r.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

ENV NODE_ENV=production
EXPOSE 3000

USER botuser

# Execute the entrypoint script, then start the Node application
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["npm", "start"]