import React, { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Layout } from '../components/Layout';
import { useAuth } from '../context/AuthContext';
import { Bot, Loader2, Check, CalendarDays, Bell, Sparkles } from 'lucide-react';
import api from '../lib/api';

const NOTIFICATIONS = [
  { label: 'Email me when notes are ready', help: 'One message per meeting, to you only.' },
  { label: 'Slack DM with the summary', help: 'Requires the Slack app.' },
  { label: 'Weekly digest of action items', help: 'Monday morning, your open follow-ups.' },
];

// The backend's cap (UserSettingsUpdate.ask_ai_instructions).
const ASK_AI_INSTRUCTIONS_MAX = 1000;

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

  const [instructions, setInstructions] = useState('');
  const [savingInstructions, setSavingInstructions] = useState(false);
  const [instructionsError, setInstructionsError] = useState(null);
  const [instructionsSaved, setInstructionsSaved] = useState(false);

  const [searchParams, setSearchParams] = useSearchParams();
  const [calendarStatus, setCalendarStatus] = useState({ connected: false, google_email: null });
  const [calendarLoading, setCalendarLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [calendarError, setCalendarError] = useState(null);
  const [justConnected, setJustConnected] = useState(false);

  const fetchCalendarStatus = () => {
    setCalendarLoading(true);
    return api.get('/calendar/status')
      .then(({ data }) => setCalendarStatus(data))
      .catch((err) => console.error('Failed to load calendar status', err))
      .finally(() => setCalendarLoading(false));
  };

  useEffect(() => {
    fetchCalendarStatus();
  }, []);

  useEffect(() => {
    if (searchParams.get('calendar') === 'connected') {
      setJustConnected(true);
      setSearchParams({}, { replace: true });
      fetchCalendarStatus();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const handleConnectCalendar = async () => {
    setConnecting(true);
    setCalendarError(null);
    try {
      const { data } = await api.get('/calendar/connect');
      window.location.href = data.auth_url;
    } catch (err) {
      setCalendarError(err.response?.data?.detail || 'Failed to start connecting Google Calendar.');
      setConnecting(false);
    }
  };

  const handleDisconnectCalendar = async () => {
    setDisconnecting(true);
    setCalendarError(null);
    try {
      await api.delete('/calendar/disconnect');
      setCalendarStatus({ connected: false, google_email: null });
      setJustConnected(false);
    } catch (err) {
      setCalendarError(err.response?.data?.detail || 'Failed to disconnect Google Calendar.');
    } finally {
      setDisconnecting(false);
    }
  };

  useEffect(() => {
    let isMounted = true;
    api.get('/users/me')
      .then(({ data }) => {
        if (!isMounted) return;
        setBotDisplayName(data.bot_display_name);
        setInstructions(data.ask_ai_instructions ?? '');
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

  // Saved on its own: PATCH /users/me changes only the fields it is sent.
  const handleInstructionsSubmit = async (e) => {
    e.preventDefault();
    setSavingInstructions(true);
    setInstructionsError(null);
    setInstructionsSaved(false);
    try {
      const { data } = await api.patch('/users/me', { ask_ai_instructions: instructions });
      setInstructions(data.ask_ai_instructions ?? '');
      setInstructionsSaved(true);
      setTimeout(() => setInstructionsSaved(false), 2500);
    } catch (err) {
      console.error('Failed to save Ask AI instructions', err);
      setInstructionsError(err.response?.data?.detail || 'Failed to save. Please try again.');
    } finally {
      setSavingInstructions(false);
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

          {/* Ask AI */}
          <div className="bg-surface border border-border-strong rounded-2xl p-6 flex flex-col gap-5">
            <div className="flex items-center gap-3">
              <div className="bg-brand-blue/10 p-2.5 rounded-xl">
                <Sparkles className="w-5 h-5 text-brand-blue" />
              </div>
              <div>
                <h2 className="text-[15px] font-bold text-brand-dark">Ask AI custom instructions</h2>
                <p className="text-xs text-muted">
                  What Ask AI should know about you and how you like answers - your role, your team, a preferred format.
                </p>
              </div>
            </div>

            {loading ? (
              <div className="flex justify-center py-6">
                <Loader2 className="w-5 h-5 animate-spin text-brand-blue" />
              </div>
            ) : (
              <form onSubmit={handleInstructionsSubmit} className="flex flex-col gap-4">
                <div>
                  <textarea
                    rows={5}
                    maxLength={ASK_AI_INSTRUCTIONS_MAX}
                    value={instructions}
                    onChange={(e) => setInstructions(e.target.value)}
                    placeholder="e.g. I lead the platform team. Keep answers short and list action items first."
                    className="w-full resize-y rounded-xl border border-border bg-surface px-4 py-3 text-base text-brand-dark placeholder:text-muted transition-colors focus:border-brand-blue focus:outline-none tablet:text-sm"
                  />
                  <div className="mt-1 flex justify-between gap-3 text-xs text-muted">
                    <span>Used for tone and context. Answers still come only from your meetings.</span>
                    <span className="flex-none tabular-nums">{instructions.length}/{ASK_AI_INSTRUCTIONS_MAX}</span>
                  </div>
                </div>

                {instructionsError && <p className="text-sm text-red-600 dark:text-red-400">{instructionsError}</p>}

                <div className="flex items-center gap-3">
                  <button
                    type="submit"
                    disabled={savingInstructions}
                    className="flex items-center px-4 py-2.5 bg-brand-blue text-white font-medium text-sm rounded-xl hover:opacity-90 disabled:opacity-50 transition-opacity"
                  >
                    {savingInstructions ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : null}
                    Save instructions
                  </button>
                  {instructionsSaved && (
                    <span className="flex items-center gap-1.5 text-sm font-medium text-status-done-fg">
                      <Check className="w-4 h-4" /> Saved
                    </span>
                  )}
                </div>
              </form>
            )}
          </div>

          {/* Calendar */}
          <div className="bg-surface border border-border-strong rounded-2xl p-6 flex flex-col gap-4">
            <div className="flex items-center gap-3">
              <div className="bg-status-muted-bg p-2.5 rounded-xl">
                <CalendarDays className="w-5 h-5 text-status-muted-fg" />
              </div>
              <div>
                <h2 className="text-[15px] font-bold text-brand-dark">Connected calendar</h2>
                <p className="text-xs text-muted">Opt individual events into recording from the Dashboard.</p>
              </div>
            </div>

            {justConnected && (
              <div className="flex items-center gap-2 px-3.5 py-2.5 rounded-xl bg-status-done-bg text-status-done-fg text-sm font-medium">
                <Check className="w-4 h-4 shrink-0" /> Google Calendar connected.
              </div>
            )}
            {calendarError && <p className="text-sm text-red-600 dark:text-red-400">{calendarError}</p>}

            <div className="flex flex-col gap-2.5">
              <div className="flex items-center gap-3.5 p-3.5 border border-border rounded-xl">
                <span className="w-8.5 h-8.5 rounded-lg bg-status-done-bg text-status-done-fg text-[11px] font-extrabold flex items-center justify-center shrink-0">
                  GC
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-[14.5px] font-semibold text-brand-dark">Google Calendar</div>
                  <div className="text-xs text-muted truncate">
                    {calendarLoading
                      ? 'Checking…'
                      : calendarStatus.connected
                        ? calendarStatus.google_email
                        : 'Not connected'}
                  </div>
                </div>
                {calendarLoading ? (
                  <Loader2 className="w-4 h-4 animate-spin text-faint shrink-0" />
                ) : calendarStatus.connected ? (
                  <button
                    onClick={handleDisconnectCalendar}
                    disabled={disconnecting}
                    className="px-3 py-1.5 border border-border rounded-lg text-[13px] font-semibold text-faint hover:text-red-600 dark:hover:text-red-400 hover:border-red-200 dark:hover:border-red-500/30 transition-colors disabled:opacity-50 shrink-0"
                  >
                    {disconnecting ? 'Disconnecting…' : 'Disconnect'}
                  </button>
                ) : (
                  <button
                    onClick={handleConnectCalendar}
                    disabled={connecting}
                    className="px-3 py-1.5 bg-brand-blue text-white rounded-lg text-[13px] font-semibold hover:opacity-90 transition-opacity disabled:opacity-50 shrink-0"
                  >
                    {connecting ? 'Connecting…' : 'Connect'}
                  </button>
                )}
              </div>

              <div className="flex items-center gap-3.5 p-3.5 border border-dashed border-border rounded-xl cursor-not-allowed opacity-70">
                <span className="w-8.5 h-8.5 rounded-lg bg-status-muted-bg text-status-muted-fg text-[11px] font-extrabold flex items-center justify-center shrink-0">
                  MS
                </span>
                <div className="flex-1">
                  <div className="text-[14.5px] font-semibold text-brand-dark">Outlook Calendar</div>
                  <div className="text-xs text-muted">Not connected</div>
                </div>
                <span className="px-3 py-1.5 border border-border rounded-lg text-[13px] font-semibold text-faint shrink-0">
                  Connect
                </span>
              </div>
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
