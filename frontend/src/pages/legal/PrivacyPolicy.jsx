import React from 'react';
import { Link } from 'react-router-dom';
import { H2, LegalPage, P, Term, UL } from '../../components/legal/LegalPage';
import { COMPANY } from './company';

export const PrivacyPolicy = () => (
  <LegalPage
    title="Privacy policy"
    summary={`How ${COMPANY.name} handles the personal data you and your meeting participants entrust to it.`}
  >
    <H2>Who we are</H2>
    <P>
      {COMPANY.name} provides MeetIQ, a service that records meetings you ask it to join and turns them into
      transcripts, summaries and action items. For the data you put into MeetIQ, {COMPANY.name} is the data
      controller. You can reach us at{' '}
      <a href={`mailto:${COMPANY.contactEmail}`} className="font-bold text-accent-ink hover:opacity-80">
        {COMPANY.contactEmail}
      </a>
      .
    </P>

    <H2>What we collect</H2>
    <P>
      A precise, itemised list is on{' '}
      <Link to="/what-we-store" className="font-bold text-accent-ink hover:opacity-80">
        What we store
      </Link>
      . In summary: your account email, the settings you choose, the audio of meetings you record, the
      transcripts and summaries derived from that audio, your chats about them, and — only if you connect it — an
      access token for your Google Calendar.
    </P>

    <H2>Why we process it</H2>
    <UL>
      <li>
        <Term>To provide the service</Term> — joining the call you asked for, recording it, transcribing it and
        answering your questions about it. This is the performance of our contract with you.
      </li>
      <li>
        <Term>To keep the service working and secure</Term> — diagnosing failures, preventing abuse, and
        enforcing usage limits. This is our legitimate interest in running a reliable service.
      </li>
      <li>
        <Term>To meet legal obligations</Term> where they apply to us.
      </li>
    </UL>
    <P>
      We do not process your meetings for advertising, profiling, or to train machine-learning models — ours or
      anyone else&apos;s.
    </P>

    <H2>Other people in your meetings</H2>
    <P>
      A recorded meeting contains other people&apos;s voices and words. MeetIQ joins as a visible participant
      under a name you choose, so it can be seen in the participant list, and it never emails or otherwise
      contacts anyone in the call.
    </P>
    <P>
      <Term>
        You are responsible for telling participants that the meeting is being recorded and for obtaining their
        consent where the law requires it.
      </Term>{' '}
      Recording law varies by country and, in some places, by state. MeetIQ gives you the tool; it does not give
      you permission.
    </P>

    <H2>Who we share it with</H2>
    <P>
      We use processors to run the service, each bound to handle the data only on our instructions: Supabase for
      authentication, the database and audio storage; Google for transcription, summarisation and Ask AI answers
      via its Gemini models; and Groq as a standby chat provider when configured. We do not sell personal data.
    </P>
    <P>
      These providers operate internationally, so your data may be processed outside your country. We may also
      disclose data where we are legally required to, or to establish or defend legal claims.
    </P>

    <H2>How long we keep it</H2>
    <P>
      Meetings and their transcripts are kept until you delete them or close your account — we do not expire them
      on a schedule. Deletion is described on{' '}
      <Link to="/data-deletion" className="font-bold text-accent-ink hover:opacity-80">
        Data deletion
      </Link>
      , including the short window in which deleted rows can persist in routine backups.
    </P>

    <H2>Security</H2>
    <P>
      Recordings are held in a private bucket, reachable in the app only through short-lived signed links rather
      than public URLs, and database access is scoped so one account cannot read another&apos;s rows. No service
      can promise perfect security, and we do not.
    </P>

    <H2>Your rights</H2>
    <P>
      Depending on where you live, you may have the right to access the personal data we hold about you, correct
      it, delete it, receive a copy of it, or object to or restrict some processing. You can exercise most of
      these in the app directly; for anything else, email{' '}
      <a href={`mailto:${COMPANY.contactEmail}`} className="font-bold text-accent-ink hover:opacity-80">
        {COMPANY.contactEmail}
      </a>
      . If you believe we have handled your data badly, you may also complain to your local data protection
      authority.
    </P>

    <H2>Children</H2>
    <P>MeetIQ is not intended for, and may not be used by, anyone under 16.</P>

    <H2>Changes</H2>
    <P>
      If we change this policy in a way that materially affects you, we will say so in the product before the
      change takes effect, not only by editing this page.
    </P>
  </LegalPage>
);
