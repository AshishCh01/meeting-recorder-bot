import { exec } from 'child_process';
import { promisify } from 'util';
import os from 'os';
import path from 'path';
import { fileURLToPath } from 'url';

const execAsync = promisify(exec);
const __dirname = path.dirname(fileURLToPath(import.meta.url));

const NIRCMD = path.resolve(__dirname, '..', '..', 'nircmd.exe');
const SVV = path.resolve(__dirname, '..', '..', 'SoundVolumeView.exe');

const CABLE_INPUT_DEVICE = 'CABLE Input (VB-Audio Virtual Cable)';
const PLAYBACK = 1;
const COMMUNICATION = 2;

export class AudioRouter {
  static _previousDevice = null;

  static isWindows() {
    return os.platform() === 'win32';
  }

  static async getCurrentDevice() {
    if (!AudioRouter.isWindows()) return null;
    try {
      const { stdout } = await execAsync(
        'powershell -Command "Get-ItemProperty -Path \'HKCU:\\SOFTWARE\\Microsoft\\Multimedia\\Sound Mapper\' -Name \'Playback\' | Select-Object -ExpandProperty Playback"',
        { timeout: 5000 }
      );
      const device = stdout.trim();
      console.log(`[AudioRouter] Current default device: "${device}"`);
      return device || null;
    } catch {
      console.log('[AudioRouter] Could not read current device from registry — will use fallback restore');
      return null;
    }
  }

  static async routeToCable() {
    if (!AudioRouter.isWindows()) {
      console.log('[AudioRouter] Not Windows — skipping (Linux uses PulseAudio automatically)');
      return;
    }

    try {
      AudioRouter._previousDevice = await AudioRouter.getCurrentDevice();

      // Step 1: set system default so any new app that opens uses CABLE
      await execAsync(`"${NIRCMD}" setdefaultsounddevice "${CABLE_INPUT_DEVICE}" ${PLAYBACK}`);
      await execAsync(`"${NIRCMD}" setdefaultsounddevice "${CABLE_INPUT_DEVICE}" ${COMMUNICATION}`);
      console.log(`[AudioRouter] ✓ System default routed to: "${CABLE_INPUT_DEVICE}"`);

      // Step 2: set Chrome's app-specific audio device using SoundVolumeView.
      // Chrome persistent profiles cache their last-used audio device and
      // ignore system default changes — SoundVolumeView bypasses this by
      // setting the device at the per-app level, overriding Chrome's cache.
      // This is what makes manual Volume Mixer routing unnecessary.
      await execAsync(`"${SVV}" /SetAppDefault "${CABLE_INPUT_DEVICE}" all chrome.exe`);
      console.log('[AudioRouter] ✓ Chrome app-level audio routed to CABLE Input');
      console.log('[AudioRouter] No manual Volume Mixer step needed');
    } catch (err) {
      console.error('[AudioRouter] Failed to route audio:', err.message);
      console.error('[AudioRouter] Make sure both nircmd.exe and SoundVolumeView.exe are in meeting-bot/');
    }
  }

  static async routeBack() {
    if (!AudioRouter.isWindows()) return;

    const restoreDevice = AudioRouter._previousDevice || 'Speakers';

    try {
      // Restore system default
      await execAsync(`"${NIRCMD}" setdefaultsounddevice "${restoreDevice}" ${PLAYBACK}`);
      await execAsync(`"${NIRCMD}" setdefaultsounddevice "${restoreDevice}" ${COMMUNICATION}`);
      console.log(`[AudioRouter] ✓ System default restored to: "${restoreDevice}"`);

      // Restore Chrome's app-level device too
      await execAsync(`"${SVV}" /SetAppDefault "${restoreDevice}" all chrome.exe`);
      console.log(`[AudioRouter] ✓ Chrome app-level audio restored to: "${restoreDevice}"`);
    } catch (err) {
      console.error('[AudioRouter] Failed to restore audio device:', err.message);
      console.log(`[AudioRouter] Manually restore: nircmd setdefaultsounddevice "${restoreDevice}" 1`);
    } finally {
      AudioRouter._previousDevice = null;
    }
  }
}