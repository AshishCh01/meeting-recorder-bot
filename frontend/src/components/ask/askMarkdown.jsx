import React from 'react';
import { Link } from 'react-router-dom';

// The only links an Ask AI answer may contain: this app's own meeting pages,
// as the model is told to cite them ("[Title — 15 Sep 2026](/meetings/<id>)").
const MEETING_PATH = /^\/meetings\/[0-9a-f-]{36}$/i;

// ReactMarkdown components for Ask AI answers.
//
// Answers are built from meeting transcripts, and a transcript is text anyone
// in the call could have said - including a markdown link or image aimed at
// the reader. So a link renders as an in-app <Link> only when it points at a
// meeting page, and as plain text otherwise; images render as their alt text,
// so nothing an answer contains can load a remote resource.
export const askMarkdownComponents = {
  // `node` is ReactMarkdown's AST node - not something to put on the DOM.
  // eslint-disable-next-line no-unused-vars
  a: ({ node, href, children }) =>
    MEETING_PATH.test(href || '') ? (
      <Link to={href} className="font-semibold text-brand-blue hover:underline">
        {children}
      </Link>
    ) : (
      <span>{children}</span>
    ),
  img: ({ alt }) => (alt ? <span>{alt}</span> : null),
};
