import { spawn } from 'child_process';
// We will leave this import here so we don't break dependencies, 
// but we will not use it in the args array below.
import { getCaptureArgs } from './AudioCapture.js';

export class FFmpegManager {
  constructor(outputPath) {
    this.outputPath = outputPath;
    this.process = null;
  }

  start() {
    // Replaced dynamic args with hardcoded Windows video-only capture
    const args = [
      '-f', 'gdigrab',
      '-framerate', '15',
      '-i', 'desktop',
      '-c:v', 'libx264',
      '-preset', 'ultrafast',
      // Removed the '-c:a', 'aac' audio flags completely
      '-y',
      this.outputPath,
    ];

    this.process = spawn('ffmpeg', args);
    this.process.stderr.on('data', (data) => {
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

