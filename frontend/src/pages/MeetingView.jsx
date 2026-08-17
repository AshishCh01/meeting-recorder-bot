import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Layout } from '../components/Layout';
import { MeetingDetails } from '../components/MeetingDetails';
import { ChatInterface } from '../components/ChatInterface';
import { ArrowLeft, Loader2 } from 'lucide-react';
import api from '../lib/api';

export const MeetingView = () => {
  const { id } = useParams();
  const [meeting, setMeeting] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

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
      alert("Retry failed to initiate.");
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
        <div className="bg-red-50 text-red-600 p-6 rounded-xl border border-red-100">
          {error || 'Meeting not found'}
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="mb-6 flex items-center">
        <Link to="/dashboard" className="text-slate-400 hover:text-brand-blue transition-colors flex items-center text-sm font-medium mr-4">
          <ArrowLeft className="w-4 h-4 mr-1" />
          Back
        </Link>
        <h1 className="text-2xl font-bold text-brand-dark tracking-tight truncate flex-1">
          {meeting.title || 'Recorded Meeting'}
        </h1>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 h-[calc(100vh-140px)]">
        {/* Left Pane: Static Details */}
        <div className="h-full overflow-hidden">
          <MeetingDetails meeting={meeting} onRetry={handleRetry} />
        </div>

        {/* Right Pane: AI Chat */}
        <div className="h-full overflow-hidden">
          <ChatInterface meetingId={meeting.id} />
        </div>
      </div>
    </Layout>
  );
};
