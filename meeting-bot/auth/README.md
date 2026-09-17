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

There are **two generator scripts, not four** — `generate-auth.cjs` for Google
and `generate-zoom-auth.cjs` for Zoom. You run each one once per host, so four
runs fill the four files. Nothing in either script is edited between runs.

Each run is configured entirely by three environment variables:

| Variable | What it decides |
|---|---|
| `GOOGLE_USER_DATA_DIR` / `ZOOM_USER_DATA_DIR` | which Chrome user-data directory to launch |
| `GOOGLE_PROFILE_DIR` / `ZOOM_PROFILE_DIR` | which profile inside it — **this is the account** |
| `AUTH_STATE_PATH` / `ZOOM_AUTH_STATE_PATH` | which host's file gets written |

**None of them has a default, deliberately.** The profile pair used to be two
hardcoded lines in each script with a commented-out alternate pair underneath,
and you picked a host by editing the source. That had no error path, and it was
wrong in git: the committed default was bot-b's profile, so the documented
bot-a command captured bot-b's Google account and wrote it into bot-a's file.
Both hosts then share one login — the exact failure C4 exists to remove — and
nothing reports it, because each script's closing cookie check proves that *a*
session was captured, never whose. Omitting any variable now prints the correct
command and exits rather than guessing.

Run from `meeting-bot/`, on a machine with a desktop — these open a real Chrome
window for you to sign in. The generators write straight to the path the bot
will read, so there is no copy step and no chance of the two disagreeing.

### bash

```bash
# Google — bot-a
GOOGLE_USER_DATA_DIR='C:\chrome-bot-profile' GOOGLE_PROFILE_DIR='Profile 1' AUTH_STATE_PATH='auth/bot-a/auth.json' node generate-auth.cjs

# Google — bot-b
GOOGLE_USER_DATA_DIR='C:\chrome-bot-b-profile' GOOGLE_PROFILE_DIR='Default' AUTH_STATE_PATH='auth/bot-b/auth.json' node generate-auth.cjs

# Zoom — bot-a
ZOOM_USER_DATA_DIR='C:\chrome-bot-profile-zoom' ZOOM_PROFILE_DIR='Profile 1' ZOOM_AUTH_STATE_PATH='auth/bot-a/zoom-auth.json' node generate-zoom-auth.cjs

# Zoom — bot-b
ZOOM_USER_DATA_DIR='C:\chrome-bot-b-profile-zoom' ZOOM_PROFILE_DIR='Default' ZOOM_AUTH_STATE_PATH='auth/bot-b/zoom-auth.json' node generate-zoom-auth.cjs
```

### PowerShell

```powershell
# Google — bot-a
$env:GOOGLE_USER_DATA_DIR="C:\chrome-bot-profile"; $env:GOOGLE_PROFILE_DIR="Profile 1"; $env:AUTH_STATE_PATH="auth/bot-a/auth.json"; node generate-auth.cjs

# Google — bot-b
$env:GOOGLE_USER_DATA_DIR="C:\chrome-bot-b-profile"; $env:GOOGLE_PROFILE_DIR="Default"; $env:AUTH_STATE_PATH="auth/bot-b/auth.json"; node generate-auth.cjs

# Zoom — bot-a
$env:ZOOM_USER_DATA_DIR="C:\chrome-bot-profile-zoom"; $env:ZOOM_PROFILE_DIR="Profile 1"; $env:ZOOM_AUTH_STATE_PATH="auth/bot-a/zoom-auth.json"; node generate-zoom-auth.cjs

# Zoom — bot-b
$env:ZOOM_USER_DATA_DIR="C:\chrome-bot-b-profile-zoom"; $env:ZOOM_PROFILE_DIR="Default"; $env:ZOOM_AUTH_STATE_PATH="auth/bot-b/zoom-auth.json"; node generate-zoom-auth.cjs
```

The profile paths above are the ones this install uses; substitute your own if
they differ. `$env:VAR` assignments persist for the rest of the PowerShell
session, so set all three every time rather than relying on what a previous
command left behind.

### Check before you sign in

Add `--dry-run` to any of the commands above. It validates the configuration,
prints the profile and the destination it resolved, and exits without launching
Chrome or writing anything:

```bash
GOOGLE_USER_DATA_DIR='C:\chrome-bot-profile' GOOGLE_PROFILE_DIR='Profile 1' AUTH_STATE_PATH='auth/bot-a/auth.json' node generate-auth.cjs --dry-run
```

Worth doing before every real capture — a mismatched profile otherwise costs a
full interactive sign-in to discover, and only if you happen to notice.

A real run prints the same three lines, then asks you to confirm the signed-in
account against the destination path before it writes. **Sign into a different
account for each host** — that is the entire point of C4. Two hosts on one
Google login means two concurrent sessions rotating the same short-lived
cookies against each other, which is the failure this phase exists to remove.

### When a session expires

`AuthKeepAlive` refreshes these in place every 15 minutes, so this is only for
when its `ALERT` fires (see `docs/auth-keepalive-runbook.md`). Re-run the one
command for the host and platform that expired — the same two scripts, no
source editing, nothing to uncomment. If all four expire at once, run all four
commands one after another. Close Chrome fully first (`taskkill /F /IM
chrome.exe`); `launchPersistentContext` cannot attach to a running browser.

If the user-data directory does not exist, Chrome creates an empty one and you
are signing in from scratch rather than reusing a profile. The scripts warn
when they spot this, because it is usually a typo in the path.

## Single-host installs

**At runtime, nothing here applies.** With `AUTH_STATE_PATH` /
`ZOOM_AUTH_STATE_PATH` unset, `resolveAuthStatePath` falls back to
`meeting-bot/auth.json` and `meeting-bot/zoom-auth.json` exactly as it always
has, and this directory can stay empty.

**The generators are stricter than the runtime**, and that asymmetry is
intentional: point them at the repo-root files explicitly rather than relying
on the fallback.

```bash
GOOGLE_USER_DATA_DIR='C:\chrome-bot-profile' GOOGLE_PROFILE_DIR='Profile 1' AUTH_STATE_PATH='auth.json' node generate-auth.cjs
ZOOM_USER_DATA_DIR='C:\chrome-bot-profile-zoom' ZOOM_PROFILE_DIR='Profile 1' ZOOM_AUTH_STATE_PATH='zoom-auth.json' node generate-zoom-auth.cjs
```

A silent fallback is right for the runtime — it keeps every existing
single-host install working across an upgrade, and it fails at boot rather than
mid-meeting if it is wrong. It is wrong for a generator, where the same silence
means an interactive sign-in you cannot undo has just written an account to a
path you did not choose.

## Health

Each bot reports, per platform, whether its session is alive, on
`GET /capacity` (`auth: {google: {...}, zoom: {...}}`). The backend's registry
will not dispatch a Google meeting to a host whose Google session is expired,
but will still send it Zoom meetings — the two identities expire independently.
`AuthKeepAlive` re-checks every 15 minutes and logs an ALERT naming the fix; see
`docs/auth-keepalive-runbook.md`.
