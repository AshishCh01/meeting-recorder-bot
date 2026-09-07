/**
 * Resolves the first selector in an ordered fallback list that is actually
 * visible on the page.
 *
 * Google reworks the Meet pre-join UI without notice - renaming aria-labels,
 * rewording buttons, swapping <button> for <div role="button">. Any one of
 * those breaks a single hard-coded selector outright, and the only signal
 * today is a join that starts timing out for every user at once.
 *
 * Lists are ordered most-specific first. When a later tier is what matched,
 * that gets logged as SELECTOR-HEAL: the join still succeeds, but the fact
 * that the primary selector has gone stale shows up in the logs while there
 * are still working fallbacks left, instead of only surfacing once every
 * tier has broken.
 *
 * Returns null rather than throwing - the caller decides whether a miss is
 * fatal (the join button) or expected (the name field, which an
 * authenticated session doesn't render at all).
 */
export async function findFirstVisible(page, selectors, { timeout = 10000, label = 'element', pollMs = 500 } = {}) {
  const deadline = Date.now() + timeout;

  for (;;) {
    for (let tier = 0; tier < selectors.length; tier++) {
      const locator = page.locator(selectors[tier]).first();
      const visible = await locator.isVisible().catch(() => false);
      if (visible) {
        if (tier > 0) {
          console.warn(
            `[SELECTOR-HEAL] ${label}: tier 0 no longer matches, fell back to tier ${tier} ` +
            `("${selectors[tier]}"). Update the selector list before the remaining tiers break too.`
          );
        }
        return locator;
      }
    }

    if (Date.now() >= deadline) return null;
    await page.waitForTimeout(pollMs);
  }
}
