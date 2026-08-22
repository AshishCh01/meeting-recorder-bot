// Maps every backend Meeting.status value to a display label and a
// semantic "tone" (done | processing | failed | muted). Multiple backend
// statuses share the "processing" tone since they're all just different
// stages of the same in-flight state to the user.
const STATUS_META = {
  scheduled: { label: 'Scheduled', tone: 'muted' },
  joining: { label: 'Joining', tone: 'processing' },
  recording: { label: 'Recording', tone: 'processing' },
  uploading: { label: 'Uploading', tone: 'processing' },
  transcribing: { label: 'Transcribing', tone: 'processing' },
  completed: { label: 'Completed', tone: 'done' },
  failed: { label: 'Failed', tone: 'failed' },
};

export const STATUS_TONES = ['done', 'processing', 'failed', 'muted'];

export function getStatusMeta(status) {
  return STATUS_META[status] || { label: status || 'Unknown', tone: 'muted' };
}
