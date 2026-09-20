// The handful of facts these pages cannot get from the codebase. They are
// business and legal decisions, not engineering ones, so they live in one
// place rather than being scattered through four documents.
//
// Fill these in and set DRAFT to false before the pages go public. While
// DRAFT is true every legal page carries a visible notice saying so, which is
// the honest state for text that has not been through legal review.
export const DRAFT = true;

export const COMPANY = {
  // e.g. "MeetIQ Technologies Pvt. Ltd." - the entity that is actually the
  // data controller. "MeetIQ" alone is a product name, not a legal person.
  name: 'MeetIQ',
  contactEmail: 'hello@meetiq.com',
  // Country whose law governs the terms, and whose courts hear disputes.
  jurisdiction: 'India',
};

// Bumped by hand when the substance of a page changes, not on every edit.
export const LAST_UPDATED = '20 September 2026';
