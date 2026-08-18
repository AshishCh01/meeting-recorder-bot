import React, { useState } from 'react';
import { Calendar, Clock, Video, ListTodo, FileText, FileAudio, User, AlertCircle, RefreshCw, Loader2, Download } from 'lucide-react';
import api from '../lib/api';

export const MeetingDetails = ({ meeting, onRetry }) => {
  const [activeTab, setActiveTab] = useState('summary');
  const [isRetrying, setIsRetrying] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);

  const handleDownloadPDF = async () => {
    setIsDownloading(true);
    try {
      const response = await api.get(`/meetings/${meeting.id}/export-pdf`, {
        responseType: 'blob'
      });
      
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `meeting-summary-${meeting.id}.pdf`);
      document.body.appendChild(link);
      link.click();
      link.parentNode.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (error) {
      console.error("Error downloading PDF:", error);
      alert("Failed to download PDF. Please try again.");
    } finally {
      setIsDownloading(false);
    }
  };

  const handleRetryClick = async () => {
    setIsRetrying(true);
    if (onRetry) {
      await onRetry();
    }
    setIsRetrying(false);
  };

  const tabs = [
    { id: 'summary', label: 'Summary', icon: FileText },
    { id: 'action_items', label: 'Action Items', icon: ListTodo },
    { id: 'transcript', label: 'Transcript', icon: FileAudio },
  ];

  const transcriptData = meeting?.transcript || {};
  const { summary, key_points, conclusion, action_items, conversation } = transcriptData;

  const renderTranscript = () => {
    if (!conversation || !Array.isArray(conversation) || conversation.length === 0) {
      return <p className="text-slate-500 italic">No transcript available.</p>;
    }

    return (
      <div className="space-y-4">
        {conversation.map((seg, i) => {
          if (!seg.text) return null;
          
          let speaker = "Unknown";
          let text = seg.text;
          const match = seg.text.match(/^([^:-]+)[:\-]\s*(.*)/);
          if (match) {
            speaker = match[1].trim();
            text = match[2].trim();
          }

          return (
            <div key={i} className="flex gap-4">
              <div className="flex-shrink-0 w-8 h-8 rounded-full bg-brand-blue/10 flex items-center justify-center mt-1">
                <span className="text-xs font-bold text-brand-blue">
                  {speaker.substring(0, 2).toUpperCase()}
                </span>
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-2 mb-1">
                  <p className="text-xs font-semibold text-slate-700 truncate">{speaker}</p>
                  <span className="text-[10px] text-slate-400 font-mono flex-shrink-0">
                    {seg.timestamp_start}
                  </span>
                </div>
                <p className="text-sm text-brand-dark leading-relaxed break-words">{text}</p>
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  const renderContent = () => {
    if (activeTab === 'summary') {
      return (
        <div className="space-y-6">
          {summary ? (
            <div>
              <h3 className="text-sm font-semibold text-slate-800 mb-2">Overview</h3>
              <p className="text-sm text-brand-dark leading-relaxed">{summary}</p>
            </div>
          ) : (
            <p className="text-slate-500 italic">No summary generated yet.</p>
          )}

          {key_points && key_points.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold text-slate-800 mb-2">Key Points</h3>
              <ul className="list-disc pl-5 space-y-1">
                {key_points.map((pt, i) => (
                  <li key={i} className="text-sm text-brand-dark">{pt}</li>
                ))}
              </ul>
            </div>
          )}

          {conclusion && (
            <div>
              <h3 className="text-sm font-semibold text-slate-800 mb-2">Conclusion</h3>
              <p className="text-sm text-brand-dark leading-relaxed">{conclusion}</p>
            </div>
          )}
        </div>
      );
    }
    
    if (activeTab === 'action_items') {
      return (
        <div>
          {action_items && action_items.length > 0 ? (
            <div className="space-y-3">
              {action_items.map((item, i) => (
                <div key={i} className="bg-slate-50 border border-slate-100 rounded-lg p-4 flex gap-3">
                  <div className="mt-0.5">
                    <ListTodo className="w-4 h-4 text-brand-blue" />
                  </div>
                  <div>
                    <p className="text-sm font-medium text-brand-dark mb-1">{item.item}</p>
                    <div className="flex items-center gap-3 text-xs text-slate-500">
                      <span className="flex items-center gap-1">
                        <User className="w-3 h-3" /> {item.owner}
                      </span>
                      {item.timestamp && <span className="font-mono">{item.timestamp}</span>}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-slate-500 italic">No action items found.</p>
          )}
        </div>
      );
    }
    
    if (activeTab === 'transcript') {
      return renderTranscript();
    }
  };

  return (
    <div className="flex flex-col h-full bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      {/* Header */}
      <div className="px-6 py-5 border-b border-slate-100 bg-slate-50/50">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-xl font-bold text-brand-dark">Meeting Details</h2>
          {meeting.status === 'completed' && (
            <button
              onClick={handleDownloadPDF}
              disabled={isDownloading}
              className="flex items-center gap-2 px-3 py-1.5 bg-white border border-slate-200 text-sm font-medium text-slate-700 rounded-lg hover:bg-slate-50 shadow-sm disabled:opacity-50 transition-colors"
            >
              {isDownloading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Download className="w-4 h-4" />
              )}
              {isDownloading ? 'Generating...' : 'Download PDF'}
            </button>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-4 text-xs text-slate-500">
          {meeting.created_at && (
            <div className="flex items-center gap-1.5">
              <Calendar className="w-4 h-4" />
              {new Date(meeting.created_at).toLocaleDateString()}
            </div>
          )}
          {meeting.duration_seconds && (
            <div className="flex items-center gap-1.5">
              <Clock className="w-4 h-4" />
              {Math.round(meeting.duration_seconds / 60)} minutes
            </div>
          )}
          {meeting.platform && (
            <div className="flex items-center gap-1.5 capitalize">
              <Video className="w-4 h-4" />
              {meeting.platform}
            </div>
          )}
        </div>
        
        {/* Secure Audio Playback Player */}
        {meeting.audio_playback_url && (
          <div className="mt-5 border-t border-slate-100 pt-4">
            <audio controls src={meeting.audio_playback_url} className="w-full h-10 outline-none" />
          </div>
        )}

        {/* Retry Processing Block */}
        {meeting.status === 'failed' && (
          <div className="mt-5 border-t border-slate-100 pt-4">
            <div className="p-4 bg-red-50 border border-red-100 rounded-lg flex items-start gap-3">
              <AlertCircle className="w-5 h-5 text-red-500 mt-0.5" />
              <div className="flex-1">
                <h3 className="text-sm font-semibold text-red-800">Processing Failed</h3>
                <p className="text-sm text-red-600 mt-1">{meeting.error_message || "An unknown error occurred during transcription."}</p>
              </div>
              <button
                onClick={handleRetryClick}
                disabled={isRetrying}
                className="px-4 py-2 bg-white text-red-600 text-sm font-medium border border-red-200 rounded-lg shadow-sm hover:bg-red-50 disabled:opacity-50 flex items-center gap-2"
              >
                <RefreshCw className={`w-4 h-4 ${isRetrying ? 'animate-spin' : ''}`} />
                {isRetrying ? 'Retrying...' : 'Retry Processing'}
              </button>
            </div>
          </div>
        )}
        {/* Processing State Block */}
        {['joining', 'recording', 'transcribing'].includes(meeting.status) && (
          <div className="mt-5 p-6 bg-blue-50 border border-blue-100 rounded-xl flex flex-col items-center justify-center text-center">
            <Loader2 className="w-8 h-8 text-brand-blue animate-spin mb-3" />
            <h3 className="text-sm font-semibold text-brand-dark mb-1">
              {meeting.status === 'joining' ? 'Bot is joining the meeting...' :
               meeting.status === 'recording' ? 'Recording in progress...' :
               'Transcribing and generating insights...'}
            </h3>
            <p className="text-xs text-slate-500">This page will update automatically when finished.</p>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex border-b border-slate-100 px-6">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-4 text-sm font-medium border-b-2 transition-colors ${
                isActive 
                  ? 'border-brand-blue text-brand-blue' 
                  : 'border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300'
              }`}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Content Area */}
      <div className="flex-1 overflow-y-auto p-6 bg-white">
        {renderContent()}
      </div>
    </div>
  );
};
