#!/bin/bash
# meeting-bot/docker-entrypoint.sh

set -e

echo "[entrypoint] Cleaning up stale lock files from previous runs..."
rm -f /tmp/.X99-lock
rm -rf /var/run/pulse /var/lib/pulse /root/.config/pulse

echo "[entrypoint] starting Xvfb on :99"
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
export DISPLAY=:99

echo "[entrypoint] starting PulseAudio in system mode"
pulseaudio -D --system --disallow-exit --disallow-module-loading=no
sleep 1

echo "[entrypoint] creating RecordingSink"
pactl load-module module-null-sink sink_name=RecordingSink sink_properties=device.description=RecordingSink
pactl set-default-sink RecordingSink

echo "[entrypoint] handing off to: $*"
exec "$@"