function startOfDay(date) {
  const d = new Date(date);
  d.setHours(0, 0, 0, 0);
  return d;
}

// Buckets meetings into Today / Yesterday / This week / Earlier by
// created_at. Assumes the input is already sorted newest-first (as
// GET /meetings returns it) - buckets are emitted in that same order
// and only included if non-empty.
export function groupMeetingsByDate(meetings) {
  const today = startOfDay(new Date());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const weekStart = new Date(today);
  weekStart.setDate(weekStart.getDate() - 6);

  const buckets = { Today: [], Yesterday: [], 'This week': [], Earlier: [] };

  for (const meeting of meetings) {
    if (!meeting.created_at) {
      buckets.Earlier.push(meeting);
      continue;
    }
    const day = startOfDay(new Date(meeting.created_at));
    if (day.getTime() === today.getTime()) {
      buckets.Today.push(meeting);
    } else if (day.getTime() === yesterday.getTime()) {
      buckets.Yesterday.push(meeting);
    } else if (day.getTime() > weekStart.getTime()) {
      buckets['This week'].push(meeting);
    } else {
      buckets.Earlier.push(meeting);
    }
  }

  return Object.entries(buckets)
    .filter(([, items]) => items.length > 0)
    .map(([label, items]) => ({ label, items }));
}
