import { createContext, useContext } from 'react';
import { useMeetingChat } from '../hooks/useMeetingChat';

// Holds one meeting's chat state for the whole view.
//
// MeetingView renders both chat surfaces at once - the desktop column and the
// mobile drag-up sheet - and only hides one with CSS, so both are always
// mounted. When each called useMeetingChat() directly they got their own
// useState, i.e. two independent conversations for the same meeting: a
// question asked on desktop never appeared in the mobile sheet's history, and
// vice versa. Owning the state here and having both surfaces read it means
// there is exactly one conversation per meeting, whichever surface is visible.
const MeetingChatContext = createContext(null);

export function MeetingChatProvider({ meetingId, children }) {
  const chat = useMeetingChat(meetingId);
  return (
    <MeetingChatContext.Provider value={chat}>
      {children}
    </MeetingChatContext.Provider>
  );
}

export function useMeetingChatContext() {
  const chat = useContext(MeetingChatContext);
  if (!chat) {
    throw new Error('useMeetingChatContext must be used inside a <MeetingChatProvider>');
  }
  return chat;
}
