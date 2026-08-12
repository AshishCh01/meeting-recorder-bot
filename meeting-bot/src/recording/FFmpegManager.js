import { spawn } from 'child_process';
import { getCaptureArgs } from './AudioCapture.js';

export class FFmpegManager {
  constructor(outputPath) {
    this.outputPath = outputPath;
    this.process = null;
  }

  start() {
    const args = [
      ...getCaptureArgs(),
      '-c:v', 'libx264',
      '-preset', 'ultrafast',
      '-c:a', 'aac',
      '-y',
      this.outputPath,
    ];

    this.process = spawn('ffmpeg', args);
    this.process.stderr.on('data', () => {
      // ffmpeg logs progress to stderr by default; uncomment for debugging:
      // console.log(data.toString());
    });

    return this.process;
  }

  stop() {
    return new Promise((resolve, reject) => {
      if (!this.process) return resolve();

      this.process.on('close', (code) => {
        if (code === 0 || code === 255) resolve(); // 255 = normal 'q' quit
        else reject(new Error(`ffmpeg exited with code ${code}`));
      });

      // Graceful stop so the mp4 container is finalized correctly
      this.process.stdin.write('q');
    });
  }
}
