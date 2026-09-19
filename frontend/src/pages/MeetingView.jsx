import React, { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Layout } from '../components/Layout';
import { MeetingDetails } from '../components/MeetingDetails';
import { MeetingChatInterface } from '../components/ChatInterface';
import { MobileChatSheet } from '../components/meeting/MobileChatSheet';
import { MeetingChatProvider } from '../context/MeetingChatContext';
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
    // Skip ticks while the tab is hidden. A recording can run for up to
    // MAX_RECORDING_DURATION_MINUTES (90), so a page left open in a
    // background tab was polling ~1000 times for updates nobody could see.
    // The visibilitychange listener fetches once on return, so coming back
    // to the tab shows current state immediately rather than after 5s.
    intervalId = setInterval(() => {
      if (document.visibilityState === 'visible') fetchMeeting();
    }, 5000);

    const onVisible = () => {
      if (document.visibilityState === 'visible') fetchMeeting();
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      isMounted = false;
      if (intervalId) clearInterval(intervalId);
      document.removeEventListener('visibilitychange', onVisible);
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
      const { data } = await api.post(`/meetings/${id}/stop`);
      if (data?.meeting) {
        // A queued meeting had no bot, so the backend cancelled it on the
        // spot and says so. Show it now - otherwise "Cancel" re-enables for
        // up to 5s over a meeting that is already stopped, and a second
        // click earns a 409 alert.
        setMeeting(prev => ({ ...prev, ...data.meeting }));
      }
      // Otherwise the actual "failed" status lands via meeting-bot's webhook
      // once it finishes shutting down (closing the browser, stopping
      // ffmpeg) - the 5s poll above picks that up.
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
      {/* Both chat surfaces below are always mounted (only CSS hides one), so
          they share a single conversation through this provider rather than
          each owning its own copy of the chat state. */}
      <MeetingChatProvider meetingId={meeting.id}>
        {/* Mobile height is intentionally unconstrained: the chat sheet below is
            fixed over the bottom of the viewport, so a viewport-height card with
            its own scroller would bury the tab content underneath it. */}
        <div className="lg:h-[calc(100vh-4rem)] flex bg-surface border border-border-strong rounded-2xl overflow-hidden shadow-sm">
          <div className="flex-1 min-w-0">
            <MeetingDetails meeting={meeting} onRetry={handleRetry} onStop={handleStop} onDeleteRequest={setDeleteTarget} />
          </div>

          {/* Desktop: permanent chat column */}
          <div className="hidden lg:block w-105 flex-none border-l border-line">
            <MeetingChatInterface />
          </div>
        </div>

        {/* Mobile: drag-up chat sheet */}
        <MobileChatSheet meetingTitle={meeting.title || meeting.meeting_url} />
      </MeetingChatProvider>

      <DeleteConfirmDialog
        meeting={deleteTarget}
        deleting={deleting}
        onConfirm={handleDeleteConfirm}
        onCancel={() => setDeleteTarget(null)}
      />
    </Layout>
  );
};
