import React from 'react';
import { Video } from 'lucide-react';

export const EmptyState = ({ onRecordClick }) => (
  <div className="flex flex-col items-center justify-center text-center gap-5 px-6 py-20">
    <div className="w-14 h-14 rounded-2xl bg-status-done-bg flex items-center justify-center">
      <Video className="h-7 w-7 text-status-done-fg" />
    </div>
    <div className="max-w-md">
      <h2 className="text-2xl font-extrabold text-brand-dark tracking-tight">
        Paste a meeting link and the bot will join.
      </h2>
      <p className="mt-3 text-[15px] leading-relaxed text-body">
        Nothing is recorded until you ask. Drop in a Google Meet or Zoom link and MeetIQ will join, record,
        and write up the summary.
      </p>
    </div>
    <button
      onClick={onRecordClick}
      className="px-6 py-3 rounded-xl bg-linear-to-br from-brand-blue to-brand-blue-light text-white font-bold text-sm shadow-sm hover:opacity-90 transition-opacity"
    >
      + Record a meeting
    </button>
  </div>
);
