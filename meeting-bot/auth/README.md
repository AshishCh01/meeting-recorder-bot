# Per-host bot credentials

One directory per recorder in `BOT_HOSTS`, named after its host id
(docs/scaling-plan.md, Phase C4):

```
meeting-bot/auth/
  bot-a/
    auth.json        # Google session for host bot-a
    zoom-auth.json   # Zoom session for host bot-a
  bot-b/
    auth.json        # a DIFFERENT Google account
    zoom-auth.json   # a DIFFERENT Zoom account
```

**Everything in here except this file is gitignored**, and the ignore is on the
directory rather than on the two known filenames. A real `auth.json` leaked into
this repo's history once already (`docs/aws-ec2-deploy.md`); with N hosts there
are now 2N of these files plus whatever backups someone makes while rotating
one, so the rule has to cover `auth.json.bak` and `bot-c/` too — not just the
names that existed when the rule was written. These files are live session
cookies: anyone holding one is signed in as the bot account.

## Generating them

The generators write to whatever path the bot will read, so there is no copy
step and no chance of the two disagreeing. Run from `meeting-bot/`, on a
machine with a desktop — they open a real Chrome window for you to sign in:

```bash
AUTH_STATE_PATH=auth/bot-a/auth.json           node generate-auth.cjs
ZOOM_AUTH_STATE_PATH=auth/bot-a/zoom-auth.json node generate-zoom-auth.cjs

AUTH_STATE_PATH=auth/bot-b/auth.json           node generate-auth.cjs
ZOOM_AUTH_STATE_PATH=auth/bot-b/zoom-auth.json node generate-zoom-auth.cjs
```

Each prints the absolute path it wrote. **Sign into a different account for each
host** — that is the entire point of C4. Two hosts on one Google login means two
concurrent sessions rotating the same short-lived cookies against each other,
which is the failure this phase exists to remove.

`generate-auth.cjs` needs a Chrome profile directory at the path hardcoded near
the top of the script; see the comments in it.

## Single-host installs

Nothing here applies. With `AUTH_STATE_PATH` / `ZOOM_AUTH_STATE_PATH` unset,
`resolveAuthStatePath` falls back to `meeting-bot/auth.json` and
`meeting-bot/zoom-auth.json` exactly as it always has, and this directory can
stay empty.

## Health

Each bot reports, per platform, whether its session is alive, on
`GET /capacity` (`auth: {google: {...}, zoom: {...}}`). The backend's registry
will not dispatch a Google meeting to a host whose Google session is expired,
but will still send it Zoom meetings — the two identities expire independently.
`AuthKeepAlive` re-checks every 15 minutes and logs an ALERT naming the fix; see
`docs/auth-keepalive-runbook.md`.
