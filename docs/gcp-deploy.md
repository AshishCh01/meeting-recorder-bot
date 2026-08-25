# Deploying to Google Cloud — single Compute Engine VM

Architecture: one GCE VM running `backend`, `meeting-bot`, and `frontend` via
Docker Compose, same shape as local dev. No Cloud Run, no autoscaling — the
in-memory state in both `backend` (chat session cache, watchdog/scheduler
loops) and `meeting-bot` (`activeMeetings` session map, `AuthKeepAlive`)
requires exactly one long-lived process per service, which a single VM gives
you for free without needing to externalize anything.

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

`docker-compose.yml` is dev-shaped in ways that don't belong on a VM:

- `backend` and `frontend` build from `Dockerfile.dev` (hot-reload dev
  servers) with the host source tree bind-mounted in (`./backend:/app`,
  `./frontend:/app`). Fine for editing code live on your machine; wrong for
  a deployed server — you want the actual production images (the ones with
  the `${PORT:-8000}` / nginx-template fixes already made), built once from
  a specific commit, not a dev server watching a live-mounted filesystem.
- Nothing has a `restart:` policy, so a crashed container just stays down.
- `meeting-bot`'s port `3000` is published to the host — harmless locally,
  unnecessary exposure on a VM (see the compose file's comment on this).
- `shm_size: 2gb` was sized for one concurrent meeting only.

`docker-compose.prod.yml` (new, alongside this guide) is a **standalone**
prod file — not an override merged with `docker-compose.yml`, deliberately,
since Compose's merge rules for `volumes`/`build` are easy to get subtly
wrong and I'd rather the file be unambiguous on inspection. Use it via
`-f docker-compose.prod.yml` everywhere below; leave `docker-compose.yml`
untouched for local dev.

One more thing worth knowing about, not changed here: `backend`'s
`./recordings:/app/recordings` bind mount in the dev file is dead code —
`transcription_service.py` downloads recordings from Supabase Storage, it
never reads meeting-bot's local output directly (confirmed by grep — no
local-path read in `storage_service.py`/`transcription_service.py`). It's
also pointed at a different host directory (`./recordings`) than
meeting-bot's own mount (`./meeting-bot/recordings`), so even locally it's
not actually sharing anything. Harmless to leave, not carried into the prod
file since it does nothing.

## 2. VM sizing

Per-meeting cost (your own figures): **~780MB RAM, ~1.3 vCPU** per
concurrent recording (headed Chromium + ffmpeg + its PulseAudio sink).

Baseline (OS + Docker + idle `backend`/`frontend`/`meeting-bot` with no
active recording, plus headroom for `AuthKeepAlive`'s periodic Chrome
launches) is an estimate, not measured on this specific box — budget
**~1.5GB RAM / ~1 vCPU** for it and verify with `docker stats` once real
traffic is flowing, adjusting the machine type later if it's off (GCE lets
you stop → resize → start without recreating the VM).

Formula, with a ~25-30% safety margin baked in (avoid running pinned at
100% CPU or right at the RAM ceiling — both are how you get OOM-killed
containers or a browser that silently degrades):

```
RAM  ≈ (1.5 + 0.78 × N) × 1.3   GB
vCPU ≈ (1   + 1.3  × N) × 1.3
```

| N (MAX_CONCURRENT_MEETINGS) | RAM needed | vCPU needed | Suggested machine type |
|---|---|---|---|
| 2 (what you already tested) | ~4GB | ~4.7 | `e2-standard-4` (4 vCPU / 16GB) |
| 5 | ~7GB | ~9.75 | `e2-highcpu-16` (16 vCPU / 16GB) |

`e2-standard-4` is RAM-generous relative to what N=2 needs (16GB vs. ~4GB) —
that's deliberate slack for `shm_size`, OS page cache, and Chrome memory
spikes, not waste. For N=5, `e2-highcpu-16` matches the CPU-heavy profile
better than `e2-standard-16` would (same vCPU, half the RAM, cheaper, and
this workload is CPU-bound per your own numbers).

Boot disk: 30GB balanced persistent disk (default GCE boot disks are often
10GB, too tight once you count the OS, Docker images for all three
services, and build cache). Recordings themselves don't need disk
provisioning beyond that — each is deleted locally right after a successful
Supabase upload (`MeetingLifecycle.js`'s `fs.unlinkSync`), so local storage
never accumulates.

OS: **Debian 12 (bookworm)** — matches `meeting-bot`'s Dockerfile base
image family, keeps package versions/behavior consistent with what's
already been tested in this conversation's container work.

Reserve a **static external IP** for the VM before configuring anything
below that references its address (`FRONTEND_ORIGIN`, `VITE_API_URL`,
`GOOGLE_OAUTH_REDIRECT_URI`) — an ephemeral IP changing on VM restart would
silently break CORS and the OAuth redirect.

## 3. Installing Docker and Compose on the VM

SSH in (`gcloud compute ssh <instance-name>` — uses IAP tunneling by
default, no need to open port 22 to the public internet for this), then:

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
`docker-compose` — matches every command used earlier in this project).

## 4. Getting the repo onto the VM

Code only — never the `.env`/auth files, which are gitignored on purpose
and go over in step 5 instead.

```bash
git clone <your-repo-url> meeting-recorder-bot
cd meeting-recorder-bot
```

Use an SSH deploy key or HTTPS PAT if the repo is private. Given the
history-leak warning above, confirm the rotation/purge happened before
this clone — cloning after the purge means the bad commit is simply gone
from what lands on the VM too.

## 5. Getting `.env` files and auth state onto the VM securely

Four files need to exist on the VM, none of them tracked in git:
`backend/.env`, `meeting-bot/.env`, `frontend/.env`, plus
`meeting-bot/auth.json` and `meeting-bot/zoom-auth.json`.

From your local machine (not the VM), use `gcloud compute scp` — it tunnels
over SSH/IAP the same way `gcloud compute ssh` does, so nothing here rides
over an unencrypted channel:

```bash
gcloud compute scp backend/.env <instance-name>:~/meeting-recorder-bot/backend/.env
gcloud compute scp meeting-bot/.env <instance-name>:~/meeting-recorder-bot/meeting-bot/.env
gcloud compute scp frontend/.env <instance-name>:~/meeting-recorder-bot/frontend/.env
gcloud compute scp meeting-bot/auth.json <instance-name>:~/meeting-recorder-bot/meeting-bot/auth.json
gcloud compute scp meeting-bot/zoom-auth.json <instance-name>:~/meeting-recorder-bot/meeting-bot/zoom-auth.json
```

Then on the VM, lock down permissions (these hold real secrets — Supabase
service-role key, bearer tokens, Google/Zoom session cookies):

```bash
chmod 600 backend/.env meeting-bot/.env frontend/.env meeting-bot/auth.json meeting-bot/zoom-auth.json
```

Before starting anything, update the values that were `localhost`-shaped
for local dev and now need the VM's real address:

- `backend/.env`: `FRONTEND_ORIGIN` → the frontend's real public URL;
  `GOOGLE_OAUTH_REDIRECT_URI` → `http://<static-ip-or-domain>:8000/calendar/oauth/callback`
  (and add that exact URI to the OAuth client's authorized redirects in
  Google Cloud Console, or the calendar integration will reject it).
  `MEETING_BOT_URL` stays `http://meeting-bot:3000` — that's Compose's
  internal service-name DNS, unrelated to the VM's public address, same on
  the VM as it is locally.
- `frontend/.env`: `VITE_API_URL` → the backend's real public URL
  (`http://<static-ip-or-domain>:8000`).
- `meeting-bot/.env`: `BACKEND_WEBHOOK_URL` stays `http://backend:8000/...`
  for the same internal-DNS reason. Set `MAX_CONCURRENT_MEETINGS` to
  whatever N you sized the VM for in step 2, and update
  `docker-compose.prod.yml`'s `shm_size` to match (~2gb × N as a starting
  point, then verify against real usage).

(One-time only, not a recurring re-transfer: once the VM's `meeting-bot`
container is running, its own `AuthKeepAlive` job takes over refreshing
`auth.json`/`zoom-auth.json` in place — see
`docs/auth-keepalive-runbook.md`. You only need to scp fresh copies again
if the runbook's `ALERT` fires.)

## 6. Firewall rules

GCP denies all inbound by default — you only open what's actually needed.

| Port | Who calls it | Public? |
|---|---|---|
| 80 (frontend) | End users' browsers | Yes |
| 8000 (backend) | End users' browsers call it *directly* — `VITE_API_URL` is baked into the frontend bundle at build time, the browser doesn't go through nginx for API calls | Yes |
| 3000 (meeting-bot) | Only `backend`, over the internal `meeting-net` Docker bridge | **No** — not published to the host at all in `docker-compose.prod.yml`, so there's nothing to firewall |
| 22 (SSH) | You | Prefer IAP tunneling (`gcloud compute ssh`, no firewall rule needed) over opening 22 to `0.0.0.0/0` |

```bash
gcloud compute firewall-rules create allow-frontend --allow=tcp:80 --target-tags=meeting-bot-vm --source-ranges=0.0.0.0/0
gcloud compute firewall-rules create allow-backend --allow=tcp:8000 --target-tags=meeting-bot-vm --source-ranges=0.0.0.0/0
```

(apply the `meeting-bot-vm` network tag to the instance; skip an SSH rule
entirely if using IAP tunneling, which is the default for `gcloud compute
ssh`/`scp` and doesn't need an inbound firewall allow at all.)

**Known gap, not covered by this guide**: everything above is plain HTTP —
there's no TLS anywhere in this stack yet. Real user credentials
(Supabase auth tokens, the bearer token) would cross the wire unencrypted.
Before real traffic, put a reverse proxy in front (Caddy on the VM is the
lowest-effort path — automatic Let's Encrypt certs — or a GCP HTTP(S) Load
Balancer with a Google-managed cert) and only open 443. A further
improvement beyond that: proxy `/api` through nginx to `backend:8000`
internally so the browser only ever talks to one public port/origin,
removing the need to expose 8000 publicly at all — that changes
`VITE_API_URL` to a relative path and is a real (small) app change, not
just deployment config, so it's flagged here rather than done silently.

## 7. Running Compose as a systemd service (survives reboots)

`restart: unless-stopped` (already in `docker-compose.prod.yml`) handles
individual container crashes once Docker is running. For the whole stack
to come back after a VM reboot, wrap it in a systemd unit rather than
relying on Docker's own restart-policy-on-daemon-start behavior — this
gives you one clean `systemctl` handle for start/stop/status instead of
implicit behavior to remember:

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
WorkingDirectory=/home/YOUR_USER/meeting-recorder-bot
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml up -d
ExecStop=/usr/bin/docker compose -f docker-compose.prod.yml down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
```

Replace `YOUR_USER` with the actual path, then:

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
`docs/auth-keepalive-runbook.md`) and the `[server]`/`[Lifecycle]` lines
from `meeting-bot` for join/recording failures.

**Redeploy after pulling new code** — rebuild and recreate only what
changed, to avoid unnecessary downtime on unaffected services:
```bash
git pull
docker compose -f docker-compose.prod.yml build meeting-bot
docker compose -f docker-compose.prod.yml up -d meeting-bot
```
For a frontend change, remember the build-arg substitution needs the vars
in your shell first (Compose reads `${VITE_API_URL}` etc. from the shell
environment at `build` time, not from `frontend/.env`'s `env_file:`, which
only injects runtime env into a container — nginx doesn't need these,
Vite bakes them into the JS bundle at build time instead):
```bash
set -a; source frontend/.env; set +a
docker compose -f docker-compose.prod.yml build frontend
docker compose -f docker-compose.prod.yml up -d frontend
```
Or rebuild everything at once: `docker compose -f docker-compose.prod.yml up -d --build`.

## 9. Alerting on `AuthKeepAlive` failures (not yet set up)

`docs/auth-keepalive-runbook.md` already covers what the `[AuthKeepAlive]
ALERT` log block means and how to fix it once you see it — but "someone
has to be watching `docker compose logs`" isn't real alerting. This
section is written but **not executed or tested against a real GCP
project** (no `gcloud` access from this environment) — verify each step
against current GCP docs before relying on it, the way everything else in
this guide was actually build/run-tested.

**Step 1 — get container logs into Cloud Logging.** By default they stay
local to the VM (`docker logs`). Point Docker's logging driver at Cloud
Logging directly, per-service, in `docker-compose.prod.yml`:
```yaml
  meeting-bot:
    # ...existing config...
    logging:
      driver: gcplogs
      options:
        gcp-log-cmd: "true"
```
Requires the VM's service account to have the `roles/logging.logWriter`
IAM role (GCE default service accounts usually already have it via the
`logging-write` access scope — confirm on the instance, don't assume).

**Step 2 — a log-based metric counting the ALERT string:**
```bash
gcloud logging metrics create auth_keepalive_alert \
  --description="Counts AuthKeepAlive ALERT lines from meeting-bot" \
  --log-filter='resource.type="gce_instance" AND textPayload:"[AuthKeepAlive] ALERT"'
```

**Step 3 — an alerting policy on that metric**, e.g. via the Console
(Monitoring → Alerting → Create Policy → select the
`logging/user/auth_keepalive_alert` metric → condition: count > 0 over a
5-minute window) — the CLI form needs a full policy JSON/YAML body, more
error-prone to get right blind than doing this one step in the Console.

**Step 4 — a notification channel** (email is simplest to start:
Monitoring → Alerting → Notification Channels → Email), attached to the
policy from step 3.

Once wired up, an expired Google/Zoom session gets someone paged instead
of silently sitting in logs until a real user's recording fails.
