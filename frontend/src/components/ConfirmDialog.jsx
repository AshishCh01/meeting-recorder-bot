import React from 'react';
import { Loader2 } from 'lucide-react';

// A destructive-action confirmation. DeleteConfirmDialog (meetings) and the
// Ask AI chat deletes are built on it.
export const ConfirmDialog = ({ open, title, children, confirmLabel = 'Delete', busy, onConfirm, onCancel }) => {
  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-slate-900/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
      <div className="bg-surface rounded-2xl w-full max-w-sm shadow-xl overflow-hidden">
        <div className="p-6">
          <h2 className="text-lg font-bold text-brand-dark">{title}</h2>
          <p className="mt-2 text-sm text-body">{children}</p>
          <div className="mt-6 flex justify-end gap-3">
            <button
              type="button"
              onClick={onCancel}
              disabled={busy}
              className="px-4 py-2 text-sm font-medium text-body hover:text-brand-dark disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onConfirm}
              disabled={busy}
              className="flex items-center px-4 py-2 bg-red-600 text-white font-medium text-sm rounded-xl hover:bg-red-700 disabled:opacity-50 transition-colors"
            >
              {busy ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
