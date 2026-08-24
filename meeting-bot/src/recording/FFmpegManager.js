import { spawn } from 'child_process';
import { getCaptureArgs } from './AudioCapture.js';

export class FFmpegManager {
  constructor(outputPath, monitorSource) {
    this.outputPath = outputPath;
    this.monitorSource = monitorSource;
    this.process = null;
  }

  start() {
    const args = [
      // Platform-specific audio capture args (Windows: dshow/VB-Cable, Linux: pulse)
      // getCaptureArgs() is defined in AudioCapture.js — this is the correct
      // cross-platform call that was previously bypassed by hardcoded args
      ...getCaptureArgs(this.monitorSource),

      // Audio encoding settings
      '-c:a', 'aac',
      '-ac', '2',       // force stereo
      '-ar', '44100',   // force 44.1kHz sample rate
      '-b:a', '192k',   // solid bitrate for clear voices

      // Drop video completely
      '-vn',

      '-y',
      this.outputPath,
    ];

    console.log('[FFmpegManager] Starting with args:', args.join(' '));

    this.process = spawn('ffmpeg', args, {
      // stdin MUST be 'pipe' for the graceful 'q' stop signal to reach ffmpeg.
      // Without this, on Windows especially, stdin is disconnected and the
      // 'q' command never arrives — ffmpeg gets killed abruptly and the
      // m4a container is never finalized, producing a corrupted file.
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    this.process.stderr.on('data', (data) => {
      // ffmpeg logs progress to stderr — uncomment for debugging:
      // console.log('[FFmpegManager]', data.toString());
    });

    this.process.on('error', (err) => {
      console.error('[FFmpegManager] Failed to start ffmpeg:', err.message);
    });

    return this.process;
  }

  stop() {
    return new Promise((resolve, reject) => {
      if (!this.process) return resolve();

      let closed = false;

      this.process.on('close', (code) => {
        closed = true;
        console.log(`[FFmpegManager] ffmpeg closed with code ${code}`);
        if (code === 0 || code === 255) resolve(); // 255 = normal 'q' quit
        else reject(new Error(`ffmpeg exited with code ${code}`));
      });

      // Step 1: send graceful quit signal
      try {
        this.process.stdin.write('q');
        this.process.stdin.end();
      } catch (err) {
        console.error('[FFmpegManager] Could not write q to stdin:', err.message);
      }

      // Step 2: if ffmpeg hasn't closed within 8s, send SIGINT as fallback.
      // SIGINT (Ctrl+C equivalent) still lets ffmpeg finalize the file
      // on most platforms — unlike SIGKILL which truncates it.
      setTimeout(() => {
        if (!closed) {
          console.log('[FFmpegManager] Graceful stop timed out — sending SIGINT');
          this.process.kill('SIGINT');
          
          setTimeout(() => {
            if (!closed) {
              console.log('[FFmpegManager] SIGINT timed out — sending SIGKILL');
              this.process.kill('SIGKILL');
            }
          }, 3000);
        }
      }, 8000);
    });
  }
}