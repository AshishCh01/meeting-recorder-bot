import React, { useEffect, useMemo, useState } from 'react';
import { Layout } from '../components/Layout';
import { MeetingRow } from '../components/dashboard/MeetingRow';
import { EmptyState } from '../components/dashboard/EmptyState';
import { DeleteConfirmDialog } from '../components/DeleteConfirmDialog';
import { Search, Plus, Loader2 } from 'lucide-react';
import api from '../lib/api';
import { getStatusMeta } from '../lib/status';
import { groupMeetingsByDate } from '../lib/dateGroups';
import { formatDuration } from '../lib/format';
import { useToast } from '../context/ToastContext';
import { LoadingLabel, RowSkeleton } from '../components/Skeleton';

const FILTERS = [
  { key: 'all', label: 'All statuses' },
  { key: 'done', label: 'Completed' },
  { key: 'processing', label: 'Processing' },
  { key: 'failed', label: 'Failed' },
  { key: 'muted', label: 'Scheduled' },
];

export const Dashboard = () => {
  const { toast } = useToast();
  const [meetings, setMeetings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [meetingUrl, setMeetingUrl] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [retryingId, setRetryingId] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const fetchMeetings = async () => {
    try {
      const { data } = await api.get('/meetings');
      setMeetings(data);
    } catch (err) {
      console.error('Failed to fetch meetings', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMeetings();
  }, []);

  // A meeting that finishes recording/transcribing in the background used to
  // sit here looking unchanged until the user manually reloaded. Refresh while
  // anything is still in flight, then stop once everything has settled - and
  // skip ticks while the tab is hidden, so a dashboard left open in a
  // background tab isn't polling all day.
  const hasMeetingInProgress = useMemo(
    () => meetings.some(m => getStatusMeta(m.status).tone === 'processing'),
    [meetings]
  );

  useEffect(() => {
    if (!hasMeetingInProgress) return;
    const intervalId = setInterval(() => {
      if (document.visibilityState === 'visible') fetchMeetings();
    }, 10000);
    return () => clearInterval(intervalId);
  }, [hasMeetingInProgress]);

  const handleRetry = async (meetingId) => {
    setRetryingId(meetingId);
    setMeetings(prev => prev.map(m => m.id === meetingId ? { ...m, status: 'transcribing' } : m));

    try {
      await api.post(`/meetings/${meetingId}/retry`);
    } catch (err) {
      console.error('Retry failed', err);
      toast(err.response?.data?.detail || 'Couldn’t start the retry. Please try again.');
      fetchMeetings(); // Revert optimistic update
    } finally {
      setRetryingId(null);
    }
  };

  const handleRecord = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.post('/meetings', { meeting_url: meetingUrl });
      setMeetingUrl('');
      setIsModalOpen(false);
      fetchMeetings(); // Refresh list to show new meeting
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to start recording');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    const id = deleteTarget.id;
    setDeleting(true);
    try {
      await api.delete(`/meetings/${id}`);
      setMeetings(prev => prev.filter(m => m.id !== id));
      setDeleteTarget(null);
    } catch (err) {
      console.error('Delete failed', err);
      toast(err.response?.data?.detail || 'Couldn’t delete that meeting. Please try again.');
    } finally {
      setDeleting(false);
    }
  };

  const filteredMeetings = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return meetings.filter((m) => {
      if (statusFilter !== 'all' && getStatusMeta(m.status).tone !== statusFilter) return false;
      if (query) {
        const title = (m.title || m.meeting_url || '').toLowerCase();
        if (!title.includes(query)) return false;
      }
      return true;
    });
  }, [meetings, searchQuery, statusFilter]);

  const groups = useMemo(() => groupMeetingsByDate(filteredMeetings), [filteredMeetings]);

  // Chip counts ignore the status filter (a chip has to say what it would
  // show, not what the current filter leaves) but respect the search, so
  // searching narrows every chip at once.
  const counts = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    const searched = meetings.filter(
      (m) => !query || (m.title || m.meeting_url || '').toLowerCase().includes(query)
    );
    return FILTERS.reduce((acc, f) => {
      acc[f.key] =
        f.key === 'all' ? searched.length : searched.filter((m) => getStatusMeta(m.status).tone === f.key).length;
      return acc;
    }, {});
  }, [meetings, searchQuery]);

  const totalDurationLabel = useMemo(() => {
    const totalSeconds = meetings.reduce((sum, m) => sum + (m.duration_seconds || 0), 0);
    return formatDuration(totalSeconds);
  }, [meetings]);

  const isFiltering = searchQuery.trim() !== '' || statusFilter !== 'all';

  return (
    <Layout>
      <div className="mb-6 flex flex-col gap-4">
        <div className="flex items-start justify-between flex-wrap gap-4">
          <div>
            <h1 className="text-2xl font-extrabold text-brand-dark tracking-tight">Meetings</h1>
            <p className="text-sm text-muted mt-1">
              {meetings.length} recording{meetings.length === 1 ? '' : 's'} · {totalDurationLabel} of audio
            </p>
          </div>
          <div className="flex w-full gap-3 sm:w-auto">
            <div className="relative flex-1 sm:flex-none">
              <Search className="w-4 h-4 text-muted absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                placeholder="Search titles"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full sm:w-72 h-10 pl-9 pr-3 rounded-[10px] border border-line bg-surface text-base sm:text-sm text-brand-dark placeholder:text-muted focus:outline-none focus:border-brand-blue transition-colors"
              />
            </div>
            <button
              onClick={() => setIsModalOpen(true)}
              className="btn-primary flex flex-none items-center gap-1.5 rounded-[10px] px-4 py-2 text-sm font-bold transition-opacity hover:opacity-90"
            >
              <Plus className="w-4 h-4" />
              Record
            </button>
          </div>
        </div>

        {/* Scrolls sideways on a phone instead of clipping at the edge. */}
        <div className="-mx-4 flex gap-2 overflow-x-auto px-4 no-scrollbar tablet:mx-0 tablet:px-0" role="tablist" aria-label="Filter by status">
          {FILTERS.map((f) => {
            const on = statusFilter === f.key;
            return (
              <button
                key={f.key}
                role="tab"
                aria-selected={on}
                onClick={() => setStatusFilter(f.key)}
                className={`flex h-8 flex-none items-center gap-1.5 rounded-full border px-3.5 text-[13px] font-semibold transition-colors ${
                  on
                    ? 'border-accent-line bg-accent-soft text-accent-ink'
                    : 'border-line bg-surface text-body hover:border-border hover:text-brand-dark'
                }`}
              >
                {f.label}
                <span className={`text-[11.5px] tabular-nums ${on ? 'text-accent-ink' : 'text-muted'}`}>
                  {counts[f.key] ?? 0}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {isModalOpen && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-surface rounded-2xl w-full max-w-md shadow-xl overflow-hidden">
            <div className="px-6 py-4 border-b border-line">
              <h2 className="text-lg font-bold text-brand-dark">Record New Meeting</h2>
            </div>
            <form onSubmit={handleRecord} className="p-6">
              <label className="block text-sm font-medium text-body mb-2">Meeting URL</label>
              <input
                type="url"
                required
                placeholder="https://meet.google.com/abc-defg-hij"
                className="w-full px-4 py-2.5 rounded-xl border border-border bg-surface text-base text-brand-dark placeholder:text-muted focus:outline-none focus:border-brand-blue transition-colors"
                value={meetingUrl}
                onChange={(e) => setMeetingUrl(e.target.value)}
              />
              {error && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
              <div className="mt-6 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setIsModalOpen(false)}
                  className="px-4 py-2 text-sm font-medium text-body hover:text-brand-dark"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="btn-primary flex items-center px-4 py-2 font-bold text-sm rounded-xl hover:opacity-90 disabled:opacity-50 transition-opacity"
                >
                  {submitting ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
                  Start Recording
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <DeleteConfirmDialog
        meeting={deleteTarget}
        deleting={deleting}
        onConfirm={handleDeleteConfirm}
        onCancel={() => setDeleteTarget(null)}
      />

      {loading ? (
        <>
          <LoadingLabel>Loading your meetings…</LoadingLabel>
          <RowSkeleton />
        </>
      ) : meetings.length === 0 ? (
        <EmptyState onRecordClick={() => setIsModalOpen(true)} />
      ) : groups.length === 0 ? (
        <div className="px-6 py-16 text-center text-sm text-muted">
          {isFiltering ? 'No meetings match your search or filter.' : 'No meetings yet.'}
        </div>
      ) : (
        <div className="flex flex-col">
          {groups.map((group) => (
            <div key={group.label}>
              <div className="sticky top-0 z-5 flex items-center gap-2.5 bg-page pb-1.5 pt-3.5">
                <span className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-muted">{group.label}</span>
                <span className="h-px flex-1 bg-line" />
              </div>
              {group.items.map((meeting) => (
                <MeetingRow
                  key={meeting.id}
                  meeting={meeting}
                  onDeleteRequest={setDeleteTarget}
                  onRetry={handleRetry}
                  retrying={retryingId === meeting.id}
                />
              ))}
            </div>
          ))}
        </div>
      )}
    </Layout>
  );
};
