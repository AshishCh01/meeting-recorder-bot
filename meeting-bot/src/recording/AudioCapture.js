import os from 'os';

// Returns the ffmpeg input arguments for capturing audio only.
// Linux (production, inside Docker with Xvfb + PulseAudio) is the primary
// target. Windows args are kept for local dev only (see README) -- they
// assume VB-Audio Virtual Cable is installed and selected as the bot
// Chrome window's OUTPUT device in Windows' volume mixer.
export function getCaptureArgs() {
  const platform = os.platform();

  if (platform === 'linux') {
    return [
      '-f', 'pulse',
      '-i', 'RecordingSink.monitor',
    ];
  }

  if (platform === 'win32') {
    return [
      '-f', 'dshow',
      '-i', 'audio=CABLE Output (VB-Audio Virtual Cable)',
    ];
  }

  throw new Error(`No capture configuration defined for platform: ${platform}`);
}