const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API error ${res.status}: ${body}`);
  }
  return res.json();
}

export const api = {
  listMeetings: () => request('/meetings'),
  getMeeting: (id) => request(`/meetings/${id}`),
  createMeeting: (meeting_url) =>
    request('/meetings', {
      method: 'POST',
      body: JSON.stringify({ meeting_url }),
    }),
};
