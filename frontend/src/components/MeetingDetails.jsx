import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertCircle, RefreshCw, Loader2, Download, Trash2, Square,
  ArrowLeft, MoreHorizontal, PanelRight, Sparkles,
} from 'lucide-react';
import { StatusBadge } from './StatusBadge';
import { AudioPlayer } from './meeting/AudioPlayer';
import { SummaryTab } from './meeting/SummaryTab';
import { ActionItemsList } from './meeting/ActionItemsList';
import { TranscriptTab } from './meeting/TranscriptTab';
import { formatPlatform, formatDuration } from '../lib/format';
import api from '../lib/api';
import { useToast } from '../context/ToastContext';

const TABS = [
  { id: 'summary', label: 'Summary' },
  { id: 'action_items', label: 'Action items' },
  { id: 'transcript', label: 'Transcript' },
];

const PROCESSING_STATUSES = ['queued', 'joining', 'waiting_for_admission', 'recording', 'uploading', 'transcribing'];
// Stoppable means there is still something to call off. "queued" has no bot
// yet - the backend cancels it directly - and joining through recording are
// controlled by the meeting-bot process. Once it's past "recording"
// (uploading/transcribing), the bot has already left and there's nothing
// left for a stop request to interrupt.
const STOPPABLE_STATUSES = ['queued', 'joining', 'waiting_for_admission', 'recording'];
const PROCESSING_LABEL = {
  queued: 'Waiting for a free recorder…',
  joining: 'Bot is joining the meeting…',
  waiting_for_admission: 'Waiting for the host to admit MeetIQ…',
  recording: 'Recording in progress…',
  uploading: 'Uploading recording…',
  transcribing: 'Transcribing and generating insights…',
};

/** Delete lives here now rather than as a permanent button beside Export. */
const HeaderMenu = ({ onDelete }) => {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => !wrapRef.current?.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="More actions"
        className="inline-flex h-9 w-9 items-center justify-center rounded-[10px] text-muted transition-colors hover:bg-tint-2 hover:text-brand-dark"
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <div role="menu" className="absolute right-0 top-full z-30 mt-1.5 w-44 overflow-hidden rounded-xl border border-border bg-surface py-1.5 shadow-xl">
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
            className="flex w-full items-center gap-2.5 px-3 py-2 text-sm text-red-600 transition-colors hover:bg-surface-hover dark:text-red-400"
          >
            <Trash2 className="h-4 w-4" />
            Delete meeting
          </button>
        </div>
      )}
    </div>
  );
};

export const MeetingDetails = ({
  meeting, onRetry, onStop, onDeleteRequest,
  chatOpen, onToggleChat, onOpenMobileChat,
}) => {
  const { toast } = useToast();
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
      toast('Couldn’t build the PDF. Please try again.');
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

  // The header and the player bleed out to the page gutter so their dividers
  // run edge to edge. On the right that is only the page edge while the chat
  // column is absent - with it open, the same -mx-8 pushes them 32px *into*
  // the panel, over its heading. So the right-hand bleed is dropped from the
  // width the panel appears at, and a gutter takes its place.
  const gutter = chatOpen ? 'wide:mr-0 wide:pr-6' : '';

  const created = meeting.created_at ? new Date(meeting.created_at) : null;
  const metaDateTime = created
    ? `${created.toLocaleDateString([], { day: 'numeric', month: 'short' })}, ${created.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
    : '';

  return (
    /* The column reaches the bottom of the viewport even when the meeting is
       short, so the player rests on the bottom edge instead of floating in the
       middle of an empty page. 3.5rem is main's own pt-6 + pb-8, so this fills
       exactly and adds no scrollbar. */
    <div className="flex min-h-full min-w-0 flex-col tablet:min-h-[calc(100dvh-3.5rem)]">
      {/* One sticky header. It bleeds past the page gutter with matching
          padding so the divider runs edge to edge while the text stays on the
          content grid. `top` clears the phone top bar, which is fixed. */}
      <header
        className={`sticky top-14 z-20 -mx-4 border-b border-line bg-page/85 px-4 pt-3 backdrop-blur-lg tablet:top-0 tablet:-mx-6 tablet:px-6 lg:-mx-8 lg:px-8 ${gutter}`}
      >
        <div className="flex items-center justify-between gap-3">
          <Link
            to="/dashboard"
            className="inline-flex flex-none items-center gap-1.5 py-1 text-[13px] font-bold text-muted transition-colors hover:text-brand-dark"
          >
            <ArrowLeft className="h-4 w-4" />
            Meetings
          </Link>

          <div className="flex flex-none items-center gap-1.5">
            {meeting.status === 'completed' && (
              <button
                onClick={handleDownloadPDF}
                disabled={isDownloading}
                title="Export as PDF"
                className="inline-flex items-center gap-1.5 rounded-[10px] border border-line bg-surface px-2.5 py-2 text-[13px] font-bold text-brand-dark transition-colors hover:border-border disabled:opacity-50 tablet:px-3.5"
              >
                {isDownloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                <span className="hidden tablet:inline">Export</span>
              </button>
            )}

            {/* Below 1100px the chat opens as a full-screen sheet; above it,
                the column is collapsed and restored in place. */}
            <button
              type="button"
              onClick={onOpenMobileChat}
              aria-label="Ask about this meeting"
              className="inline-flex h-9 w-9 items-center justify-center rounded-[10px] text-muted transition-colors hover:bg-tint-2 hover:text-brand-dark wide:hidden"
            >
              <Sparkles className="h-4.5 w-4.5" />
            </button>
            <button
              type="button"
              onClick={onToggleChat}
              aria-pressed={chatOpen}
              aria-label={chatOpen ? 'Hide the Ask AI panel' : 'Show the Ask AI panel'}
              title={chatOpen ? 'Hide the Ask AI panel' : 'Show the Ask AI panel'}
              className={`hidden h-9 w-9 items-center justify-center rounded-[10px] transition-colors hover:bg-tint-2 wide:inline-flex ${
                chatOpen ? 'text-brand-blue' : 'text-muted hover:text-brand-dark'
              }`}
            >
              <PanelRight className="h-4.5 w-4.5" />
            </button>

            <HeaderMenu onDelete={() => onDeleteRequest(meeting)} />
          </div>
        </div>

        {/* Title on one line, metadata beside it rather than beneath. Below
            720px the metadata wraps under, because there is no room for both. */}
        <div className="mt-2 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          {/* The title gets the whole line on a phone: below 720px the meta
              takes a full-width row of its own rather than competing for the
              same line, which had truncated the title to "Pricing ..." at
              375px. */}
          <h1 className="w-full min-w-0 truncate text-[19px] font-extrabold leading-tight tracking-tight text-brand-dark tablet:w-auto tablet:flex-1">
            {title}
          </h1>
          <span className="flex w-full flex-none items-center gap-2 text-[12.5px] text-muted tablet:w-auto">
            {meeting.status !== 'completed' && <StatusBadge status={meeting.status} />}
            <span>{formatPlatform(meeting.platform)}</span>
            {metaDateTime && (
              <>
                <span aria-hidden="true">·</span>
                <span>{metaDateTime}</span>
              </>
            )}
            <span aria-hidden="true">·</span>
            <span>{formatDuration(meeting.duration_seconds)}</span>
          </span>
        </div>

        <nav className="-mb-px mt-2.5 flex gap-5 overflow-x-auto no-scrollbar">
          {TABS.map((tab) => {
            const on = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                aria-current={on ? 'page' : undefined}
                className={`relative flex-none border-b-2 pb-2.5 pt-2 text-[13.5px] font-bold transition-colors ${
                  on ? 'border-brand-blue text-brand-dark' : 'border-transparent text-muted hover:text-brand-dark'
                }`}
              >
                {tab.label}
                {tab.id === 'action_items' && <span className="ml-1.5 text-[11px] text-muted">{actionCount}</span>}
              </button>
            );
          })}
        </nav>
      </header>

      {/* One scroll container: the page itself. No inner overflow-y-auto, no
          viewport-height card, no scrollbar inside a scrollbar. */}
      <div className={`flex-1 pb-8 pt-5 ${chatOpen ? 'wide:pr-6' : ''}`}>
        {meeting.status === 'failed' && (
          <div className="mb-5 flex items-start gap-3 rounded-xl border border-status-failed-fg/20 bg-status-failed-bg p-4">
            <AlertCircle className="mt-0.5 h-5 w-5 flex-none text-status-failed-fg" />
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-bold text-status-failed-fg">Processing failed</h2>
              <p className="mt-1 break-words text-sm text-status-failed-fg">
                {meeting.error_message || 'An unknown error occurred during transcription.'}
              </p>
            </div>
            <button
              onClick={handleRetryClick}
              disabled={isRetrying}
              className="flex flex-none items-center gap-2 rounded-lg border border-status-failed-fg/25 bg-surface px-3.5 py-2 text-sm font-bold text-status-failed-fg disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${isRetrying ? 'animate-spin' : ''}`} />
              {isRetrying ? 'Retrying…' : 'Retry'}
            </button>
          </div>
        )}

        {PROCESSING_STATUSES.includes(meeting.status) && (
          <div className="mb-5 flex flex-col items-center gap-2 rounded-xl border border-accent-line bg-accent-soft p-5 text-center">
            <Loader2 className="h-6 w-6 animate-spin text-brand-blue" />
            <h2 className="text-sm font-bold text-brand-dark">{PROCESSING_LABEL[meeting.status]}</h2>
            <p className="text-xs text-muted">
              {meeting.status === 'queued'
                ? 'The recorder is busy with another meeting. MeetIQ will join as soon as it frees up.'
                : 'This page will update automatically when finished.'}
            </p>
            {STOPPABLE_STATUSES.includes(meeting.status) && (
              <button
                onClick={handleStopClick}
                disabled={isStopping}
                title={meeting.status === 'queued' ? 'Stop waiting and cancel this recording' : 'Stop the bot and abandon this recording'}
                className="mt-1 flex items-center gap-2 rounded-lg border border-status-failed-fg/25 bg-surface px-3.5 py-2 text-sm font-bold text-status-failed-fg disabled:opacity-50"
              >
                <Square className="h-3.5 w-3.5 fill-current" />
                {isStopping ? 'Stopping…' : meeting.status === 'queued' ? 'Cancel' : 'Stop bot'}
              </button>
            )}
          </div>
        )}

        {activeTab === 'summary' && <SummaryTab summary={summary} keyPoints={key_points} conclusion={conclusion} />}
        {activeTab === 'action_items' && <ActionItemsList actionItems={action_items} />}
        {activeTab === 'transcript' && <TranscriptTab conversation={conversation} />}
      </div>

      <AudioPlayer src={meeting.audio_playback_url} gutterClass={gutter} />
    </div>
  );
};
