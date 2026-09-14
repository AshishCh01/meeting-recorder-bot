# Deploying to AWS — single EC2 instance

Architecture: one EC2 instance running `backend`, `meeting-bot`, and
`frontend` via Docker Compose, same shape as local dev. No ECS, no
autoscaling group.

**Why a single instance, and what would change that.** `meeting-bot` keeps its
`activeMeetings` session map and `AuthKeepAlive` job in process memory, so it
needs exactly one long-lived process — that constraint is real and is what
Phase C of `docs/scaling-plan.md` addresses. The `backend` no longer has that
constraint (chat history is persisted in `chat_messages` and rehydrates into
any process; the scheduler claims rows atomically), but running it on more
than one instance needs a load balancer and the Phase A2 sweep-loop
configuration, neither of which this guide sets up. Start here; see
`docs/scaling-plan.md` before scaling out.

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
- `meeting-bot`'s port `3000` is published to the host — harmless locally,
  unnecessary exposure on a server.
- `shm_size` has to be sized deliberately rather than left at Docker's 64MB
  default — Chromium needs it for video/audio stream buffers. The deployed
  value is `shm_size: 2gb`, which covers one concurrent meeting; see step 5
  for how to scale it with `MAX_CONCURRENT_MEETINGS`.

Both files now describe **five** services, not three: `backend`, `frontend` and
`meeting-bot`, plus `redis` and `worker` from
[docs/scaling-plan.md](scaling-plan.md) Phase A3. `worker` is not a fourth image
to build - it is the backend image running `arq app.worker.WorkerSettings`
instead of uvicorn, so it rebuilds and redeploys in lockstep with `backend`.
Redis is where queued transcription jobs live; `--appendonly yes` plus the
`redis-data` volume are what let a queued job survive a Redis restart, which is
the whole point of moving transcription off the in-process thread pool.

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

Baseline (OS + Docker + idle `backend`/`frontend`/`meeting-bot` with no
active recording, plus headroom for `AuthKeepAlive`'s periodic Chrome
launches) is an estimate — budget **~1.5GB RAM / ~1 vCPU** for it and verify
with `docker stats` once real traffic is flowing. EC2 lets you stop → change
instance type → start without recreating the instance, so adjusting later is
cheap (the Elastic IP and EBS volume survive).

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

| N (MAX_CONCURRENT_MEETINGS) | RAM needed | vCPU needed | Suggested instance |
|---|---|---|---|
| 2 | ~4GB | ~4.7 | `c6a.2xlarge` (8 vCPU / 16GB) |
| 5 | ~7GB | ~9.75 | `c6a.4xlarge` (16 vCPU / 32GB) |

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

Five files need to exist on the instance, none of them tracked in git:
`backend/.env`, `meeting-bot/.env`, `frontend/.env`, plus the recorder's two
session files, `meeting-bot/auth/bot-a/auth.json` and
`meeting-bot/auth/bot-a/zoom-auth.json`.

**On the auth directory layout.** Credentials live one directory per recorder,
named after the host id it serves (`docs/scaling-plan.md` Phase C4), not as two
loose files at the `meeting-bot/` root:

```
meeting-bot/auth/
  bot-a/
    auth.json        # Google session for host bot-a
    zoom-auth.json   # Zoom session for host bot-a
```

A single-instance deployment — which is what this guide sets up — uses `bot-a`
only. See "Deploying a second recorder" below before adding `bot-b`.
`meeting-bot/auth/` and everything under it is gitignored except its
`README.md`, so none of this arrives with `git clone` in step 4.

> **Check the compose mount before you deploy.** `docker-compose.prod.yml`
> still carries the pre-C4 single-file mounts
> (`./meeting-bot/auth.json:/app/auth.json` and the Zoom equivalent), which do
> not match the layout above. Point `meeting-bot`'s volume at the directory
> (`./meeting-bot/auth/bot-a:/app/auth`) and set `AUTH_STATE_PATH=/app/auth/auth.json`
> and `ZOOM_AUTH_STATE_PATH=/app/auth/zoom-auth.json`, as `docker-compose.yml`
> already does. Two reasons it has to be a directory mount, not two file
> mounts: a missing host file makes Docker silently create a *directory* at
> `/app/auth.json`, and `AuthKeepAlive` replaces these files on rotation —
> a single-file bind mount does not carry the new inode back to the host, so
> the container re-reads stale cookies after a restart. That compose edit is
> not made by this guide.

First create the directory on the instance — `scp` will not create it for you,
and without it the two auth transfers below fail:

```bash
ssh -i /path/to/your-key.pem admin@<elastic-ip> 'mkdir -p ~/meeting-recorder-bot/meeting-bot/auth/bot-a'
```

Then, from your local machine (not the instance), over SSH:

```bash
scp -i /path/to/your-key.pem backend/.env  admin@<elastic-ip>:~/meeting-recorder-bot/backend/.env
scp -i /path/to/your-key.pem meeting-bot/.env admin@<elastic-ip>:~/meeting-recorder-bot/meeting-bot/.env
scp -i /path/to/your-key.pem frontend/.env admin@<elastic-ip>:~/meeting-recorder-bot/frontend/.env
scp -i /path/to/your-key.pem meeting-bot/auth/bot-a/auth.json admin@<elastic-ip>:~/meeting-recorder-bot/meeting-bot/auth/bot-a/auth.json
scp -i /path/to/your-key.pem meeting-bot/auth/bot-a/zoom-auth.json admin@<elastic-ip>:~/meeting-recorder-bot/meeting-bot/auth/bot-a/zoom-auth.json
```

Then on the instance, lock down permissions (these hold real secrets —
Supabase service-role key, bearer tokens, Google/Zoom session cookies):

```bash
chmod 600 backend/.env meeting-bot/.env frontend/.env
chmod 700 meeting-bot/auth meeting-bot/auth/bot-a
chmod 600 meeting-bot/auth/bot-a/auth.json meeting-bot/auth/bot-a/zoom-auth.json
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
- `meeting-bot/.env`: `BACKEND_WEBHOOK_URL` stays `http://backend:8000/...`
  for the same internal-DNS reason. Set `MAX_CONCURRENT_MEETINGS` to whatever
  N you sized the instance for in step 2, and keep
  `docker-compose.prod.yml`'s `shm_size` in step with it. The documented
  baseline is:

  ```yaml
  shm_size: 2gb
  ```

  which covers `MAX_CONCURRENT_MEETINGS=1`. Budget ~2gb per concurrent
  meeting as a starting point (so ~2gb × N), then verify against real usage
  with `docker stats` rather than trusting the formula.

(One-time only, not a recurring re-transfer: once the instance's
`meeting-bot` container is running, its own `AuthKeepAlive` job takes over
refreshing `meeting-bot/auth/bot-a/auth.json` and
`meeting-bot/auth/bot-a/zoom-auth.json` in place — see
`docs/auth-keepalive-runbook.md`. You only need to scp fresh copies again if
the runbook's `ALERT` fires.)

### Deploying a second recorder (`bot-b`)

Out of scope for this guide's single-instance, single-recorder setup, but
documented here so the layout is not a surprise later. A second recorder is a
second container off the same image (`docs/scaling-plan.md` Phase C3/C4) with
its own credential directory:

```
meeting-bot/auth/
  bot-b/
    auth.json        # a DIFFERENT Google account from bot-a's
    zoom-auth.json   # a DIFFERENT Zoom account from bot-a's
```

Transferred and permissioned exactly like `bot-a` above, substituting `bot-b`
in every path:

```bash
ssh -i /path/to/your-key.pem admin@<elastic-ip> 'mkdir -p ~/meeting-recorder-bot/meeting-bot/auth/bot-b'
scp -i /path/to/your-key.pem meeting-bot/auth/bot-b/auth.json admin@<elastic-ip>:~/meeting-recorder-bot/meeting-bot/auth/bot-b/auth.json
scp -i /path/to/your-key.pem meeting-bot/auth/bot-b/zoom-auth.json admin@<elastic-ip>:~/meeting-recorder-bot/meeting-bot/auth/bot-b/zoom-auth.json
```

**The two accounts must be different.** Two hosts sharing one Google login
means two concurrent sessions rotating the same short-lived cookies against
each other, and Google's rotation cookies expire within roughly 10–60 minutes
of being issued — which is the failure Phase C4 exists to remove. Generate
each set with `meeting-bot/generate-auth.cjs` / `generate-zoom-auth.cjs`,
pointed at the target path via `AUTH_STATE_PATH` / `ZOOM_AUTH_STATE_PATH`;
`meeting-bot/auth/README.md` has the commands.

Note that `docker-compose.prod.yml` as it stands defines one `meeting-bot`
service, so adding `bot-b` is a compose change (a second service plus
`BOT_HOSTS` on `backend` and `worker`), not just a file transfer. That change
is deliberately not made here.

## 6. Security group rules

EC2 security groups deny all inbound by default — open only what's needed.

| Port | Who calls it | Public? |
|---|---|---|
| 80 (frontend) | End users' browsers | Yes — `0.0.0.0/0` |
| 8000 (backend) | End users' browsers call it *directly* — `VITE_API_URL` is baked into the frontend bundle at build time, so the browser does not go through nginx for API calls | Yes — `0.0.0.0/0` |
| 3000 (meeting-bot) | Only `backend`, over the internal `meeting-net` Docker bridge | **No** — not published to the host at all in `docker-compose.prod.yml`, so there is nothing to open |
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
Description=meeting-recorder-bot (backend + meeting-bot + frontend)
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

`WorkingDirectory` assumes the Debian AMI's `admin` user — change it to
`/home/ubuntu/...` on an Ubuntu AMI. Then:

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
to avoid unnecessary downtime on unaffected services:
```bash
git pull
docker compose -f docker-compose.prod.yml build meeting-bot
docker compose -f docker-compose.prod.yml up -d meeting-bot
```
For a frontend change, the build-arg substitution needs the vars in your
shell first (Compose reads `${VITE_API_URL}` etc. from the shell environment
at `build` time, not from `frontend/.env`'s `env_file:`, which only injects
runtime env into a container — nginx doesn't need these, Vite bakes them into
the JS bundle at build time instead):
```bash
set -a; source frontend/.env; set +a
docker compose -f docker-compose.prod.yml build frontend
docker compose -f docker-compose.prod.yml up -d frontend
```
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
