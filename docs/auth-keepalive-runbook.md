# auth.json / zoom-auth.json keep-alive — what it is, and what to do when it alerts

## Why this exists

`meeting-bot/auth.json` and `meeting-bot/zoom-auth.json` each hold a
signed-in browser session (cookies) — `GoogleMeetBot`/`ZoomBot` use them to
join meetings without hitting the anonymous "Ask to join" flow (Google) or
being flagged as a bot (Zoom). Both platforms' sessions can go stale
between real meeting joins: Google documents (via its own short-lived
session-rotation cookies) an expiry window of roughly 10-60 minutes;
Zoom's window is less well understood, but was confirmed by direct testing
during this work to also expire — valid on one check, expired a few hours
later on a second check.

Previously, the only thing that refreshed either file was a successful
meeting join (`GoogleMeetBot.join()` / `ZoomBot.join()` write the refreshed
session back right after confirming it's authenticated). If no join
happened on a given platform within its expiry window, that file went
stale, and the *next* join attempt on that platform failed with
`AUTH_EXPIRED` — before it ever got the chance to refresh anything, since
the refresh itself requires already being authenticated. A stale file
could not recover on its own.

`meeting-bot/src/core/AuthKeepAlive.js` fixes the "no join happened
recently" case for **both platforms**: on a timer (default every 15
minutes, see `AUTH_KEEPALIVE_INTERVAL_MINUTES`), it loads an authenticated
page for Google, then for Zoom, confirms each session is still signed in,
and writes the refreshed cookies back — independent of whether any real
meeting is happening.

## What it can't fix

If a session gets invalidated for a reason unrelated to simple time-based
expiry — a password change, a 2FA re-verification challenge, the platform
flagging the account for suspicious automated activity — keep-alive can't
recover it. Refreshing requires already being authenticated; once a
session is genuinely dead, every keep-alive cycle will detect that and
alert, but none of them can log back in on their own. That part still
requires the manual step below.

## What the alert looks like

In the `meeting-bot` container logs (`docker logs <container>` or wherever
you're aggregating them), a dead session produces this block, repeating
every keep-alive interval until fixed (shown here for Google; Zoom's is
identical with the label and fix command swapped):

```
======================================================================
[AuthKeepAlive] ALERT: Google auth session has EXPIRED.
[AuthKeepAlive] Every Google join will fail until this is fixed.
[AuthKeepAlive] Fix: run `node generate-auth.cjs` and sign in manually.
[AuthKeepAlive] See docs/auth-keepalive-runbook.md for details.
======================================================================
```

You'll also see this same failure mode surface independently as a real
join failing with an error starting `AUTH_EXPIRED:` (Google) or the
equivalent Zoom bot-blocked error — that's the same underlying problem,
just discovered by a real user's meeting instead of the keep-alive job.

There is currently no alerting channel wired up beyond this log output (no
Slack/email/webhook) — someone needs to be watching the logs, or you need
to point your log aggregation's alerting at the `[AuthKeepAlive] ALERT`
string, for this to actually reach a person.

## How to fix it

1. On a machine with a real display (not inside the Linux container —
   `generate-auth.cjs`/`generate-zoom-auth.cjs` are documented as
   local-dev-only and open an interactive, headed Chrome window):
   ```
   cd meeting-bot
   node generate-auth.cjs        # for Google
   node generate-zoom-auth.cjs   # for Zoom
   ```
2. Sign into the account the bot uses, in the Chrome window that opens.
3. The script overwrites `auth.json` (or `zoom-auth.json`) with a fresh,
   valid session.
4. If running via `docker-compose`, both files are bind-mounted into the
   container (`docker-compose.yml`), so the running container picks up the
   new file immediately — no restart needed. The next keep-alive cycle (or
   the next real join) will confirm it's valid again.

## Notes

- Disable the job entirely with `AUTH_KEEPALIVE_ENABLED=false` (e.g. for
  local Windows dev, where it otherwise pops up two real Chrome windows —
  one per platform — every interval).
- Both platforms are checked sequentially in the same cycle, not in
  parallel, to avoid stacking two extra headed Chrome instances onto the
  shared Xvfb display at once, on top of whatever real meetings may be
  running concurrently.
- Longer-term alternative considered and not pursued now: Zoom has an
  official bot/SDK API that would avoid this whole class of problem for
  Zoom (though as of March 2026 it also requires per-meeting authorization
  via a ZAK/OBF token or RTMS for joining meetings outside your own
  account — not simply a drop-in swap). Google Meet has no equivalent
  public API for a third-party bot to join an arbitrary user's meeting, so
  this keep-alive approach is the realistic fix for Google specifically,
  not a stopgap for something better that exists today.
