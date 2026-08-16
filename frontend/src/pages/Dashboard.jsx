import React, { useEffect, useState } from 'react';
import { Layout } from '../components/Layout';
import { Video, Calendar, Clock, ArrowRight, Plus, Loader2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import api from '../lib/api';

export const Dashboard = () => {
  const [meetings, setMeetings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [meetingUrl, setMeetingUrl] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

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

  return (
    <Layout>
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-brand-dark tracking-tight">Dashboard</h1>
          <p className="text-sm text-slate-500 mt-1">Manage and review your recorded meetings.</p>
        </div>
        <button 
          onClick={() => setIsModalOpen(true)}
          className="flex items-center px-4 py-2.5 bg-brand-blue text-white font-medium text-sm rounded-xl hover:bg-blue-700 transition-colors shadow-sm shadow-brand-blue/20"
        >
          <Plus className="w-4 h-4 mr-2" />
          Record Meeting
        </button>
      </div>

      {isModalOpen && (
        <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl w-full max-w-md shadow-xl overflow-hidden">
            <div className="px-6 py-4 border-b border-slate-100">
              <h2 className="text-lg font-semibold text-brand-dark">Record New Meeting</h2>
            </div>
            <form onSubmit={handleRecord} className="p-6">
              <label className="block text-sm font-medium text-slate-700 mb-2">Meeting URL</label>
              <input 
                type="url"
                required
                placeholder="https://meet.google.com/abc-defg-hij"
                className="w-full px-4 py-2.5 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-brand-blue/50 focus:border-brand-blue transition-all"
                value={meetingUrl}
                onChange={(e) => setMeetingUrl(e.target.value)}
              />
              {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
              <div className="mt-6 flex justify-end gap-3">
                <button 
                  type="button" 
                  onClick={() => setIsModalOpen(false)}
                  className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-900"
                >
                  Cancel
                </button>
                <button 
                  type="submit" 
                  disabled={submitting}
                  className="flex items-center px-4 py-2 bg-brand-blue text-white font-medium text-sm rounded-xl hover:bg-blue-700 disabled:opacity-50 transition-colors"
                >
                  {submitting ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
                  Start Recording
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden min-h-[300px]">
        <div className="px-6 py-4 border-b border-slate-100 bg-slate-50/50">
          <h2 className="text-sm font-semibold text-brand-dark">Recent Recordings</h2>
        </div>
        
        {loading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="w-8 h-8 animate-spin text-brand-blue" />
          </div>
        ) : meetings.length > 0 ? (
          <ul className="divide-y divide-slate-100">
            {meetings.map((meeting) => (
              <li key={meeting.id} className="hover:bg-slate-50 transition-colors group cursor-pointer block">
                <Link to={`/meetings/${meeting.id}`} className="px-6 py-5 flex items-center justify-between">
                  <div className="flex items-center min-w-0 gap-4">
                    <div className="flex-shrink-0 h-12 w-12 rounded-xl bg-brand-blue/5 flex items-center justify-center border border-brand-blue/10 group-hover:border-brand-blue/20 transition-colors">
                      <Video className="h-5 w-5 text-brand-blue" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-brand-dark truncate">{meeting.meeting_url || 'Meeting'} - {meeting.status}</p>
                      <div className="mt-1 flex items-center gap-4 text-xs text-slate-500">
                        {meeting.created_at && (
                          <div className="flex items-center gap-1.5">
                            <Calendar className="w-3.5 h-3.5" />
                            {new Date(meeting.created_at).toLocaleDateString()}
                          </div>
                        )}
                        {meeting.duration_seconds > 0 && (
                          <div className="flex items-center gap-1.5">
                            <Clock className="w-3.5 h-3.5" />
                            {Math.round(meeting.duration_seconds / 60)}m
                          </div>
                        )}
                        {meeting.platform && (
                          <div className="hidden sm:block">
                            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-600 capitalize">
                              {meeting.platform}
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                  <div className="flex-shrink-0">
                    <ArrowRight className="h-5 w-5 text-slate-300 group-hover:text-brand-blue transition-colors" />
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <div className="px-6 py-12 text-center">
            <div className="w-16 h-16 mx-auto bg-slate-50 rounded-2xl flex items-center justify-center mb-4 border border-slate-100">
              <Video className="h-8 w-8 text-slate-300" />
            </div>
            <h3 className="text-sm font-medium text-brand-dark mb-1">No recordings yet</h3>
            <p className="text-sm text-slate-500">Get started by recording your first meeting.</p>
          </div>
        )}
      </div>
    </Layout>
  );
};
