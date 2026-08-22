import React from 'react';
import { Link } from 'react-router-dom';
import { Loader2, Trash2 } from 'lucide-react';
import { StatusBadge } from '../StatusBadge';
import { abbreviate, formatDuration, formatMeetingTime, formatPlatform } from '../../lib/format';

export const MeetingRow = ({ meeting, onDeleteRequest, onRetry, retrying }) => {
  const title = meeting.title || meeting.meeting_url || 'Meeting';
  const abbr = abbreviate(title);
  const time = formatMeetingTime(meeting.created_at);
  const duration = formatDuration(meeting.duration_seconds);
  const platform = formatPlatform(meeting.platform);

  const handleDelete = (e) => {
    e.preventDefault();
    e.stopPropagation();
    onDeleteRequest(meeting);
  };

  const handleRetry = (e) => {
    e.preventDefault();
    e.stopPropagation();
    onRetry(meeting.id);
  };

  const actions = (
    <div className="flex items-center gap-2 flex-none">
      {meeting.status === 'failed' && (
        <button
          onClick={handleRetry}
          disabled={retrying}
          className="px-2.5 py-1 bg-red-50 dark:bg-red-500/10 text-red-600 dark:text-red-400 text-xs font-semibold rounded-lg hover:bg-red-100 dark:hover:bg-red-500/20 transition-colors border border-red-200 dark:border-red-500/20 flex items-center gap-1.5 disabled:opacity-50"
        >
          {retrying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
          {retrying ? 'Retrying' : 'Retry'}
        </button>
      )}
      <button
        onClick={handleDelete}
        title="Delete meeting"
        className="p-1.5 text-faint hover:text-red-600 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-500/10 rounded-lg transition-colors"
      >
        <Trash2 className="w-4 h-4" />
      </button>
    </div>
  );

  return (
    <Link
      to={`/meetings/${meeting.id}`}
      className="group block rounded-xl border border-line hover:border-brand-blue/30 hover:bg-sidebar transition-colors mb-2"
    >
      {/* Desktop row */}
      <div className="hidden lg:grid grid-cols-[1fr_130px_90px_150px_auto] gap-4 items-center px-4 py-4">
        <div className="flex items-center gap-3 min-w-0">
          <span className="flex-none w-9 h-9 rounded-lg bg-status-done-bg text-status-done-fg text-[11px] font-extrabold flex items-center justify-center">
            {abbr}
          </span>
          <span className="text-[15px] font-bold text-brand-dark tracking-tight truncate">{title}</span>
        </div>
        <div>
          <StatusBadge status={meeting.status} />
        </div>
        <div className="text-sm text-body tabular-nums">{duration}</div>
        <div className="text-sm text-muted truncate">{platform} · {time}</div>
        {actions}
      </div>

      {/* Mobile row */}
      <div className="lg:hidden flex items-start gap-3 px-3 py-3">
        <span className="flex-none w-8 h-8 rounded-lg bg-status-done-bg text-status-done-fg text-[10.5px] font-extrabold flex items-center justify-center">
          {abbr}
        </span>
        <div className="flex-1 min-w-0 flex flex-col gap-1.5">
          <div className="text-[15px] font-bold text-brand-dark leading-tight tracking-tight">{title}</div>
          <div className="flex items-center gap-2 flex-wrap">
            <StatusBadge status={meeting.status} />
            <span className="text-xs text-muted">{time} · {duration} · {platform}</span>
          </div>
        </div>
        {actions}
      </div>
    </Link>
  );
};
