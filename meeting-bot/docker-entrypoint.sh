#!/bin/bash
# meeting-bot/docker-entrypoint.sh
#
# Boots the two pieces of OS-level infrastructure the bot depends on
# before handing off to node:
#   1. Xvfb   - virtual display for headed Chrome (BrowserManager.js)
#   2. Pulse  - virtual audio sink "RecordingSink" (AudioCapture.js
#               reads from RecordingSink.monitor via ffmpeg)
set -e

echo "[entrypoint] Cleaning up stale lock files from previous runs..."
rm -f /tmp/.X99-lock
rm -rf /tmp/pulse-* "$HOME/.config/pulse"

echo "[entrypoint] starting Xvfb on :99"
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
export DISPLAY=:99
sleep 1

echo "[entrypoint] starting PulseAudio"
pulseaudio -D --exit-idle-time=-1 --disallow-exit --disallow-module-loading=no
sleep 1

echo "[entrypoint] creating RecordingSink"
pactl load-module module-null-sink sink_name=RecordingSink sink_properties=device.description=RecordingSink
pactl set-default-sink RecordingSink

echo "[entrypoint] handing off to: $*"
exec "$@"