# Deploying to AWS — single EC2 instance

Architecture: one EC2 instance running `backend`, `worker`, `redis`, two
recorders (`meeting-bot` and `meeting-bot-2`) and `frontend` via Docker
Compose, same shape as local dev. No ECS, no autoscaling group.

**Why a single instance, and what would change that.** Each recorder keeps its
`activeMeetings` session map and `AuthKeepAlive` job in process memory, so each
needs exactly one long-lived process — that constraint is real, and Phase C of
`docs/scaling-plan.md` is what makes a *pool* of such processes workable: two
containers, two credential sets, and a `BOT_HOSTS` registry the backend
dispatches against. It scales the recorders across containers on one host, not
across hosts. The `backend` no longer has that constraint (chat history is
persisted in `chat_messages` and rehydrates into any process; the scheduler
claims rows atomically), but running it on more than one instance needs a load
balancer and the Phase A2 sweep-loop configuration, neither of which this guide
sets up. Start here; see `docs/scaling-plan.md` before scaling out.

*Ported from the previous Google Cloud guide — the sizing formula, systemd
unit, and redeploy steps are unchanged, only the provider-specific commands
differ.*

## Note on the git history

`meeting-bot/auth.json` was found committed in this repo's git history
(`07294a1`, still reachable from `origin/main` even though a later commit
untracked it). The Google account's credentials have since been rotated,
so that old commit only contains a dead session token now. Decision made:
leave history as-is rather than purge it (`git filter-repo`/BFG +
force-push), since the repo is private and staying private — reopen this
if that ever changes (new collaborators, visibility toggled, fork/transfer).

---

## 1. What's changing vs. local dev, and why

`docker-compose.yml` is dev-shaped in ways that don't belong on a server:

- `backend` and `frontend` build from `Dockerfile.dev` (hot-reload dev
  servers, and the backend image additionally installs the test
  dependencies) with the host source tree bind-mounted in
  (`./backend:/app`, `./frontend:/app`). Fine for editing code live on your
  machine; wrong for a deployed server — you want the production images
  built once from a specific commit, not a dev server watching a
  live-mounted filesystem.
- Nothing has a `restart:` policy, so a crashed container just stays down.
- The recorders' ports (`3000`, `3001`) are published to the host — harmless
  locally, unnecessary exposure on a server. Neither is published in the prod
  file; the backend reaches both over the internal `meeting-net` bridge.
- `shm_size` has to be sized deliberately rather than left at Docker's 64MB
  default — Chromium needs it for video/audio stream buffers. The deployed
  value is `shm_size: 2gb` **per recorder**, which covers one concurrent
  meeting each; see step 5 for how to scale it with `MAX_CONCURRENT_MEETINGS`.

Both files now describe **six** services, not three: `backend`, `frontend` and
`meeting-bot`, plus `redis` and `worker` from
[docs/scaling-plan.md](scaling-plan.md) Phase A3, plus a second recorder
`meeting-bot-2` from Phase C3. `worker` is not a fourth image to build - it is
the backend image running `arq app.worker.WorkerSettings` instead of uvicorn, so
it rebuilds and redeploys in lockstep with `backend`. Nor is `meeting-bot-2` a
fifth: it is the same recorder image as `meeting-bot`, pointed at a different
credential directory, so the two also rebuild together. Redis is where queued
transcription jobs live; `--appendonly yes` plus the `redis-data` volume are
what let a queued job survive a Redis restart, which is the whole point of
moving transcription off the in-process thread pool.

**Two recorders, one pool.** `backend` and `worker` both carry a `BOT_HOSTS`
list naming `bot-a=http://meeting-bot:3000` and
`bot-b=http://meeting-bot-2:3000`, and each recorder is pinned to
`MAX_CONCURRENT_MEETINGS=1`. That pinning is what makes the guarantee legible:
two meetings dispatched at once *must* land on different hosts, because neither
host can take both. The list has to be identical on both services — `backend`
resolves a meeting's `bot_host_id` against it for stop/delete/re-upload, while
`worker` runs both the heartbeat that fills the registry cache and the
dispatcher that reads it. Drop `BOT_HOSTS` entirely and `bot_registry` falls
back to a pool of one built from `MEETING_BOT_URL`, which silently drops
`meeting-bot-2` out of rotation rather than failing.

`docker-compose.prod.yml` is a **standalone** prod file — not an override
merged with `docker-compose.yml`, deliberately, since Compose's merge rules
for `volumes`/`build` are easy to get subtly wrong. Use it via
`-f docker-compose.prod.yml` everywhere below; leave `docker-compose.yml`
untouched for local dev.

One more thing worth knowing about, not changed here: `backend`'s
`./recordings:/app/recordings` bind mount in the dev file is dead code —
`transcription_service.py` downloads recordings from Supabase Storage, it
never reads meeting-bot's local output directly. It's also pointed at a
different host directory (`./recordings`) than meeting-bot's own mount
(`./meeting-bot/recordings`), so even locally it isn't sharing anything.

## 2. Instance sizing

Per-meeting cost (measured): **~780MB RAM, ~1.3 vCPU** per concurrent
recording (headed Chromium + ffmpeg + its PulseAudio sink).

Baseline (OS + Docker + idle `backend`/`frontend`/both recorders with no
active recording, plus headroom for `AuthKeepAlive`'s periodic Chrome
launches) is an estimate — budget **~1.5GB RAM / ~1 vCPU** for it and verify
with `docker stats` once real traffic is flowing. EC2 lets you stop → change
instance type → start without recreating the instance, so adjusting later is
cheap (the Elastic IP and EBS volume survive).

**N is the pool's total, not one recorder's.** With `meeting-bot` and
`meeting-bot-2` both pinned to `MAX_CONCURRENT_MEETINGS=1`, the stack records
**two** meetings at once, so `N = 2` in the formula below even though no single
container exceeds one. A second idle recorder costs little beyond its own
`AuthKeepAlive` Chrome launches, which the baseline already covers; what it
adds is the second concurrent *recording*, and that is the term that matters.

**Redis and the worker** both sit in that baseline, and neither moves it much.
Redis holds `(meeting_id, storage_path)` tuples and job metadata - kilobytes,
not gigabytes - so **~50MB** covers it; if it ever grows past that, the problem
is job cleanup rather than sizing. `worker` is the more real cost, but it runs
the transcription pipeline that used to live inside `backend`, so this is work
**moving** rather than being added: budget **~500MB** at `WORKER_MAX_JOBS=4`
and expect `backend`'s own footprint to fall by roughly as much once
`TRANSCRIPTION_USE_QUEUE=true`. That pipeline is I/O- and API-bound (download,
Gemini upload, poll, then waiting on the model) rather than CPU-bound like
recording, so it does not change the vCPU formula. Add **~0.6GB** to the RAM
baseline - treat it as ~2.1GB rather than ~1.5GB - and leave the per-meeting
term alone.

Formula, with a ~25-30% safety margin baked in:

```
RAM  ≈ (1.5 + 0.78 × N) × 1.3   GB
vCPU ≈ (1   + 1.3  × N) × 1.3
```

| N (total concurrent meetings) | RAM needed | vCPU needed | Suggested instance |
|---|---|---|---|
| 2 — **this stack as configured** | ~4GB | ~4.7 | `c6a.2xlarge` (8 vCPU / 16GB) |
| 5 | ~7GB | ~9.75 | `c6a.4xlarge` (16 vCPU / 32GB) |

The first row is what two recorders at `MAX_CONCURRENT_MEETINGS=1` produce, so
`c6a.2xlarge` is the starting point for this guide — not something smaller
sized against a single recorder.

> **Do not use a `t3`/`t4g` burstable instance.** This workload is sustained
> CPU (Chromium plus ffmpeg encoding, for the whole length of a meeting), not
> bursty. T-family instances run on CPU credits and throttle hard once those
> are exhausted — an hour-long recording would degrade partway through, and
> it would show up as bad audio rather than as an obvious failure. Use a
> fixed-performance family: `c6a`/`c6i` (compute-optimised, the right shape
> here) or `m6a`/`m6i` if you want more RAM headroom.

`c6a` is AMD and cheaper than the Intel `c6i` at the same size; either works.
Compute-optimised beats general-purpose here because the profile is CPU-bound
per the numbers above.

Root volume: **30GB gp3** EBS (the 8GB default is far too tight once you
count the OS, Docker images for all five services, and build cache - though
`worker` shares the `backend` image and `redis:7-alpine` is tiny, so the two
new services add very little to it).
Recordings don't need extra provisioning — each is deleted locally right
after a successful Supabase upload (`MeetingLifecycle.js`'s `fs.unlinkSync`),
so local storage never accumulates.

AMI: **Debian 12 (bookworm)** — matches `meeting-bot`'s Dockerfile base image
family. Ubuntu 22.04/24.04 works too; if you use it, swap `debian` for
`ubuntu` in the Docker repository URL in step 3, and the SSH user is `ubuntu`
rather than `admin`.

Allocate and associate an **Elastic IP** before configuring anything below
that references the address (`FRONTEND_ORIGIN`, `VITE_API_URL`,
`GOOGLE_OAUTH_REDIRECT_URI`). A default EC2 public IP changes on every
stop/start, which would silently break CORS and the OAuth redirect.

## 3. Installing Docker and Compose on the instance

SSH in with the key pair you assigned at launch:

```bash
ssh -i /path/to/your-key.pem admin@<elastic-ip>
```

Then:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Run docker without sudo (log out/in, or `newgrp docker`, for this to take effect)
sudo usermod -aG docker $USER

# Start on boot, and now
sudo systemctl enable --now docker
```

Confirm: `docker compose version` (the plugin form, `docker compose` not
`docker-compose`).

## 4. Getting the repo onto the instance

Code only — never the `.env`/auth files, which are gitignored on purpose and
go over in step 5 instead.

```bash
git clone <your-repo-url> meeting-recorder-bot
cd meeting-recorder-bot
```

Use an SSH deploy key or HTTPS PAT if the repo is private.

## 5. Getting `.env` files and auth state onto the instance securely

Eight files need to exist on the instance, none of them tracked in git:
`backend/.env`, `meeting-bot/.env`, `frontend/.env`, the root `.env`, plus two
session files for **each** recorder — `meeting-bot/auth/bot-a/auth.json` and
`zoom-auth.json`, and the same pair under `bot-b`.

**On the auth directory layout.** Credentials live one directory per recorder,
named after the host id it serves in `BOT_HOSTS` (`docs/scaling-plan.md` Phase
C4), not as loose files at the `meeting-bot/` root:

```
meeting-bot/auth/
  bot-a/
    auth.json        # Google session for host bot-a
    zoom-auth.json   # Zoom session for host bot-a
  bot-b/
    auth.json        # a DIFFERENT Google account
    zoom-auth.json   # a DIFFERENT Zoom account
```

`meeting-bot/auth/` and everything under it is gitignored except its
`README.md`, so none of this arrives with `git clone` in step 4. The accounts
under `bot-b` must be different accounts, not copies — see "Two recorders, two
identities" below, which is the single most common way to get this wrong.

**The root `.env` is not optional.** `docker-compose.prod.yml` marks the three
`VITE_*` build args required (`${VITE_API_URL:?...}`), and Compose resolves
those when it *parses* the file — so without them `up`, `ps`, `logs` and `down`
all refuse, not just `build`. That includes the systemd unit's `up -d` after a
reboot, when nobody is around to have sourced a shell, which is exactly why the
values live in a file Compose loads automatically rather than in an exported
variable. Copy `.env.example` and fill it in:

```bash
scp -i /path/to/your-key.pem .env.example admin@<elastic-ip>:~/meeting-recorder-bot/.env.example
```

Then on the instance, `cp .env.example .env` and set `VITE_API_URL` to the
instance's real address (see the value list further down). These three are
compiled into the public JS bundle by Vite and are not secrets; they are
gitignored all the same.

First create the directories on the instance — `scp` will not create them for
you, and without them the four auth transfers below fail:

```bash
ssh -i /path/to/your-key.pem admin@<elastic-ip> \
  'mkdir -p ~/meeting-recorder-bot/meeting-bot/auth/bot-a ~/meeting-recorder-bot/meeting-bot/auth/bot-b ~/meeting-recorder-bot/meeting-bot/recordings-2'
```

`recordings-2` is `meeting-bot-2`'s own output directory. It ships with a
`.gitkeep` so `git clone` creates it, but creating it here too is harmless and
covers a checkout that predates it — a missing host directory would otherwise
be created by Docker as root and leave the container unable to write to it.

Then, from your local machine (not the instance), over SSH:

```bash
scp -i /path/to/your-key.pem backend/.env  admin@<elastic-ip>:~/meeting-recorder-bot/backend/.env
scp -i /path/to/your-key.pem meeting-bot/.env admin@<elastic-ip>:~/meeting-recorder-bot/meeting-bot/.env
scp -i /path/to/your-key.pem frontend/.env admin@<elastic-ip>:~/meeting-recorder-bot/frontend/.env
scp -i /path/to/your-key.pem meeting-bot/auth/bot-a/auth.json admin@<elastic-ip>:~/meeting-recorder-bot/meeting-bot/auth/bot-a/auth.json
scp -i /path/to/your-key.pem meeting-bot/auth/bot-a/zoom-auth.json admin@<elastic-ip>:~/meeting-recorder-bot/meeting-bot/auth/bot-a/zoom-auth.json
scp -i /path/to/your-key.pem meeting-bot/auth/bot-b/auth.json admin@<elastic-ip>:~/meeting-recorder-bot/meeting-bot/auth/bot-b/auth.json
scp -i /path/to/your-key.pem meeting-bot/auth/bot-b/zoom-auth.json admin@<elastic-ip>:~/meeting-recorder-bot/meeting-bot/auth/bot-b/zoom-auth.json
```

Then on the instance, lock down permissions (these hold real secrets —
Supabase service-role key, bearer tokens, Google/Zoom session cookies):

```bash
chmod 600 backend/.env meeting-bot/.env frontend/.env .env
chmod 700 meeting-bot/auth meeting-bot/auth/bot-a meeting-bot/auth/bot-b
chmod 600 meeting-bot/auth/bot-*/auth.json meeting-bot/auth/bot-*/zoom-auth.json
```

Before starting anything, update the values that were `localhost`-shaped for
local dev and now need the instance's real address:

- `backend/.env`: `FRONTEND_ORIGIN` → the frontend's real public URL;
  `GOOGLE_OAUTH_REDIRECT_URI` → `http://<elastic-ip-or-domain>:8000/calendar/oauth/callback`
  (and add that exact URI to the OAuth client's authorized redirects in
  Google Cloud Console, or the calendar integration will reject it).
  `MEETING_BOT_URL` stays `http://meeting-bot:3000` — that's Compose's
  internal service-name DNS, unrelated to the instance's public address.
- `frontend/.env`: `VITE_API_URL` → the backend's real public URL
  (`http://<elastic-ip-or-domain>:8000`).
- The root `.env`: the same three `VITE_*` values as `frontend/.env`. Two files
  because a Vite dev server and a Compose build read their configuration from
  different places, and Compose cannot interpolate from `frontend/.env`. Keep
  them in step — if they disagree, the deployed bundle follows the root `.env`.
- `meeting-bot/.env`: `BACKEND_WEBHOOK_URL` stays `http://backend:8000/...`
  for the same internal-DNS reason. `MAX_CONCURRENT_MEETINGS` here is the
  fallback only — `docker-compose.prod.yml` pins it to `1` on both recorders
  via `environment:`, which outranks `env_file:`, precisely because the two
  containers share this one file and could not otherwise be told apart. To
  raise the pool's capacity, raise the pinned values and each recorder's
  `shm_size` together. The documented baseline is:

  ```yaml
  shm_size: 2gb
  ```

  per recorder, covering `MAX_CONCURRENT_MEETINGS=1` each. Budget ~2gb per
  concurrent meeting, then verify against real usage with `docker stats`
  rather than trusting the formula.

(One-time only, not a recurring re-transfer: once a recorder is running, its
own `AuthKeepAlive` job takes over refreshing that host's `auth.json` and
`zoom-auth.json` in place — see `docs/auth-keepalive-runbook.md`. Each recorder
refreshes only its own directory. You only need to scp fresh copies again if
the runbook's `ALERT` fires.)

### Two recorders, two identities

`docker-compose.prod.yml` runs both recorders, and the transfers above already
cover both. What this section exists for is the one thing a file transfer
cannot enforce: **`bot-b` must hold different accounts from `bot-a`, not
copies.**

Two hosts sharing one Google login means two concurrent sessions rotating the
same short-lived cookies against each other, and Google's rotation cookies
expire within roughly 10–60 minutes of being issued. The symptom is not a clean
failure — it is both recorders' sessions dying at unpredictable intervals, each
one looking like an unrelated expiry. That is the failure Phase C4 exists to
remove, and copying `bot-a/` to `bot-b/` reintroduces all of it while looking
like a working deployment.

Generate each set on a desktop machine with `meeting-bot/generate-auth.cjs` and
`generate-zoom-auth.cjs`. Both scripts take the Chrome profile *and* the output
path as environment variables, so one command names the account and the file it
writes; there is nothing to uncomment. `meeting-bot/auth/README.md` has all
four commands for bash and PowerShell, and `--dry-run` prints the pairing a
command resolves to without launching Chrome.

> **Do not start `meeting-bot-2` before both of its files exist.** A recorder
> whose credentials are missing does not get skipped: `AuthHealth` reports
> `unknown` until its first keepalive cycle lands, `bot_registry.usable_for`
> treats `unknown` as usable, and the backend dispatches meetings the host
> cannot join. A half-populated `bot-b` is worse than no `bot-b` — it looks
> healthy and shreds meetings. If `bot-b`'s credentials are not ready, start
> the stack without it (`docker compose -f docker-compose.prod.yml up -d
> --scale meeting-bot-2=0`) and bring it up once they are.

Once both are running, confirm the pool sees two hosts rather than one:

```bash
docker compose -f docker-compose.prod.yml logs worker | grep -i 'bot-a\|bot-b'
```

## 6. Security group rules

EC2 security groups deny all inbound by default — open only what's needed.

| Port | Who calls it | Public? |
|---|---|---|
| 80 (frontend) | End users' browsers | Yes — `0.0.0.0/0` |
| 8000 (backend) | End users' browsers call it *directly* — `VITE_API_URL` is baked into the frontend bundle at build time, so the browser does not go through nginx for API calls | Yes — `0.0.0.0/0` |
| 3000 (both recorders) | Only `backend` and `worker`, over the internal `meeting-net` Docker bridge — `meeting-bot` and `meeting-bot-2` each listen on 3000 inside their own container, which is what `BOT_HOSTS` addresses | **No** — neither is published to the host in `docker-compose.prod.yml`, so there is nothing to open. Adding the second recorder opens no new port |
| 22 (SSH) | You | **Restrict to your own IP**, not `0.0.0.0/0`. Better still, use AWS Systems Manager Session Manager and open nothing — it needs the SSM agent (preinstalled on Amazon Linux, an extra install on Debian) and an instance profile with `AmazonSSMManagedInstanceCore` |

Outbound can stay at the default allow-all — the stack needs to reach
Supabase, Gemini/Groq/Sarvam, Google Calendar, and the meeting platforms.

**Known gap, not covered by this guide**: everything above is plain HTTP —
there is no TLS anywhere in this stack yet. Real user credentials (Supabase
auth tokens, the bearer token) would cross the wire unencrypted. Before real
traffic, put a reverse proxy in front — Caddy on the instance is the
lowest-effort path (automatic Let's Encrypt certs), or an Application Load
Balancer with an ACM certificate — and open only 443. A further improvement:
proxy `/api` through nginx to `backend:8000` internally so the browser only
ever talks to one public port/origin, removing the need to expose 8000 at
all. That changes `VITE_API_URL` to a relative path and is a real (small) app
change, not just deployment config, so it is flagged here rather than done
silently.

## 7. Running Compose as a systemd service (survives reboots)

`restart: unless-stopped` (already in `docker-compose.prod.yml`) handles
individual container crashes once Docker is running. For the whole stack to
come back after an instance reboot, wrap it in a systemd unit — this also
gives you one clean `systemctl` handle for start/stop/status:

```bash
sudo tee /etc/systemd/system/meeting-recorder.service > /dev/null <<'EOF'
[Unit]
Description=meeting-recorder-bot (backend + worker + 2 recorders + frontend)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/home/admin/meeting-recorder-bot
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml up -d
ExecStop=/usr/bin/docker compose -f docker-compose.prod.yml down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
```

`WorkingDirectory` is doing more work than it looks. Besides locating
`docker-compose.prod.yml`, it is the directory Compose loads the root `.env`
from — and because the `VITE_*` build args are marked required, a unit that
started somewhere else would fail at file-parse time with
`required variable VITE_API_URL is missing a value`, on every boot, with no
containers started. If you relocate the checkout, move `.env` with it.

It assumes the Debian AMI's `admin` user — change it to `/home/ubuntu/...` on
an Ubuntu AMI. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now meeting-recorder
systemctl status meeting-recorder
```

## 8. Logs, and redeploying after a code change

**Logs** (per service, or all):
```bash
docker compose -f docker-compose.prod.yml logs -f meeting-bot
docker compose -f docker-compose.prod.yml logs -f --tail 100
```
Watch specifically for `[AuthKeepAlive] ALERT` (see
`docs/auth-keepalive-runbook.md`) and the `[server]`/`[Lifecycle]` lines from
`meeting-bot` for join/recording failures.

**Redeploy after pulling new code** — rebuild and recreate only what changed,
to avoid unnecessary downtime on unaffected services. For a recorder change,
note that both recorders run the *same image*, so one build covers them and
both need recreating or you are running two different versions of the bot:
```bash
git pull
docker compose -f docker-compose.prod.yml build meeting-bot
docker compose -f docker-compose.prod.yml up -d meeting-bot meeting-bot-2
```
Recreating them together briefly leaves the pool with no free host. If that
matters, do them one at a time — `up -d meeting-bot`, wait for it to report
healthy on `/capacity`, then `up -d meeting-bot-2` — and the other host keeps
taking meetings throughout.

A frontend change needs no shell preparation: Compose reads the three `VITE_*`
values from the root `.env` automatically (it is also why every other command
here works at all — see step 5).
```bash
docker compose -f docker-compose.prod.yml build frontend
docker compose -f docker-compose.prod.yml up -d frontend
```
If you change `VITE_API_URL`, edit the root `.env` and rebuild — the value is
compiled into the bundle, so `up -d` alone will keep serving the old one.

Or rebuild everything at once:
`docker compose -f docker-compose.prod.yml up -d --build`.

Note that a backend redeploy currently kills any in-flight transcription —
the meeting sits in `transcribing` until the watchdog TTL fails it. Phase A3
in `docs/scaling-plan.md` fixes this; until then, prefer redeploying when
nothing is mid-transcription.

## 8b. Turning on Sentry, and confirming delivery

`SENTRY_DSN` is empty by default and both Python services start fine without
it (`docs/scaling-plan.md` Phase A4). To turn it on, put the DSN from your
Sentry project into `backend/.env` and recreate **both** Python services — the
API and the worker are separate processes that each initialise Sentry for
themselves:

```bash
# in backend/.env
SENTRY_DSN=https://<key>@<org>.ingest.sentry.io/<project>
ENVIRONMENT=production
```
```bash
docker compose -f docker-compose.prod.yml up -d backend worker
```

`up -d` and not `restart`: `restart` reuses the existing container. That is
fine for an env-var change alone, but the moment a redeploy also brings new
Python dependencies it will silently run the old image — which is exactly how
the `PYTHONUNBUFFERED` fix in `1c00428` sat committed in git while the running
containers predated it. After a `git pull` that touches
`backend/requirements.txt`, it is `up -d --build backend worker`.

**Then confirm delivery, because nothing else will tell you.** A wrong or
revoked DSN does not crash anything — `sentry_sdk` drops events quietly, and
the failure looks exactly like "no errors are happening". The A4 test suite
verifies the payload *before it is transmitted* (`CapturingTransport` in
`backend/tests/test_observability.py`); it cannot verify that a real Sentry
project accepts it. That check has to happen here, once, against the real DSN:

```bash
# Confirm the process actually initialised Sentry rather than skipping it.
docker compose -f docker-compose.prod.yml logs worker | grep '\[worker\] ready'
#   ... [worker] ready - log level INFO, sentry enabled, environment production

# Send one deliberate error from the API container.
docker compose -f docker-compose.prod.yml exec backend python -c   "import sentry_sdk; from app.observability import init_sentry;    init_sentry('api'); sentry_sdk.capture_message('A4 delivery check', level='error');    sentry_sdk.flush(5)"
```

Open the Sentry project and confirm the event arrived, tagged
`component: api` and `environment: production`. If nothing appears within a
minute, the DSN is wrong or outbound HTTPS to `ingest.sentry.io` is blocked by
the security group — not "no errors happened". Resolve the event afterwards.

Do this **before** the Phase A5 deploy, not after. A5 (local JWT verification)
fails for every user at once if the expected `aud`/`iss` is wrong, and its
error rate in Sentry is the signal you would be deploying in order to watch.

## 9. Alerting on `AuthKeepAlive` failures (not yet set up)

`docs/auth-keepalive-runbook.md` covers what the `[AuthKeepAlive] ALERT` log
block means and how to fix it once you see it — but "someone has to be
watching `docker compose logs`" isn't real alerting.

This section is **written but not executed or tested against a real AWS
account** — verify each step against current AWS docs before relying on it,
the way everything else in this guide was actually build/run-tested.

**Step 1 — get container logs into CloudWatch Logs.** By default they stay
local to the instance (`docker logs`). Point Docker's logging driver at
CloudWatch, per service, in `docker-compose.prod.yml`:

```yaml
  meeting-bot:
    # ...existing config...
    logging:
      driver: awslogs
      options:
        awslogs-region: <your-region>
        awslogs-group: /meeting-recorder/meeting-bot
        awslogs-create-group: "true"
```

Requires an **instance profile** (IAM role attached to the EC2 instance) with
`logs:CreateLogGroup`, `logs:CreateLogStream` and `logs:PutLogEvents`. Attach
it to the instance rather than putting access keys in a file.

**Step 2 — a metric filter counting the ALERT string:**

```bash
aws logs put-metric-filter \
  --log-group-name /meeting-recorder/meeting-bot \
  --filter-name auth-keepalive-alert \
  --filter-pattern '"[AuthKeepAlive] ALERT"' \
  --metric-transformations \
      metricName=AuthKeepAliveAlert,metricNamespace=MeetingRecorder,metricValue=1,defaultValue=0
```

**Step 3 — an SNS topic and a subscription** to receive the notification:

```bash
aws sns create-topic --name meeting-recorder-alerts
aws sns subscribe --topic-arn <topic-arn> --protocol email --notification-endpoint you@example.com
```

(Confirm the subscription from the email before relying on it.)

**Step 4 — a CloudWatch alarm on that metric:**

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name auth-keepalive-alert \
  --namespace MeetingRecorder --metric-name AuthKeepAliveAlert \
  --statistic Sum --period 300 --evaluation-periods 1 \
  --threshold 0 --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching \
  --alarm-actions <topic-arn>
```

Once wired up, an expired Google/Zoom session gets someone emailed instead of
sitting silently in logs until a real user's recording fails.

> When Phase A4 of `docs/scaling-plan.md` lands, Sentry will cover unhandled
> backend exceptions. This CloudWatch path stays useful regardless — it
> watches a *log string* from `meeting-bot`, which is a Node service Sentry
> is not being added to.
