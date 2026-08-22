import React, { useEffect, useState } from 'react';
import { Layout } from '../components/Layout';
import { Bot, Loader2, Check } from 'lucide-react';
import api from '../lib/api';

export const Settings = () => {
  const [botDisplayName, setBotDisplayName] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let isMounted = true;
    api.get('/users/me')
      .then(({ data }) => {
        if (isMounted) setBotDisplayName(data.bot_display_name);
      })
      .catch((err) => {
        console.error('Failed to load settings', err);
        if (isMounted) setError('Failed to load your settings.');
      })
      .finally(() => {
        if (isMounted) setLoading(false);
      });
    return () => { isMounted = false; };
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const { data } = await api.patch('/users/me', { bot_display_name: botDisplayName });
      setBotDisplayName(data.bot_display_name);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (err) {
      console.error('Failed to save settings', err);
      setError(err.response?.data?.detail || 'Failed to save. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Layout>
      <div className="max-w-xl">
        <h1 className="text-2xl font-extrabold text-brand-dark tracking-tight">Settings</h1>
        <p className="text-sm text-muted mt-1">Manage how MeetIQ shows up in your meetings.</p>

        <div className="mt-6 bg-white border border-border-strong rounded-2xl p-6">
          <div className="flex items-center gap-3 mb-5">
            <div className="bg-brand-blue/10 p-2.5 rounded-xl">
              <Bot className="w-5 h-5 text-brand-blue" />
            </div>
            <div>
              <h2 className="text-[15px] font-bold text-brand-dark">Bot display name</h2>
              <p className="text-xs text-muted">Shown in the participant list when the bot joins a meeting for you.</p>
            </div>
          </div>

          {loading ? (
            <div className="flex justify-center py-6">
              <Loader2 className="w-5 h-5 animate-spin text-brand-blue" />
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              <div>
                <label className="block text-sm font-medium text-body mb-1.5">Display name</label>
                <input
                  type="text"
                  required
                  maxLength={50}
                  value={botDisplayName}
                  onChange={(e) => setBotDisplayName(e.target.value)}
                  placeholder="MeetIQ Notetaker"
                  className="w-full px-4 py-2.5 border border-border rounded-xl text-brand-dark placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-blue/20 focus:border-brand-blue transition-all"
                />
              </div>

              {error && <p className="text-sm text-red-600">{error}</p>}

              <div className="flex items-center gap-3">
                <button
                  type="submit"
                  disabled={saving || !botDisplayName.trim()}
                  className="flex items-center px-4 py-2.5 bg-brand-blue text-white font-medium text-sm rounded-xl hover:opacity-90 disabled:opacity-50 transition-opacity"
                >
                  {saving ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
                  Save changes
                </button>
                {saved && (
                  <span className="flex items-center gap-1.5 text-sm font-medium text-status-done-fg">
                    <Check className="w-4 h-4" /> Saved
                  </span>
                )}
              </div>
            </form>
          )}
        </div>
      </div>
    </Layout>
  );
};
