import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, CalendarClock, Lock } from 'lucide-react';
import { Layout } from '../components/Layout';
import api from '../lib/api';
import { formatPlatform } from '../lib/format';
import { useToast } from '../context/ToastContext';
import { useBilling } from '../context/BillingContext';
import { LoadingLabel, RowSkeleton } from '../components/Skeleton';

// event.starts_at is either an ISO datetime (timed event) or a plain
// "YYYY-MM-DD" date (all-day event, which POST /calendar/events/{id}/schedule
// rejects - shown here for visibility, not made actionable).
function formatEventTime(startsAt) {
  if (!startsAt) return '';
  if (!startsAt.includes('T')) return 'All day';
  return new Date(startsAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// Events come back ordered, so the day headings can be emitted in the order
// they are first seen rather than sorted again here. Today and Tomorrow are
// named; anything further out gets its date, because "Thu" alone is ambiguous
// once the list runs past a week.
function groupEventsByDay(events) {
  const dayKey = (value) => (value || '').slice(0, 10);
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

  const label = (key) => {
    if (key === iso(today)) return 'Today';
    if (key === iso(tomorrow)) return 'Tomorrow';
    if (!key) return 'Scheduled';
    return new Date(`${key}T00:00:00`).toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' });
  };

  const order = [];
  const byDay = new Map();
  for (const event of events) {
    const key = dayKey(event.starts_at);
    if (!byDay.has(key)) {
      byDay.set(key, []);
      order.push(key);
    }
    byDay.get(key).push(event);
  }
  return order.map((key) => ({ label: label(key), items: byDay.get(key) }));
}

/**
 * The per-event recording switch. Recording is off until the user turns it on,
 * and a switch is what makes that state readable at a glance across a whole
 * day - a button labelled "Record" says what will happen, not what is
 * currently true.
 */
const RecordSwitch = ({ on, pending, disabled, onChange, label }) => (
  <button
    type="button"
    role="switch"
    aria-checked={on}
    aria-label={label}
    title={label}
    disabled={pending || disabled}
    onClick={onChange}
    className={`relative h-6 w-11 flex-none rounded-full transition-colors disabled:opacity-50 ${
      on ? 'btn-primary' : 'bg-tint-3'
    }`}
  >
    <span
      className={`absolute top-[3px] grid h-4.5 w-4.5 place-items-center rounded-full bg-white shadow transition-transform ${
        on ? 'translate-x-[23px]' : 'translate-x-[3px]'
      }`}
    >
      {pending && <Loader2 className="h-3 w-3 animate-spin text-brand-blue" />}
    </span>
  </button>
);

export const Upcoming = () => {
  const { toast } = useToast();
  const { can, planName, loading: billingLoading } = useBilling();
  const canSchedule = can('calendar_scheduling');
  const [status, setStatus] = useState({ connected: false, google_email: null });
  const [statusLoading, setStatusLoading] = useState(true);
  const [events, setEvents] = useState([]);
  const [eventsLoading, setEventsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [pendingId, setPendingId] = useState(null);

  const fetchAll = async () => {
    setError(null);
    // Billing Phase 4: /calendar/events 402s on a plan without scheduling, so
    // a free user's visit here would otherwise be a spinner that resolves
    // into an error. /calendar/status is deliberately left ungated on the
    // backend (a downgraded user must still see and revoke a connection), but
    // there is nothing to do with the answer on this page, so neither call is
    // made at all.
    if (!canSchedule) {
      setStatusLoading(false);
      setEventsLoading(false);
      return;
    }
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
    // Waits for the plan before deciding: firing on the first render would
    // run the free-user branch for everyone, paying users included, and then
    // never re-run once the plan arrived.
    if (billingLoading) return;
    fetchAll();
  }, [billingLoading, canSchedule]);

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
      toast(err.response?.data?.detail || 'Couldn’t update that event. Please try again.');
    } finally {
      setPendingId(null);
    }
  };

  return (
    <Layout>
      <div className="mb-6">
        <h1 className="text-2xl font-extrabold text-brand-dark tracking-tight">Upcoming</h1>
        <p className="text-sm text-muted mt-1">
          {billingLoading
            ? 'Loading…'
            : !canSchedule
              ? 'Record meetings straight from your calendar on Pro.'
              : statusLoading
                ? 'Checking calendar connection…'
                : status.connected
                  ? `Synced from ${status.google_email}`
                  : 'Connect your Google Calendar to see upcoming meetings here.'}
        </p>
      </div>

      {/* Billing Phase 4. The page is reachable on every plan and explains
          itself, rather than disappearing from the nav - a feature you cannot
          see is one you will never buy. The backend refuses the same routes
          regardless of what is rendered here. */}
      {!billingLoading && !canSchedule && (
        <div className="flex flex-col items-center justify-center text-center gap-5 px-6 py-20">
          <div className="w-14 h-14 rounded-2xl bg-accent-soft flex items-center justify-center">
            <Lock className="h-7 w-7 text-accent-ink" />
          </div>
          <div className="max-w-md">
            <h2 className="text-2xl font-extrabold text-brand-dark tracking-tight">
              Calendar scheduling is part of Pro
            </h2>
            <p className="mt-3 text-[15px] leading-relaxed text-body">
              Connect Google Calendar and opt individual events into recording - MeetIQ joins
              automatically a couple of minutes before each one starts. You&rsquo;re on{' '}
              {planName || 'Free'}.
            </p>
          </div>
          <Link
            to="/settings"
            className="btn-primary px-6 py-3 rounded-xl font-bold text-sm hover:opacity-90 transition-opacity"
          >
            See plans
          </Link>
        </div>
      )}

      {!billingLoading && canSchedule && !statusLoading && !status.connected && (
        <div className="flex flex-col items-center justify-center text-center gap-5 px-6 py-20">
          <div className="w-14 h-14 rounded-2xl bg-accent-soft flex items-center justify-center">
            <CalendarClock className="h-7 w-7 text-accent-ink" />
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
            className="btn-primary px-6 py-3 rounded-xl font-bold text-sm hover:opacity-90 transition-opacity"
          >
            Go to Settings
          </Link>
        </div>
      )}

      {canSchedule && status.connected && (
        <>
          {error && <p className="text-sm text-red-600 dark:text-red-400 mb-4">{error}</p>}

          {eventsLoading ? (
            <>
              <LoadingLabel>Loading your calendar…</LoadingLabel>
              <RowSkeleton rows={4} />
            </>
          ) : events.length === 0 ? (
            <div className="px-6 py-16 text-center text-sm text-muted">
              No upcoming events with a Google Meet or Zoom link.
            </div>
          ) : (
            <div className="flex flex-col">
              {groupEventsByDay(events).map((group) => (
                <div key={group.label}>
                  <div className="sticky top-0 z-5 flex items-center gap-2.5 bg-page pb-1.5 pt-3.5">
                    <span className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted">
                      {group.label}
                    </span>
                    <span className="h-px flex-1 bg-line" />
                  </div>
                  {group.items.map((event) => {
                    const hasLink = Boolean(event.meeting_url);
                    const pending = pendingId === event.id;
                    return (
                      <div
                        key={event.id}
                        className="flex items-center gap-3.5 border-b border-line px-2.5 py-3 transition-colors hover:bg-tint"
                      >
                        <span className="w-13 flex-none text-[12.5px] tabular-nums text-muted">
                          {formatEventTime(event.starts_at)}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[14.5px] font-bold tracking-[-0.015em] text-brand-dark">
                            {event.title}
                          </div>
                          <div className="mt-0.5 truncate text-[13px] text-muted">
                            {hasLink ? formatPlatform(event.platform) : 'No video link'}
                          </div>
                        </div>
                        {hasLink ? (
                          <RecordSwitch
                            on={Boolean(event.already_scheduled)}
                            pending={pending}
                            onChange={() => handleToggle(event)}
                            label={event.already_scheduled ? 'Cancel recording' : 'Record this meeting'}
                          />
                        ) : (
                          // All-day events and events without a link cannot be
                          // scheduled; POST /calendar/events/{id}/schedule
                          // rejects them. Shown, but not made actionable.
                          <span className="flex-none text-[12.5px] text-faint">—</span>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </Layout>
  );
};
