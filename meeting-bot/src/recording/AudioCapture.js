// Returns the ffmpeg input arguments for capturing audio only, via
// PulseAudio (Docker, Linux — Xvfb + PulseAudio).
//
// monitorSource: the per-session PulseAudio monitor from AudioSink.provision()
// (e.g. "rec_<meetingId>.monitor"). Falls back to the container-wide default
// sink's monitor when omitted, for any caller not yet threading a per-session
// sink through.
export function getCaptureArgs(monitorSource) {
  return [
    '-f', 'pulse',
    '-i', monitorSource || 'RecordingSink.monitor',
  ];
}