// Maps every backend Meeting.status value to a display label and a
// semantic "tone" (done | processing | failed | muted). Multiple backend
// statuses share the "processing" tone since they're all just different
// stages of the same in-flight state to the user.
const STATUS_META = {
  scheduled: { label: 'Scheduled', tone: 'muted' },
  // Phase C2: the bot was busy, so this meeting is holding for a free
  // recorder. Deliberately not labelled "Joining" - nothing is joining yet,
  // and the honest label is what stops a support question from starting with
  // "it says joining but it isn't". The "processing" tone is load-bearing as
  // well as cosmetic: Dashboard.jsx polls while any meeting has that tone, so
  // a queued meeting refreshes itself into "Joining" once a recorder frees up.
  queued: { label: 'Waiting for a recorder', tone: 'processing' },
  joining: { label: 'Joining', tone: 'processing' },
  waiting_for_admission: { label: 'Waiting for admission', tone: 'processing' },
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
