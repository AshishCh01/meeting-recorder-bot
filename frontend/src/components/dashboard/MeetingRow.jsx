import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, MoreHorizontal, Trash2, RotateCcw, Video } from 'lucide-react';
import { StatusBadge } from '../StatusBadge';
import { getStatusMeta } from '../../lib/status';
import { formatDuration, formatMeetingTime, formatPlatform } from '../../lib/format';

const ICON_TONE = {
  processing: 'bg-status-processing-bg text-status-processing-fg',
  failed: 'bg-status-failed-bg text-status-failed-fg',
  done: 'bg-tint-2 text-muted',
  muted: 'bg-tint-2 text-muted',
};

/**
 * The overflow menu. Delete lives in here rather than in the row, because a
 * destructive action one stray click from a row you meant to open is the kind
 * of affordance people only notice once.
 */
const RowMenu = ({ onDelete }) => {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (!wrapRef.current?.contains(e.target)) setOpen(false);
    };
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
        className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-tint-2 hover:text-brand-dark"
      >
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-20 mt-1.5 w-40 overflow-hidden rounded-xl border border-border bg-surface py-1.5 shadow-xl"
        >
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
            Delete
          </button>
        </div>
      )}
    </div>
  );
};

export const MeetingRow = ({ meeting, onDeleteRequest, onRetry, retrying }) => {
  const title = meeting.title || meeting.meeting_url || 'Meeting';
  const { tone } = getStatusMeta(meeting.status);

  // GET /meetings already returns the whole transcript, so the summary line
  // costs nothing extra here. A meeting still processing has no summary and
  // gets no subline - its badge already says what it is doing, and repeating
  // that under the title just says it twice.

  return (
    <article className="group relative grid grid-cols-[34px_minmax(0,1fr)] items-center gap-x-3 gap-y-0 border-b border-line px-2.5 py-3 transition-colors hover:bg-tint tablet:grid-cols-[34px_minmax(0,1fr)_auto_auto]">
      <span className={`grid h-8.5 w-8.5 place-items-center rounded-[10px] ${ICON_TONE[tone]}`}>
        <Video className="h-4.5 w-4.5" />
      </span>

      <div className="min-w-0">
        {/* The stretched pseudo-element makes the whole row clickable without
            nesting the action buttons inside an anchor. They sit above it on
            z-10. */}
        <Link
          to={`/meetings/${meeting.id}`}
          className="block truncate text-[14.5px] font-bold tracking-[-0.015em] text-brand-dark after:absolute after:inset-0 after:content-['']"
        >
          {title}
        </Link>
        {meeting.transcript?.summary && (
          <p className="mt-0.5 truncate text-[13px] text-muted">{meeting.transcript.summary}</p>
        )}
      </div>

      <div className="relative z-10 col-start-2 mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-2 pr-9 tablet:col-start-3 tablet:row-start-1 tablet:mt-0 tablet:flex-nowrap tablet:gap-3.5 tablet:pr-0">
        {/* Only an abnormal status earns a badge. Eight rows each stamped
            "Completed" tell the user nothing they cannot see from the row. */}
        {tone !== 'done' && <StatusBadge status={meeting.status} />}

        <span className="flex items-center gap-2.5 text-[12.5px] tabular-nums text-muted">
          {meeting.duration_seconds ? (
            <b className="font-semibold text-body">{formatDuration(meeting.duration_seconds)}</b>
          ) : null}
          <span>{formatPlatform(meeting.platform)}</span>
          <span className="hidden tablet:inline">{formatMeetingTime(meeting.created_at)}</span>
        </span>

        {meeting.status === 'failed' && (
          <button
            type="button"
            onClick={() => onRetry(meeting.id)}
            disabled={retrying}
            className="inline-flex flex-none items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-xs font-semibold text-body transition-colors hover:border-brand-blue hover:text-brand-dark disabled:opacity-50"
          >
            {retrying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
            {retrying ? 'Retrying' : 'Retry'}
          </button>
        )}

      </div>

      {/* A child of the article, not of the meta line, so the phone offsets
          anchor to the row. A failed row carries a badge, the meta and a Retry
          button, which together wrap past 375px; inside the meta line the menu
          ended up stranded on a line of its own.

          Hidden until hover or keyboard focus on pointer devices, and always
          present on touch, which has no hover to reveal it with. */}
      <span className="absolute right-2 top-2.5 z-10 flex opacity-100 transition-opacity tablet:static tablet:col-start-4 tablet:row-start-1 tablet:opacity-0 tablet:group-hover:opacity-100 tablet:group-focus-within:opacity-100">
        <RowMenu onDelete={() => onDeleteRequest(meeting)} />
      </span>
    </article>
  );
};
