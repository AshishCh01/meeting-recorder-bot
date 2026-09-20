// Short names on purpose. These sit in a metadata column next to a duration
// and a timestamp, and "Google Meet" against "Zoom" left that column visibly
// ragged from row to row. "Meet" and "Zoom" are the same width, so the
// columns line up and the labels are still unambiguous.
const PLATFORM_LABELS = {
  google: 'Meet',
  zoom: 'Zoom',
  teams: 'Teams',
};

export function formatPlatform(platform) {
  return PLATFORM_LABELS[platform] || (platform ? platform : 'Unknown');
}

export function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return '—';
  const totalMinutes = Math.round(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes} min`;
}

function isSameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

// "14:00" for today/yesterday, "Wed 15:40" for anything older - matches
// the design reference's grouped-list time format.
export function formatMeetingTime(dateString) {
  if (!dateString) return '';
  const date = new Date(dateString);
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);

  const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  if (isSameDay(date, now) || isSameDay(date, yesterday)) return time;

  const weekday = date.toLocaleDateString([], { weekday: 'short' });
  return `${weekday} ${time}`;
}

// Elapsed/total time for the audio player, e.g. 65 -> "1:05", 3725 -> "1:02:05".
export function formatClockTime(seconds) {
  if (!seconds || seconds < 0 || !Number.isFinite(seconds)) return '0:00';
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  return `${minutes}:${String(secs).padStart(2, '0')}`;
}
