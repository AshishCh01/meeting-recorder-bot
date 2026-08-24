import { execFileSync } from 'child_process';
import os from 'os';

// Per-session PulseAudio null-sink (phase 2 of 2, audit finding C-1).
// docker-entrypoint.sh creates one system-wide default sink ("RecordingSink")
// at container boot; every browser and every ffmpeg process used to share
// it, so concurrent meetings' audio blended together in every recording.
// provision() instead creates a dedicated sink per meetingId — the caller
// points that session's Chrome process at it via BrowserManager's
// pulseSink option, and points that session's ffmpeg at its .monitor via
// Recorder/FFmpegManager, so each session's audio stays isolated end to end.
//
// Linux only. Windows dev (VB-Audio Virtual Cable) has no equivalent
// per-session mechanism with the free single cable pair, so provision() is
// a no-op there and Windows-dev sessions keep sharing the one hardcoded
// VB-Cable device (see AudioCapture.js) — leave MAX_CONCURRENT_MEETINGS=1
// on Windows dev.
export class AudioSink {
  static provision(meetingId) {
    if (os.platform() !== 'linux') {
      return { sinkName: null, monitorSource: null, release: () => {} };
    }

    const sinkName = `rec_${meetingId.replace(/-/g, '_')}`;
    let moduleId;
    try {
      moduleId = execFileSync('pactl', [
        'load-module', 'module-null-sink',
        `sink_name=${sinkName}`,
        `sink_properties=device.description=${sinkName}`,
      ]).toString().trim();
    } catch (err) {
      throw new Error(`Failed to provision audio sink ${sinkName}: ${err.message}`);
    }

    return {
      sinkName,
      monitorSource: `${sinkName}.monitor`,
      release: () => {
        try {
          execFileSync('pactl', ['unload-module', moduleId]);
        } catch (err) {
          console.error(`[AudioSink] Failed to release sink ${sinkName} (module ${moduleId}):`, err.message);
        }
      },
    };
  }
}
