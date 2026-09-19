import { supabase } from './supabase';

// Posts to one of the backend's server-sent-event chat routes and hands each
// event to `onEvent` as it arrives. Shared by the meeting chat and Ask AI, so
// both read the stream the same way.
//
// Throws on a non-2xx status - ownership, readiness and rate-limit failures
// arrive as normal status codes, because the backend validates before the
// stream starts - and on an `error` event, with the backend's message in
// both cases.
export async function readChatStream(path, body, { signal, onEvent }) {
  const { data: { session } } = await supabase.auth.getSession();
  const response = await fetch(`${import.meta.env.VITE_API_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok || !response.body) {
    const detail = await response.json().catch(() => null);
    const error = new Error(detail?.detail || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  // Minimal SSE reader: frames are separated by a blank line, and a frame
  // can straddle two network chunks, so hold the remainder in `buffer`.
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';

    for (const frame of frames) {
      const line = frame.split('\n').find(l => l.startsWith('data:'));
      if (!line) continue;

      let event;
      try {
        event = JSON.parse(line.slice(5).trim());
      } catch {
        continue;
      }

      if (event.type === 'error') throw new Error(event.message);
      onEvent(event);
    }
  }
}
