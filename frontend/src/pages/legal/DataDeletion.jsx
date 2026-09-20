import React from 'react';
import { Link } from 'react-router-dom';
import { H2, LegalPage, P, Term, UL } from '../../components/legal/LegalPage';
import { COMPANY } from './company';

export const DataDeletion = () => (
  <LegalPage
    title="Data deletion"
    summary="How to remove a recording, your chat history, or your entire account — and what happens when you do."
  >
    <H2>Delete one meeting</H2>
    <P>
      Open the meeting and choose <Term>Delete meeting</Term> from the menu beside Export, or use the menu on its
      row in the meetings list. Deleting is immediate and cannot be undone. It removes, together:
    </P>
    <UL>
      <li>the audio file;</li>
      <li>the transcript, summary, key points and action items;</li>
      <li>the passages and embeddings that made the meeting searchable in Ask AI;</li>
      <li>the chat you had about that meeting.</li>
    </UL>
    <P>
      If the bot is still in the call when you delete, we ask it to leave first, so a meeting you have just
      deleted does not carry on being recorded.
    </P>

    <H2>Delete your Ask AI chats</H2>
    <P>
      On the Ask AI page, each chat&apos;s menu has <Term>Delete</Term>, and{' '}
      <Term>Delete all chats</Term> at the foot of the list removes every one of them. This removes the
      conversations and their messages. It does not touch your meetings.
    </P>

    <H2>Disconnect your calendar</H2>
    <P>
      Settings has a <Term>Disconnect</Term> control for Google Calendar. It deletes the stored access token, so
      we can no longer read your events. Meetings already recorded are unaffected — delete those separately if
      you want them gone.
    </P>

    <H2>Delete your account</H2>
    <P>
      There is no self-service account deletion in the app yet. Email{' '}
      <a href={`mailto:${COMPANY.contactEmail}`} className="font-bold text-accent-ink hover:opacity-80">
        {COMPANY.contactEmail}
      </a>{' '}
      from the address on the account and we will delete the account together with every meeting, recording,
      transcript, search passage and chat belonging to it. We will confirm when it is done.
    </P>

    <H2>What may survive a deletion, briefly</H2>
    <P>
      Deleted rows can persist for a short period in routine encrypted backups of the database before those
      backups age out. Server logs may retain operational records — such as that a request happened, and when —
      which do not contain your transcripts or audio. Neither is used to reconstruct deleted content.
    </P>

    <H2>Questions</H2>
    <P>
      Write to{' '}
      <a href={`mailto:${COMPANY.contactEmail}`} className="font-bold text-accent-ink hover:opacity-80">
        {COMPANY.contactEmail}
      </a>
      . If you want to know what is held before deciding,{' '}
      <Link to="/what-we-store" className="font-bold text-accent-ink hover:opacity-80">
        What we store
      </Link>{' '}
      lists it.
    </P>
  </LegalPage>
);
