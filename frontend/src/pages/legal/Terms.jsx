import React from 'react';
import { Link } from 'react-router-dom';
import { H2, LegalPage, P, Term, UL } from '../../components/legal/LegalPage';
import { COMPANY } from './company';

export const Terms = () => (
  <LegalPage
    title="Terms of service"
    summary={`The agreement between you and ${COMPANY.name} for using MeetIQ.`}
  >
    <H2>Agreement</H2>
    <P>
      By creating an account or using MeetIQ, you agree to these terms. If you are using MeetIQ for an
      organisation, you confirm you may accept these terms on its behalf.
    </P>

    <H2>The service</H2>
    <P>
      MeetIQ joins meetings you ask it to, records their audio, and produces transcripts, summaries and action
      items you can search and ask questions about. It currently supports Google Meet and Zoom. MeetIQ is in{' '}
      <Term>beta</Term>: it is free to use, features may change, and it may be unavailable from time to time.
    </P>

    <H2>Your account</H2>
    <P>
      Keep your credentials to yourself; you are responsible for what happens under your account. Tell us
      promptly at{' '}
      <a href={`mailto:${COMPANY.contactEmail}`} className="font-bold text-accent-ink hover:opacity-80">
        {COMPANY.contactEmail}
      </a>{' '}
      if you think someone else has access to it. You must be at least 16 years old.
    </P>

    <H2>Recording, consent and the law</H2>
    <P>
      This is the obligation that matters most, so it is stated plainly.{' '}
      <Term>
        You are responsible for having the right to record every meeting you record, and for telling the other
        participants and obtaining their consent where the law requires it.
      </Term>
    </P>
    <P>
      Recording law differs between countries and, in some places, between states — some require every party to
      agree, some only one. MeetIQ joins as a visible participant under a name you choose so it can be seen, but
      visibility is not consent. You must not use MeetIQ to record anyone covertly or unlawfully.
    </P>

    <H2>Acceptable use</H2>
    <P>You agree not to:</P>
    <UL>
      <li>record meetings you have no right to record;</li>
      <li>use MeetIQ to harass, surveil or profile the people in your calls;</li>
      <li>upload or process content that is unlawful, or that infringes someone else&apos;s rights;</li>
      <li>attempt to break, overload or circumvent the service, its limits, or its security;</li>
      <li>resell or repackage the service without our written agreement.</li>
    </UL>
    <P>We may suspend or close an account that breaks these rules.</P>

    <H2>Your content</H2>
    <P>
      Your recordings, transcripts and chats remain yours. You grant us only the permission needed to run the
      service for you: to store that content, to process it, and to pass it to the processors listed in the{' '}
      <Link to="/privacy" className="font-bold text-accent-ink hover:opacity-80">
        privacy policy
      </Link>{' '}
      so they can transcribe and summarise it. We do not use your content to train models.
    </P>

    <H2>AI output</H2>
    <P>
      Transcripts, summaries and answers are generated automatically and{' '}
      <Term>can be wrong</Term> — a name misheard, a point missed, an action item attributed to the wrong person.
      Check anything that matters against the recording before relying on it. MeetIQ is a note-taker, not a
      system of record.
    </P>

    <H2>Availability and changes</H2>
    <P>
      We may change, suspend or discontinue parts of MeetIQ. While it is in beta we do not promise any particular
      uptime. If we discontinue the service, we will give you reasonable notice and a chance to export your data.
    </P>

    <H2>Ending it</H2>
    <P>
      You may stop using MeetIQ and have your account deleted at any time — see{' '}
      <Link to="/data-deletion" className="font-bold text-accent-ink hover:opacity-80">
        Data deletion
      </Link>
      . We may close your account for a material breach of these terms, or if we stop offering the service.
    </P>

    <H2>No warranty, and limits on liability</H2>
    <P>
      MeetIQ is provided &quot;as is&quot;, without warranties of any kind, to the fullest extent the law allows.
      We are not liable for indirect or consequential loss, for lost profits or revenue, or for any loss arising
      from your reliance on automatically generated output, or from a recording you were not entitled to make.
      Nothing here excludes liability that cannot lawfully be excluded.
    </P>

    <H2>Governing law</H2>
    <P>
      These terms are governed by the laws of {COMPANY.jurisdiction}, and its courts have exclusive jurisdiction,
      except where mandatory consumer law gives you the right to bring a claim where you live.
    </P>

    <H2>Contact</H2>
    <P>
      <a href={`mailto:${COMPANY.contactEmail}`} className="font-bold text-accent-ink hover:opacity-80">
        {COMPANY.contactEmail}
      </a>
    </P>
  </LegalPage>
);
