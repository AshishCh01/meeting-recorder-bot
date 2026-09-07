import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, RefreshCw, Loader2, Download, Trash2, Square } from 'lucide-react';
import { StatusBadge } from './StatusBadge';
import { AudioPlayer } from './meeting/AudioPlayer';
import { SummaryTab } from './meeting/SummaryTab';
import { ActionItemsList } from './meeting/ActionItemsList';
import { TranscriptTab } from './meeting/TranscriptTab';
import { formatPlatform, formatDuration } from '../lib/format';
import api from '../lib/api';

const TABS = [
  { id: 'summary', label: 'Summary' },
  { id: 'action_items', label: 'Action items' },
  { id: 'transcript', label: 'Transcript' },
];

const PROCESSING_STATUSES = ['joining', 'waiting_for_admission', 'recording', 'uploading', 'transcribing'];
// Only these are still controlled by the meeting-bot process - once it's
// past "recording" (uploading/transcribing), the bot has already left and
// there's nothing left for a stop request to interrupt.
const STOPPABLE_STATUSES = ['joining', 'waiting_for_admission', 'recording'];
const PROCESSING_LABEL = {
  joining: 'Bot is joining the meeting…',
  waiting_for_admission: 'Waiting for the host to admit MeetIQ…',
  recording: 'Recording in progress…',
  uploading: 'Uploading recording…',
  transcribing: 'Transcribing and generating insights…',
};

export const MeetingDetails = ({ meeting, onRetry, onStop, onDeleteRequest }) => {
  const [activeTab, setActiveTab] = useState('summary');
  const [isRetrying, setIsRetrying] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [isStopping, setIsStopping] = useState(false);

  const handleDownloadPDF = async () => {
    setIsDownloading(true);
    try {
      const response = await api.get(`/meetings/${meeting.id}/export-pdf`, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `meeting-summary-${meeting.id}.pdf`);
      document.body.appendChild(link);
      link.click();
      link.parentNode.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Error downloading PDF:', error);
      alert('Failed to download PDF. Please try again.');
    } finally {
      setIsDownloading(false);
    }
  };

  const handleRetryClick = async () => {
    setIsRetrying(true);
    if (onRetry) await onRetry();
    setIsRetrying(false);
  };

  const handleStopClick = async () => {
    setIsStopping(true);
    if (onStop) await onStop();
    setIsStopping(false);
  };

  const transcriptData = meeting?.transcript || {};
  const { summary, key_points, conclusion, action_items, conversation } = transcriptData;
  const actionCount = action_items?.length || 0;
  const title = meeting.title || meeting.meeting_url || 'Meeting';

  const created = meeting.created_at ? new Date(meeting.created_at) : null;
  const breadcrumbDate = created
    ? created.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })
    : '';
  const metaDateTime = created
    ? `${created.toLocaleDateString([], { day: 'numeric', month: 'short' })}, ${created.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
    : '';

  return (
    <div className="flex flex-col lg:h-full lg:overflow-hidden bg-surface">
      {/* Header */}
      <div className="flex-none px-5 lg:px-7 py-4 lg:py-5 border-b border-line flex flex-col gap-3.5">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5 text-[13px] text-muted min-w-0">
            <Link to="/dashboard" className="font-bold text-brand-blue whitespace-nowrap">← Meetings</Link>
            {breadcrumbDate && (
              <>
                <span>/</span>
                <span className="truncate">{breadcrumbDate}</span>
              </>
            )}
          </div>
          <div className="flex gap-2 flex-none">
            {meeting.status === 'completed' && (
              <button
                onClick={handleDownloadPDF}
                disabled={isDownloading}
                title="Export as PDF"
                className="px-2.5 lg:px-3.5 py-2 border border-border rounded-lg text-[13px] font-bold text-brand-dark disabled:opacity-50 flex items-center gap-1.5"
              >
                {isDownloading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                <span className="hidden lg:inline">Export</span>
              </button>
            )}
            <button
              onClick={() => onDeleteRequest(meeting)}
              title="Delete meeting"
              className="px-2.5 lg:px-3.5 py-2 border border-border rounded-lg text-[13px] font-bold text-brand-dark flex items-center gap-1.5"
            >
              <Trash2 className="w-4 h-4" />
              <span className="hidden lg:inline">Delete</span>
            </button>
          </div>
        </div>

        <div className="min-w-0">
          <h1 className="text-xl lg:text-[26px] font-extrabold tracking-tight text-brand-dark leading-tight">{title}</h1>
          <div className="flex items-center gap-2.5 mt-2 flex-wrap">
            <StatusBadge status={meeting.status} />
            <span className="text-[13px] lg:text-[13.5px] text-muted">
              {formatPlatform(meeting.platform)}
              {metaDateTime ? ` · ${metaDateTime}` : ''} · {formatDuration(meeting.duration_seconds)}
            </span>
          </div>
        </div>

        <AudioPlayer src={meeting.audio_playback_url} />

        {meeting.status === 'failed' && (
          <div className="p-4 bg-status-failed-bg border border-red-100 dark:border-red-500/20 rounded-xl flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-status-failed-fg mt-0.5 flex-none" />
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-bold text-status-failed-fg">Processing failed</h3>
              <p className="text-sm text-status-failed-fg/90 mt-1">
                {meeting.error_message || 'An unknown error occurred during transcription.'}
              </p>
            </div>
            <button
              onClick={handleRetryClick}
              disabled={isRetrying}
              className="flex-none px-3.5 py-2 bg-surface text-status-failed-fg text-sm font-bold border border-red-200 dark:border-red-500/20 rounded-lg disabled:opacity-50 flex items-center gap-2"
            >
              <RefreshCw className={`w-4 h-4 ${isRetrying ? 'animate-spin' : ''}`} />
              {isRetrying ? 'Retrying…' : 'Retry'}
            </button>
          </div>
        )}

        {PROCESSING_STATUSES.includes(meeting.status) && (
          <div className="p-5 bg-status-done-bg border border-border-strong rounded-xl flex flex-col items-center text-center gap-2">
            <Loader2 className="w-6 h-6 text-brand-blue animate-spin" />
            <h3 className="text-sm font-bold text-brand-dark">{PROCESSING_LABEL[meeting.status]}</h3>
            <p className="text-xs text-muted">This page will update automatically when finished.</p>
            {STOPPABLE_STATUSES.includes(meeting.status) && (
              <button
                onClick={handleStopClick}
                disabled={isStopping}
                title="Stop the bot and abandon this recording"
                className="mt-1 px-3.5 py-2 bg-surface text-status-failed-fg text-sm font-bold border border-red-200 dark:border-red-500/20 rounded-lg disabled:opacity-50 flex items-center gap-2"
              >
                <Square className="w-3.5 h-3.5 fill-current" />
                {isStopping ? 'Stopping…' : 'Stop bot'}
              </button>
            )}
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex-none px-5 lg:px-7 pt-3 lg:pt-0">
        <div className="hidden lg:flex gap-6 border-b border-line -mb-px">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`py-3.5 text-sm font-bold border-b-2 transition-colors flex items-center gap-1.5 ${
                activeTab === tab.id ? 'text-brand-blue border-brand-blue' : 'text-muted border-transparent hover:text-brand-dark'
              }`}
            >
              {tab.label}
              {tab.id === 'action_items' && (
                <span className="text-[11px] bg-line text-body rounded-md px-1.5 py-0.5">{actionCount}</span>
              )}
            </button>
          ))}
        </div>
        <div className="lg:hidden flex gap-1.5 bg-status-muted-bg rounded-[10px] p-1 mb-3">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex-1 text-center py-2 rounded-lg text-[13px] font-bold transition-colors ${
                activeTab === tab.id ? 'bg-surface text-brand-dark shadow-sm' : 'text-body'
              }`}
            >
              {tab.id === 'action_items' ? `Actions · ${actionCount}` : tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 lg:overflow-y-auto px-5 lg:px-7 pt-5 pb-44 lg:pb-5">
        {activeTab === 'summary' && <SummaryTab summary={summary} keyPoints={key_points} conclusion={conclusion} />}
        {activeTab === 'action_items' && <ActionItemsList actionItems={action_items} />}
        {activeTab === 'transcript' && <TranscriptTab conversation={conversation} />}
      </div>
    </div>
  );
};
