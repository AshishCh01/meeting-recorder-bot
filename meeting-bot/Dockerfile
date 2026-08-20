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

WORKDIR /app

# Copy package configurations and install Node modules
COPY package*.json ./
RUN npm install

# Install Playwright OS-level dependencies for Chromium
RUN npx playwright install-deps chromium

# Copy application source code
COPY . .

# Ensure browser profile directory exists
RUN mkdir -p browser-profiles

# Configure entrypoint script permissions
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV NODE_ENV=production
EXPOSE 3000

# Execute the entrypoint script, then start the Node application
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["npm", "start"]