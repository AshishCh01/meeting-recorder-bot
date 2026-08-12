

import { spawn } from 'child_process';
import { getCaptureArgs } from './AudioCapture.js';

export class FFmpegManager {
  constructor(outputPath) {
    this.outputPath = outputPath;
    this.process = null;
  }

  start() {
    const args = [
      // 1. Audio input from the Virtual Cable ONLY (No gdigrab/desktop)
      '-f', 'dshow',
      '-i', 'audio=CABLE Output (VB-Audio Virtual Cable)',
      
      // 2. Audio encoding settings
      '-c:a', 'aac', 
      '-ac', '2',        // Force Stereo
      '-ar', '44100',    // Force 44.1kHz sample rate
      '-b:a', '192k',    // Set a solid bitrate for clear voices
      
      // 3. Drop video completely just to be safe
      '-vn', 
      
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

      // Graceful stop so the m4a container is finalized correctly
      this.process.stdin.write('q');
    });
  }
}
