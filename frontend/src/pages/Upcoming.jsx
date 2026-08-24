import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, CalendarClock, CheckCircle2 } from 'lucide-react';
import { Layout } from '../components/Layout';
import api from '../lib/api';
import { formatPlatform } from '../lib/format';

// event.starts_at is either an ISO datetime (timed event) or a plain
// "YYYY-MM-DD" date (all-day event, which POST /calendar/events/{id}/schedule
// rejects - shown here for visibility, not made actionable).
function formatEventTime(startsAt) {
  if (!startsAt) return '';
  if (!startsAt.includes('T')) {
    return new Date(`${startsAt}T00:00:00`).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
  }
  return new Date(startsAt).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export const Upcoming = () => {
  const [status, setStatus] = useState({ connected: false, google_email: null });
  const [statusLoading, setStatusLoading] = useState(true);
  const [events, setEvents] = useState([]);
  const [eventsLoading, setEventsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [pendingId, setPendingId] = useState(null);

  const fetchAll = async () => {
    setError(null);
    try {
      const { data: calStatus } = await api.get('/calendar/status');
      setStatus(calStatus);
      setStatusLoading(false);
      if (!calStatus.connected) {
        setEventsLoading(false);
        return;
      }
      setEventsLoading(true);
      const { data } = await api.get('/calendar/events');
      setEvents(data);
    } catch (err) {
      console.error('Failed to load calendar events', err);
      setError('Failed to load your calendar events.');
    } finally {
      setEventsLoading(false);
    }
  };

  useEffect(() => {
    fetchAll();
  }, []);

  const handleToggle = async (event) => {
    setPendingId(event.id);
    try {
      if (event.already_scheduled) {
        await api.delete(`/meetings/${event.meeting_id}`);
      } else {
        await api.post(`/calendar/events/${event.id}/schedule`);
      }
      await fetchAll();
    } catch (err) {
      console.error('Failed to update this event', err);
      alert(err.response?.data?.detail || 'Failed to update this event.');
    } finally {
      setPendingId(null);
    }
  };

  return (
    <Layout>
      <div className="mb-6">
        <h1 className="text-2xl font-extrabold text-brand-dark tracking-tight">Upcoming</h1>
        <p className="text-sm text-muted mt-1">
          {statusLoading
            ? 'Checking calendar connection…'
            : status.connected
              ? `Synced from ${status.google_email}`
              : 'Connect your Google Calendar to see upcoming meetings here.'}
        </p>
      </div>

      {!statusLoading && !status.connected && (
        <div className="flex flex-col items-center justify-center text-center gap-5 px-6 py-20">
          <div className="w-14 h-14 rounded-2xl bg-status-done-bg flex items-center justify-center">
            <CalendarClock className="h-7 w-7 text-status-done-fg" />
          </div>
          <div className="max-w-md">
            <h2 className="text-2xl font-extrabold text-brand-dark tracking-tight">No calendar connected</h2>
            <p className="mt-3 text-[15px] leading-relaxed text-body">
              Connect Google Calendar in Settings, then opt individual events into recording -
              MeetIQ joins automatically a couple of minutes before each one starts.
            </p>
          </div>
          <Link
            to="/settings"
            className="px-6 py-3 rounded-xl bg-linear-to-br from-brand-blue to-brand-blue-light text-white font-bold text-sm shadow-sm hover:opacity-90 transition-opacity"
          >
            Go to Settings
          </Link>
        </div>
      )}

      {status.connected && (
        <>
          {error && <p className="text-sm text-red-600 dark:text-red-400 mb-4">{error}</p>}

          {eventsLoading ? (
            <div className="flex h-64 items-center justify-center">
              <Loader2 className="w-8 h-8 animate-spin text-brand-blue" />
            </div>
          ) : events.length === 0 ? (
            <div className="px-6 py-16 text-center text-sm text-muted">
              No upcoming events with a Google Meet or Zoom link.
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {events.map((event) => {
                const hasLink = Boolean(event.meeting_url);
                const pending = pendingId === event.id;
                return (
                  <div
                    key={event.id}
                    className={`flex items-center gap-3.5 p-4 rounded-xl border ${
                      hasLink ? 'border-line' : 'border-dashed border-line opacity-60'
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-[15px] font-semibold text-brand-dark truncate">{event.title}</div>
                      <div className="text-xs text-muted truncate mt-0.5">
                        {formatEventTime(event.starts_at)}
                        {hasLink ? ` · ${formatPlatform(event.platform)}` : ' · No video link'}
                      </div>
                    </div>
                    {hasLink && (
                      <button
                        onClick={() => handleToggle(event)}
                        disabled={pending}
                        title={event.already_scheduled ? 'Cancel recording' : 'Record this meeting'}
                        className={`flex-none px-3.5 py-2 rounded-lg text-[13px] font-semibold transition-colors disabled:opacity-50 flex items-center gap-1.5 ${
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
        </>
      )}
    </Layout>
  );
};
