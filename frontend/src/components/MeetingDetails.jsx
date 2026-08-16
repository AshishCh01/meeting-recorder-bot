import React, { useState } from 'react';
import { Calendar, Clock, Video, ListTodo, FileText, FileAudio } from 'lucide-react';

export const MeetingDetails = ({ meeting }) => {
  const [activeTab, setActiveTab] = useState('summary');

  const tabs = [
    { id: 'summary', label: 'Summary', icon: FileText },
    { id: 'action_items', label: 'Action Items', icon: ListTodo },
    { id: 'transcript', label: 'Transcript', icon: FileAudio },
  ];

  const renderTranscript = (transcriptText) => {
    if (!transcriptText) return <p className="text-slate-500 italic">No transcript available.</p>;

    // Attempt basic parsing assuming standard speaker formats like "Speaker 1: Hello"
    const lines = transcriptText.split('\n');
    return (
      <div className="space-y-4">
        {lines.map((line, i) => {
          if (!line.trim()) return null;
          
          // Match standard diarization formats
          const match = line.match(/^([^:]+):\s*(.*)/);
          if (match) {
            return (
              <div key={i} className="flex gap-4">
                <div className="flex-shrink-0 w-8 h-8 rounded-full bg-brand-blue/10 flex items-center justify-center">
                  <span className="text-xs font-bold text-brand-blue">{match[1].substring(0, 2).toUpperCase()}</span>
                </div>
                <div>
                  <p className="text-xs font-semibold text-slate-500 mb-1">{match[1]}</p>
                  <p className="text-sm text-brand-dark leading-relaxed">{match[2]}</p>
                </div>
              </div>
            );
          }

          // Fallback if no speaker found
          return <p key={i} className="text-sm text-brand-dark leading-relaxed">{line}</p>;
        })}
      </div>
    );
  };

  const renderContent = () => {
    if (activeTab === 'summary') {
      return (
        <div className="prose prose-slate max-w-none text-sm text-brand-dark">
          {meeting.summary ? (
             <p className="whitespace-pre-wrap">{meeting.summary}</p>
          ) : (
            <p className="text-slate-500 italic">No summary generated yet.</p>
          )}
        </div>
      );
    }
    
    if (activeTab === 'action_items') {
      return (
        <div className="prose prose-slate max-w-none text-sm text-brand-dark">
          {meeting.action_items ? (
             <p className="whitespace-pre-wrap">{meeting.action_items}</p>
          ) : (
            <p className="text-slate-500 italic">No action items found.</p>
          )}
        </div>
      );
    }
    
    if (activeTab === 'transcript') {
      return renderTranscript(meeting.transcript);
    }
  };

  return (
    <div className="flex flex-col h-full bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
      {/* Header */}
      <div className="px-6 py-5 border-b border-slate-100 bg-slate-50/50">
        <h2 className="text-xl font-bold text-brand-dark mb-2">Meeting Details</h2>
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
