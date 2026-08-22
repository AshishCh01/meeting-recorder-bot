import React from 'react';
import { Loader2 } from 'lucide-react';

export const DeleteConfirmDialog = ({ meeting, deleting, onConfirm, onCancel }) => {
  if (!meeting) return null;
  const title = meeting.title || meeting.meeting_url || 'this meeting';

  return (
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-surface rounded-2xl w-full max-w-sm shadow-xl overflow-hidden">
        <div className="p-6">
          <h2 className="text-lg font-bold text-brand-dark">Delete meeting?</h2>
          <p className="mt-2 text-sm text-body">
            Delete <span className="font-semibold text-brand-dark">"{title}"</span>? This removes the recording,
            transcript, and chat history. This can't be undone.
          </p>
          <div className="mt-6 flex justify-end gap-3">
            <button
              type="button"
              onClick={onCancel}
              disabled={deleting}
              className="px-4 py-2 text-sm font-medium text-body hover:text-brand-dark disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onConfirm}
              disabled={deleting}
              className="flex items-center px-4 py-2 bg-red-600 text-white font-medium text-sm rounded-xl hover:bg-red-700 disabled:opacity-50 transition-colors"
            >
              {deleting ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
              Delete
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
