import { execFile } from 'child_process';
import { promisify } from 'util';
import os from 'os';

const execFileAsync = promisify(execFile);

// Per-session PulseAudio null-sink (phase 2 of 2, audit finding C-1).
// docker-entrypoint.sh creates one system-wide default sink ("RecordingSink")
// at container boot; every browser and every ffmpeg process used to share
// it, so concurrent meetings' audio blended together in every recording.
// provision() instead creates a dedicated sink per meetingId — the caller
// points that session's Chrome process at it via BrowserManager's
// pulseSink option, and points that session's ffmpeg at its .monitor via
// Recorder/FFmpegManager, so each session's audio stays isolated end to end.
//
// provision()/release() are async because they shell out to `pactl`. They
// used to use execFileSync, which blocks Node's single-threaded event loop
// for the duration of the subprocess: harmless at MAX_CONCURRENT_MEETINGS=1,
// but with concurrency enabled it stalls every other live meeting's
// admission polling, in-call checks and HTTP handling each time a meeting
// starts or ends.
//
// Linux only — provision() is a no-op on any other platform.
export class AudioSink {
  static async provision(meetingId) {
    if (os.platform() !== 'linux') {
      return { sinkName: null, monitorSource: null, release: async () => {} };
    }

    const sinkName = `rec_${meetingId.replace(/-/g, '_')}`;
    let moduleId;
    try {
      const { stdout } = await execFileAsync('pactl', [
        'load-module', 'module-null-sink',
        `sink_name=${sinkName}`,
        `sink_properties=device.description=${sinkName}`,
      ]);
      moduleId = stdout.toString().trim();
    } catch (err) {
      throw new Error(`Failed to provision audio sink ${sinkName}: ${err.message}`);
    }

    return {
      sinkName,
      monitorSource: `${sinkName}.monitor`,
      release: async () => {
        try {
          await execFileAsync('pactl', ['unload-module', moduleId]);
        } catch (err) {
          console.error(`[AudioSink] Failed to release sink ${sinkName} (module ${moduleId}):`, err.message);
        }
      },
    };
  }
}
