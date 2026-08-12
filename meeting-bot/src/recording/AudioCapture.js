import os from 'os';

// Returns the ffmpeg input arguments for capturing screen + audio.
// Linux (production, inside Docker with Xvfb + PulseAudio) is the primary
// target. Windows args are kept for local dev only (see README) — they
// assume VB-Audio Virtual Cable is installed and selected as the bot
// Chrome window's output device.
export function getCaptureArgs() {
  const platform = os.platform();

  if (platform === 'linux') {
    const display = process.env.DISPLAY || ':99';
    return [
      '-f', 'x11grab',
      '-video_size', '1280x720',
      '-i', display,
      '-f', 'pulse',
      '-i', 'RecordingSink.monitor',
    ];
  }

  if (platform === 'win32') {
    return [
      '-f', 'gdigrab',
      '-framerate', '15',
      '-i', 'desktop',
      '-f', 'dshow',
      '-i', 'audio=CABLE Output (VB-Audio Virtual Cable)',
    ];
  }

  throw new Error(`No capture configuration defined for platform: ${platform}`);
}
