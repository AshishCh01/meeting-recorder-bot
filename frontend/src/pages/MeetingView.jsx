import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Layout } from '../components/Layout';
import { MeetingDetails } from '../components/MeetingDetails';
import { ChatInterface } from '../components/ChatInterface';
import { MobileChatSheet } from '../components/meeting/MobileChatSheet';
import { DeleteConfirmDialog } from '../components/DeleteConfirmDialog';
import { Loader2 } from 'lucide-react';
import api from '../lib/api';

export const MeetingView = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [meeting, setMeeting] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let intervalId;
    let isMounted = true;

    const fetchMeeting = async () => {
      try {
        const { data } = await api.get(`/meetings/${id}`);
        if (isMounted) {
          setMeeting(data);
          setLoading(false);
          if (data.status === 'completed' || data.status === 'failed') {
            if (intervalId) clearInterval(intervalId);
          }
        }
      } catch (err) {
        console.error(err);
        if (isMounted) {
          setError('Failed to load meeting details.');
          setLoading(false);
          if (intervalId) clearInterval(intervalId);
        }
      }
    };

    fetchMeeting();
    intervalId = setInterval(fetchMeeting, 5000);

    return () => {
      isMounted = false;
      if (intervalId) clearInterval(intervalId);
    };
  }, [id]);

  const handleRetry = async () => {
    try {
      await api.post(`/meetings/${id}/retry`);
      setMeeting(prev => ({ ...prev, status: 'transcribing', error_message: null }));
    } catch (err) {
      console.error(err);
      alert('Retry failed to initiate.');
    }
  };

  const handleStop = async () => {
    try {
      await api.post(`/meetings/${id}/stop`);
      // Actual "failed" status lands via meeting-bot's webhook once it
      // finishes shutting down (closing the browser, stopping ffmpeg) -
      // the 5s poll above picks that up, no need to set it optimistically.
    } catch (err) {
      console.error(err);
      alert(err.response?.data?.detail || 'Failed to stop the bot.');
    }
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.delete(`/meetings/${deleteTarget.id}`);
      navigate('/dashboard');
    } catch (err) {
      console.error('Delete failed', err);
      alert('Failed to delete meeting.');
      setDeleting(false);
    }
  };

  if (loading) {
    return (
      <Layout>
        <div className="flex h-[60vh] items-center justify-center">
          <Loader2 className="w-8 h-8 animate-spin text-brand-blue" />
        </div>
      </Layout>
    );
  }

  if (error || !meeting) {
    return (
      <Layout>
        <div className="bg-red-50 dark:bg-red-500/10 text-red-600 dark:text-red-400 p-6 rounded-xl border border-red-100 dark:border-red-500/20">
          {error || 'Meeting not found'}
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="h-[calc(100vh-13rem)] lg:h-[calc(100vh-4rem)] flex bg-surface border border-border-strong rounded-2xl overflow-hidden shadow-sm">
        <div className="flex-1 min-w-0">
          <MeetingDetails meeting={meeting} onRetry={handleRetry} onStop={handleStop} onDeleteRequest={setDeleteTarget} />
        </div>

        {/* Desktop: permanent chat column */}
        <div className="hidden lg:block w-105 flex-none border-l border-line">
          <ChatInterface meetingId={meeting.id} />
        </div>
      </div>

      {/* Mobile: drag-up chat sheet */}
      <MobileChatSheet meetingId={meeting.id} meetingTitle={meeting.title || meeting.meeting_url} />

      <DeleteConfirmDialog
        meeting={deleteTarget}
        deleting={deleting}
        onConfirm={handleDeleteConfirm}
        onCancel={() => setDeleteTarget(null)}
      />
    </Layout>
  );
};
