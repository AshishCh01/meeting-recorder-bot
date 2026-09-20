import React from 'react';
import { Link } from 'react-router-dom';
import { H2, LegalPage, P, Term, UL } from '../../components/legal/LegalPage';

// Every claim on this page is checked against the code: the storage path and
// bucket in app/services/storage_service.py and app/api/meetings.py, the
// tables in backend/alembic/versions, and the model settings in app/config.py.
export const WhatWeStore = () => (
  <LegalPage
    title="What we store"
    summary="The plain-English version of the privacy policy: exactly what MeetIQ keeps, where it keeps it, and who else sees it."
  >
    <H2>Your account</H2>
    <P>
      An email address and a password, held by our authentication provider. We never see your password. Alongside
      it we keep two settings you control: the <Term>display name the bot uses</Term> when it joins a call, and any{' '}
      <Term>standing instructions</Term> you give Ask AI.
    </P>

    <H2>Your meetings</H2>
    <UL>
      <li>
        <Term>Audio.</Term> One audio file per recorded meeting, stored in a private bucket under your own user
        ID. No video is captured at any point. Playback in the app uses short-lived signed links rather than a
        public URL.
      </li>
      <li>
        <Term>The transcript and what we derive from it.</Term> The speaker-labelled transcript, the summary, the
        key points and the action items.
      </li>
      <li>
        <Term>Meeting details.</Term> The meeting link you submitted, the platform, the title, when it ran, how
        long it lasted, and its current status.
      </li>
      <li>
        <Term>Search index.</Term> The transcript is split into passages and stored with numeric embeddings, so
        Ask AI can find the right moment across meetings. These are derived from your transcript and are deleted
        with it.
      </li>
      <li>
        <Term>Chats.</Term> The questions you ask about a meeting, and your Ask AI conversations, with the answers.
      </li>
    </UL>

    <H2>Your calendar, if you connect it</H2>
    <P>
      Connecting Google Calendar stores an access token so we can read your upcoming events and show them in one
      list. Connecting it does not record anything. A meeting is only recorded once you switch on that specific
      event. You can disconnect at any time from Settings, which removes the stored token.
    </P>

    <H2>Who else sees it</H2>
    <P>
      We use a small number of processors, and only for the jobs described here. We do not sell your data, and we
      do not use your meetings to train anyone&apos;s models.
    </P>
    <UL>
      <li>
        <Term>Supabase</Term> — authentication, the database and the audio storage bucket.
      </li>
      <li>
        <Term>Google (Gemini)</Term> — transcription, summaries, and answering your Ask AI questions.
      </li>
      <li>
        <Term>Groq</Term> — a standby for answering questions if the primary model is unavailable. It is only
        contacted when that fallback is configured and needed.
      </li>
    </UL>

    <H2>What we do not do</H2>
    <UL>
      <li>No video, ever — the bot captures audio only.</li>
      <li>No emails to the other people in your call. MeetIQ contacts nobody on your behalf.</li>
      <li>No engagement scores, talk-time ratios or other measurements of the people in the meeting.</li>
      <li>No recording of a meeting you have not explicitly asked for or switched on.</li>
    </UL>

    <H2>Getting rid of it</H2>
    <P>
      Deleting a meeting removes its audio, transcript, search passages and chat together. See{' '}
      <Link to="/data-deletion" className="font-bold text-accent-ink hover:opacity-80">
        Data deletion
      </Link>{' '}
      for how to delete a single meeting, your chats, or your whole account.
    </P>
  </LegalPage>
);
