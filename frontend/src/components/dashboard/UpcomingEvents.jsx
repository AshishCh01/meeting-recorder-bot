import React, { useEffect, useState } from 'react';
import { Loader2, CalendarClock, CheckCircle2 } from 'lucide-react';
import api from '../../lib/api';
import { formatPlatform } from '../../lib/format';

// event.starts_at is either an ISO datetime (timed event) or a plain
// "YYYY-MM-DD" date (all-day event, which POST /calendar/events/{id}/schedule
// rejects - shown here for visibility, not made actionable).
function formatEventTime(startsAt) {
  if (!startsAt) return '';
  if (!startsAt.includes('T')) {
    return new Date(`${startsAt}T00:00:00`).toLocaleDateString([], { month: 'short', day: 'numeric' });
  }
  return new Date(startsAt).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export const UpcomingEvents = ({ onScheduled }) => {
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [events, setEvents] = useState([]);
  const [error, setError] = useState(null);
  const [pendingId, setPendingId] = useState(null);

  const fetchEvents = async () => {
    setError(null);
    try {
      const { data: status } = await api.get('/calendar/status');
      if (!status.connected) {
        setConnected(false);
        return;
      }
      setConnected(true);
      const { data } = await api.get('/calendar/events');
      setEvents(data);
    } catch (err) {
      console.error('Failed to load calendar events', err);
      setError('Failed to load your calendar events.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchEvents();
  }, []);

  const handleToggle = async (event) => {
    setPendingId(event.id);
    try {
      if (event.already_scheduled) {
        await api.delete(`/meetings/${event.meeting_id}`);
      } else {
        await api.post(`/calendar/events/${event.id}/schedule`);
      }
      await fetchEvents();
      onScheduled?.();
    } catch (err) {
      console.error('Failed to update this event', err);
      alert(err.response?.data?.detail || 'Failed to update this event.');
    } finally {
      setPendingId(null);
    }
  };

  // Connect Google Calendar in Settings to make this section appear -
  // nothing to show (and no point polling) for a user who hasn't.
  if (!loading && !connected) return null;

  return (
    <div className="mb-6 bg-surface border border-border-strong rounded-2xl p-5 flex flex-col gap-3">
      <div className="flex items-center gap-2.5">
        <CalendarClock className="w-4.5 h-4.5 text-brand-blue" />
        <h2 className="text-[15px] font-bold text-brand-dark">Upcoming from your calendar</h2>
        {loading && <Loader2 className="w-4 h-4 animate-spin text-faint" />}
      </div>

      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      {!loading && !error && events.length === 0 && (
        <p className="text-sm text-muted">No upcoming events with a Google Meet or Zoom link.</p>
      )}

      {events.length > 0 && (
        <div className="flex flex-col gap-2">
          {events.map((event) => {
            const hasLink = Boolean(event.meeting_url);
            const pending = pendingId === event.id;
            return (
              <div
                key={event.id}
                className={`flex items-center gap-3.5 p-3 rounded-xl border ${
                  hasLink ? 'border-line' : 'border-dashed border-line opacity-60'
                }`}
              >
                <div className="flex-1 min-w-0">
                  <div className="text-[14px] font-semibold text-brand-dark truncate">{event.title}</div>
                  <div className="text-xs text-muted truncate">
                    {formatEventTime(event.starts_at)}
                    {hasLink ? ` · ${formatPlatform(event.platform)}` : ' · No video link'}
                  </div>
                </div>
                {hasLink && (
                  <button
                    onClick={() => handleToggle(event)}
                    disabled={pending}
                    title={event.already_scheduled ? 'Cancel recording' : 'Record this meeting'}
                    className={`flex-none px-3 py-1.5 rounded-lg text-[13px] font-semibold transition-colors disabled:opacity-50 flex items-center gap-1.5 ${
                      event.already_scheduled
                        ? 'border border-border text-status-done-fg hover:text-red-600 dark:hover:text-red-400 hover:border-red-200 dark:hover:border-red-500/30'
                        : 'bg-brand-blue text-white hover:opacity-90'
                    }`}
                  >
                    {pending ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : event.already_scheduled ? (
                      <>
                        <CheckCircle2 className="w-3.5 h-3.5" /> Scheduled
                      </>
                    ) : (
                      'Record'
                    )}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
