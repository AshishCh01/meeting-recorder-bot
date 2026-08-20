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

# Copy package configurations and install Node modules
COPY package*.json ./
RUN npm install

# Install Playwright OS-level dependencies for Chromium
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