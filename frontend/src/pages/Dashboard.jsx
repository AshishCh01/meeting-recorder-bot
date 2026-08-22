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

const FILTERS = [
  { key: 'all', label: 'All statuses' },
  { key: 'done', label: 'Completed' },
  { key: 'processing', label: 'Processing' },
  { key: 'failed', label: 'Failed' },
  { key: 'muted', label: 'Scheduled' },
];

export const Dashboard = () => {
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

  const handleRetry = async (meetingId) => {
    setRetryingId(meetingId);
    setMeetings(prev => prev.map(m => m.id === meetingId ? { ...m, status: 'transcribing' } : m));

    try {
      await api.post(`/meetings/${meetingId}/retry`);
    } catch (err) {
      console.error('Retry failed', err);
      alert('Failed to initiate retry.');
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
      alert('Failed to delete meeting.');
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
          <div className="flex gap-3">
            <div className="relative">
              <Search className="w-4 h-4 text-faint absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                placeholder="Search titles"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full sm:w-72 h-10 pl-9 pr-3 rounded-lg border border-border text-sm text-brand-dark placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-blue/30 focus:border-brand-blue transition-all"
              />
            </div>
            <button
              onClick={() => setIsModalOpen(true)}
              className="flex items-center px-4 py-2 bg-linear-to-br from-brand-blue to-brand-blue-light text-white font-bold text-sm rounded-lg hover:opacity-90 transition-opacity shadow-sm shrink-0"
            >
              <Plus className="w-4 h-4 mr-1.5" />
              Record
            </button>
          </div>
        </div>

        <div className="flex gap-2 flex-wrap">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setStatusFilter(f.key)}
              className={`px-3 py-1.5 rounded-full text-xs font-semibold border transition-colors ${
                statusFilter === f.key
                  ? 'bg-brand-dark text-white border-brand-dark'
                  : 'bg-surface text-body border-border-strong hover:border-brand-blue/40'
              }`}
            >
              {f.label}
            </button>
          ))}
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
                className="w-full px-4 py-2.5 rounded-xl border border-border focus:outline-none focus:ring-2 focus:ring-brand-blue/50 focus:border-brand-blue transition-all"
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
                  className="flex items-center px-4 py-2 bg-brand-blue text-white font-medium text-sm rounded-xl hover:opacity-90 disabled:opacity-50 transition-opacity"
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
        <div className="flex h-64 items-center justify-center">
          <Loader2 className="w-8 h-8 animate-spin text-brand-blue" />
        </div>
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
              <div className="flex items-center gap-3 py-3">
                <span className="text-xs font-extrabold tracking-widest uppercase text-muted">{group.label}</span>
                <span className="flex-1 h-px bg-line" />
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
