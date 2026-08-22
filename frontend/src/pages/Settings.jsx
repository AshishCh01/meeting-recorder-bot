import React, { useEffect, useState } from 'react';
import { Layout } from '../components/Layout';
import { useAuth } from '../context/AuthContext';
import { Bot, Loader2, Check, CalendarDays, Bell } from 'lucide-react';
import api from '../lib/api';

const CALENDARS = [
  { abbr: 'GC', label: 'Google Calendar' },
  { abbr: 'MS', label: 'Outlook Calendar' },
];

const NOTIFICATIONS = [
  { label: 'Email me when notes are ready', help: 'One message per meeting, to you only.' },
  { label: 'Slack DM with the summary', help: 'Requires the Slack app.' },
  { label: 'Weekly digest of action items', help: 'Monday morning, your open follow-ups.' },
];

const ComingSoonPill = () => (
  <span className="px-2.5 py-1 rounded-full bg-status-muted-bg text-status-muted-fg text-[11px] font-bold tracking-wide">
    Coming soon
  </span>
);

const DisabledToggle = () => (
  <span className="flex-none w-10.5 h-6 rounded-full bg-border-strong flex items-center px-0.5 opacity-60">
    <span className="w-5 h-5 rounded-full bg-surface shadow" />
  </span>
);

export const Settings = () => {
  const { user } = useAuth();
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
      <div className="max-w-2xl">
        <h1 className="text-2xl font-extrabold text-brand-dark tracking-tight">Settings</h1>
        <p className="text-sm text-muted mt-1">Manage your account and how MeetIQ shows up in your meetings.</p>

        <div className="mt-6 flex flex-col gap-5">
          {/* Account */}
          <div className="bg-surface border border-border-strong rounded-2xl p-6 flex flex-col gap-4">
            <div className="flex items-center gap-3">
              <div className="w-11 h-11 rounded-full bg-border-strong flex items-center justify-center text-body font-bold shrink-0">
                {user?.email?.charAt(0).toUpperCase()}
              </div>
              <div className="min-w-0">
                <h2 className="text-[15px] font-bold text-brand-dark">Account</h2>
                <p className="text-xs text-muted truncate">{user?.email}</p>
              </div>
            </div>
            <div className="grid grid-cols-[100px_1fr] sm:grid-cols-[140px_1fr] gap-y-3 gap-x-4 items-center pt-1">
              <span className="text-[13.5px] text-muted">Email</span>
              <div className="h-10 flex items-center px-3.5 border border-line bg-sidebar rounded-lg text-[14.5px] text-body truncate">
                {user?.email}
              </div>
            </div>
          </div>

          {/* Bot & recording */}
          <div className="bg-surface border border-border-strong rounded-2xl p-6 flex flex-col gap-5">
            <div className="flex items-center gap-3">
              <div className="bg-brand-blue/10 p-2.5 rounded-xl">
                <Bot className="w-5 h-5 text-brand-blue" />
              </div>
              <div>
                <h2 className="text-[15px] font-bold text-brand-dark">Bot &amp; recording</h2>
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
                  <label className="block text-sm font-medium text-body mb-1.5">Bot display name</label>
                  <input
                    type="text"
                    required
                    maxLength={50}
                    value={botDisplayName}
                    onChange={(e) => setBotDisplayName(e.target.value)}
                    placeholder="MeetIQ Notetaker"
                    className="w-full px-4 py-2.5 bg-surface border border-border rounded-xl text-brand-dark placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-brand-blue/20 focus:border-brand-blue transition-all"
                  />
                </div>

                {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

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

          {/* Calendar - not implemented yet */}
          <div className="bg-surface border border-border-strong rounded-2xl p-6 flex flex-col gap-4 opacity-80">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <div className="bg-status-muted-bg p-2.5 rounded-xl">
                  <CalendarDays className="w-5 h-5 text-status-muted-fg" />
                </div>
                <h2 className="text-[15px] font-bold text-brand-dark">Connected calendar</h2>
              </div>
              <ComingSoonPill />
            </div>
            <div className="flex flex-col gap-2.5">
              {CALENDARS.map((cal) => (
                <div
                  key={cal.abbr}
                  className="flex items-center gap-3.5 p-3.5 border border-dashed border-border rounded-xl cursor-not-allowed"
                >
                  <span className="w-8.5 h-8.5 rounded-lg bg-status-muted-bg text-status-muted-fg text-[11px] font-extrabold flex items-center justify-center">
                    {cal.abbr}
                  </span>
                  <div className="flex-1">
                    <div className="text-[14.5px] font-semibold text-brand-dark">{cal.label}</div>
                    <div className="text-xs text-muted">Not connected</div>
                  </div>
                  <span className="px-3 py-1.5 border border-border rounded-lg text-[13px] font-semibold text-faint">
                    Connect
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Notifications - not implemented yet */}
          <div className="bg-surface border border-border-strong rounded-2xl p-6 flex flex-col gap-4 opacity-80">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <div className="bg-status-muted-bg p-2.5 rounded-xl">
                  <Bell className="w-5 h-5 text-status-muted-fg" />
                </div>
                <h2 className="text-[15px] font-bold text-brand-dark">Notifications</h2>
              </div>
              <ComingSoonPill />
            </div>
            <div className="flex flex-col gap-3.5">
              {NOTIFICATIONS.map((n) => (
                <div key={n.label} className="flex items-center gap-4 cursor-not-allowed">
                  <div className="flex-1">
                    <div className="text-[14.5px] font-semibold text-brand-dark">{n.label}</div>
                    <div className="text-xs text-muted mt-0.5">{n.help}</div>
                  </div>
                  <DisabledToggle />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </Layout>
  );
};
