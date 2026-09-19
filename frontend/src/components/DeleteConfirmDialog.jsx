import React from 'react';
import { ConfirmDialog } from './ConfirmDialog';

export const DeleteConfirmDialog = ({ meeting, deleting, onConfirm, onCancel }) => {
  if (!meeting) return null;
  const title = meeting.title || meeting.meeting_url || 'this meeting';

  return (
    <ConfirmDialog open title="Delete meeting?" busy={deleting} onConfirm={onConfirm} onCancel={onCancel}>
      Delete <span className="font-semibold text-brand-dark">"{title}"</span>? This removes the recording,
      transcript, and chat history. This can't be undone.
    </ConfirmDialog>
  );
};
