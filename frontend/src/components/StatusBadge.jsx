import React from 'react';
import { getStatusMeta } from '../lib/status';

const TONE_CLASSES = {
  done: 'bg-status-done-bg text-status-done-fg',
  processing: 'bg-status-processing-bg text-status-processing-fg',
  failed: 'bg-status-failed-bg text-status-failed-fg',
  muted: 'bg-status-muted-bg text-status-muted-fg',
};

export const StatusBadge = ({ status, className = '' }) => {
  const { label, tone } = getStatusMeta(status);
  return (
    <span
      className={`inline-flex items-center px-2.5 py-1 rounded-md text-xs font-bold whitespace-nowrap ${TONE_CLASSES[tone]} ${className}`}
    >
      {label}
    </span>
  );
};
