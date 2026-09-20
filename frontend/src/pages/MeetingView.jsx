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
import { usePersistentToggle } from '../hooks/usePersistentToggle';

export const MeetingView = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const [meeting, setMeeting] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleting, setDeleting] = useState(false);
  // Remembered across meetings: collapsing the panel on one call and finding
  // it back on the next is the same annoyance as the sidebar rail resetting.
  const [chatOpen, toggleChat] = usePersistentToggle('meeting-chat-open', true);
  const [sheetOpen, setSheetOpen] = useState(false);

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
        {/* No card. The old wrapper was a bordered, rounded box fixed to
            calc(100vh-4rem) with its own scroller inside the page's scroller -
            a card inside a card, and a scrollbar inside a scrollbar. The page
            is the only scroll container now.

            The chat column is a grid track rather than a fixed 420px flex
            child, which is what squeezed the content between 1024 and 1200px;
            collapsing it now gives the whole width back. */}
        <div
          className={`grid min-h-full items-start gap-0 ${
            chatOpen ? 'wide:grid-cols-[minmax(0,1fr)_360px]' : 'wide:grid-cols-[minmax(0,1fr)]'
          }`}
        >
          <MeetingDetails
            meeting={meeting}
            onRetry={handleRetry}
            onStop={handleStop}
            onDeleteRequest={setDeleteTarget}
            chatOpen={chatOpen}
            onToggleChat={toggleChat}
            onOpenMobileChat={() => setSheetOpen(true)}
          />

          {/* Sticky rather than scrolling with the page, so the composer stays
              put while the transcript moves behind it. -mt-6/-mr-8 cancel the
              page gutter on all three sides so this is a true full-height
              column. Without the top cancel its natural top sits at main's
              24px padding and a 100vh box hangs its composer below the fold;
              without the bottom cancel that same box plus the page's 32px
              bottom padding makes the document taller than the viewport and
              adds a scrollbar to a meeting that fits. */}
          {chatOpen && (
            <aside className="-mr-8 -mb-8 -mt-6 hidden border-l border-line bg-sidebar wide:sticky wide:top-0 wide:block wide:h-screen">
              <MeetingChatInterface />
            </aside>
          )}
        </div>

        <MobileChatSheet
          meetingTitle={meeting.title || meeting.meeting_url}
          open={sheetOpen}
          onClose={() => setSheetOpen(false)}
        />
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
