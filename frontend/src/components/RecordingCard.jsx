// import MeetingStatus from './MeetingStatus.jsx';
// import TranscriptView from './TranscriptView.jsx';
// import MeetingChat from './MeetingChat.jsx';
// import { ExternalLink } from 'lucide-react';

// export default function RecordingCard({ meeting }) {
//   return (
//     <div className="border border-[#c2c6d8] rounded-lg px-4 py-3 mb-3 bg-[#ffffff]">
//       <div className="flex justify-between items-center">
//         <div>
//           <div className="font-semibold text-[#0b1c30]">{meeting.platform} meeting</div>
//           <div className="text-sm text-[#424656] break-all">{meeting.meeting_url}</div>
//           {meeting.error_message && (
//             <div className="text-xs text-[#ba1a1a] mt-1">{meeting.error_message}</div>
//           )}
//         </div>

//         <div className="flex items-center gap-3">
//           <MeetingStatus status={meeting.status} />
//           {meeting.status === 'completed' && meeting.recording_url && (
//             <a
//               href={meeting.recording_url}
//               target="_blank"
//               rel="noreferrer"
//               className="flex items-center gap-1 text-sm font-medium text-[#0050cb] hover:text-[#0044CC] transition-colors"
//             >
//               View recording
//               <ExternalLink size={14} />
//             </a>
//           )}
//         </div>
//       </div>

//       <TranscriptView transcript={meeting.transcript} />
//       {meeting.status === 'completed' && <MeetingChat meetingId={meeting.id} />}
//     </div>
//   );
// }
import MeetingStatus from './MeetingStatus.jsx';
import TranscriptView from './TranscriptView.jsx';
import MeetingChat from './MeetingChat.jsx';
import { ExternalLink, Video, MoreVertical, PlayCircle } from 'lucide-react';

const ACCENT_BY_STATUS = {
  failed: '#ba1a1a',
  completed: '#0050cb',
};
const DEFAULT_ACCENT = '#0050cb';

export default function RecordingCard({ meeting }) {
  const accentColor = ACCENT_BY_STATUS[meeting.status] || DEFAULT_ACCENT;
  const isActive = meeting.status !== 'completed' && meeting.status !== 'failed';

  return (
    <div className="group relative bg-[#ffffff] border border-[#c2c6d8] rounded-xl shadow-sm hover:shadow-[0_2px_8px_rgba(0,0,0,0.04)] transition-all duration-200 overflow-hidden mb-3">
      {/* Accent line: always visible while active, fades in on hover otherwise */}
      <div
        className={`absolute left-0 top-0 bottom-0 w-1 rounded-l-xl transition-opacity ${
          isActive ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
        }`}
        style={{ backgroundColor: accentColor }}
      ></div>

      <div className="flex items-center justify-between gap-4 px-6 py-5">
        <div className="flex items-center gap-4 min-w-0">
          <div
            className="w-10 h-10 rounded-lg flex items-center justify-center border border-[#c2c6d8] flex-shrink-0"
            style={{ color: accentColor }}
          >
            <Video size={20} />
          </div>
          <div className="min-w-0">
            <h3 className="text-base font-semibold text-[#0b1c30] group-hover:text-[#0050cb] transition-colors truncate">
              {meeting.platform} meeting
            </h3>
            <p className="text-xs text-[#424656] mt-1 break-all">{meeting.meeting_url}</p>
            {/* {meeting.error_message && (
              <p className="text-xs text-[#ba1a1a] mt-1">{meeting.error_message}</p>
            )} */}
          </div>
        </div>

        <div className="flex items-center gap-3 flex-shrink-0">
          <MeetingStatus status={meeting.status} />

          {meeting.status === 'completed' && meeting.recording_url && (
            <a
              href={meeting.recording_url}
              target="_blank"
              rel="noreferrer"
              className="h-8 px-4 text-xs font-semibold bg-[#ffffff] border border-[#c2c6d8] rounded hover:bg-[#eff4ff] transition-colors flex items-center gap-2 whitespace-nowrap text-[#0b1c30]"
            >
              <PlayCircle size={16} />
              <span className="hidden sm:inline">View recording</span>
              <ExternalLink size={12} />
            </a>
          )}

          <button className="hidden md:flex text-[#424656] hover:text-[#0b1c30] p-1 rounded transition-colors opacity-0 group-hover:opacity-100">
            <MoreVertical size={18} />
          </button>
        </div>
      </div>

      <div className="px-6 pb-5 border-t border-[#c2c6d8]/60 pt-4">
        <TranscriptView transcript={meeting.transcript} />
        {meeting.status === 'completed' && <MeetingChat meetingId={meeting.id} />}
      </div>
    </div>
  );
}