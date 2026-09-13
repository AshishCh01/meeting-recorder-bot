# Backend tests

These run against a **real PostgreSQL**, not SQLite, and five of the test files
additionally need **Redis**. Both run automatically in CI — see "In CI" below,
and note `PYTEST_REQUIRE_NO_SKIPS` if you are about to add a conditional skip.

Postgres because the behaviour under test in `test_scheduler_claim.py` is two
connections racing a conditional `UPDATE`; that depends on Postgres row-level
locking and `READ COMMITTED` re-evaluation, which SQLite does not reproduce (it
serialises writers at the database level, so the race cannot even be staged).

The Postgres image must be a **pgvector** one. `test_transcription_queue.py`
writes real `meeting_chunks` rows, and that table's `embedding` column is a
`Vector(768)` — `conftest.py` runs `CREATE EXTENSION IF NOT EXISTS vector`
before creating the schema, which fails loudly on a plain `postgres` image.

`conftest.py` reads `TEST_DATABASE_URL` and refuses to run without it, so a
stray `pytest` can never point the suite at the real `DATABASE_URL` in
`backend/.env`. It creates and drops `users`, `meetings` and `meeting_chunks`,
and truncates them between tests — use a throwaway database.

**The suite never reads `backend/.env`.** `conftest.py` sets `IGNORE_DOTENV=1`
before any `app.*` import, and both routes a `.env` has into the process honour
it: `Settings`' `env_file` and `load_dotenv()` in `app/db/database.py`. Tests see
code defaults, the stub values `conftest.py` sets, and anything you export
yourself, so results no longer depend on whose machine runs them, and no real
provider key is ever in the test process. `test_env_isolation.py` enforces
this and fails with setting names only, never values.

- A test that needs a non-default setting sets it with
  `monkeypatch.setattr(settings, "...", value)`. Don't rely on a default either:
  pin every flag the test's result depends on. Running the suite with each
  boolean setting inverted is how the scheduler tests' two hidden dependencies
  were found.
- A shell or CI override still works (`BOT_DISPATCH_USE_QUEUE=true pytest`),
  except for credentials. The isolation test rejects a real provider key from
  any source; stub the call instead.
- Never put `os.environ` or `settings` into an assertion or a print. pytest
  renders both operands of a failed assert, and `repr(settings)` contains every
  key it holds.

`TEST_REDIS_URL` is optional: without it, `test_transcription_queue.py` skips
entirely, the three Redis-backed tests in `test_bot_dispatch_queue.py` skip
(the other 29 in that file run against Postgres alone), the seven tests in
`test_bot_pool.py` that exercise the real heartbeat cache skip (the other 24
run against Postgres alone), and 21 of the 35 in `test_rate_limit.py` skip
(the other 14 run without it - they are the fail-open ones, which need a Redis
that is *not* there), and so does the one real-Redis test in
`test_webhook_enqueue_failure.py` (the other 8 in that file run against
Postgres alone). The scheduler, status-machine, webhook and
scheduled-without-time tests still run. Full suite: 311 with Redis, 259 passed
/ 52 skipped without.

**Rate limiting is off unless a test turns it on.** `conftest.py` sets
`RATE_LIMIT_ENABLED=false`, because Phase B1's limiter is an `async def`
dependency on `POST /meetings` and both chat routes - so every test that posts
to one of those would otherwise open a connection to `settings.redis_url`,
which in this suite is the *code default* (`redis://localhost:6379`), not
`TEST_REDIS_URL`. Those tests would still pass (the limiter fails open), but
each would carry a real connection attempt and an error log for a subsystem it
is not about, and the result would quietly depend on whether the machine
happens to be running a Redis on 6379. `test_rate_limit.py` turns it back on
with `monkeypatch`, which is the same rule as every other flag.

`tests/fake_bot_pool.py` is a helper, not a test module: it holds the `FakeBot`
and `FakeBotPool` stand-ins the three bot-pool files use. It deliberately
delegates its refusal *messages* to `bot_registry`'s own helpers rather than
rewording them — the exhaustion reason is what the user whose meeting was not
recorded ends up reading, and a fake that phrased it its own way let the real
wording drift with every test still green, which it promptly did the first time
those strings were written by hand. The split it enforces is
deliberate — the *dispatcher's* tests fake the registry so they need only
Postgres, while anything that says "cache" uses the real Redis one, because a
fake of a cache proves nothing about the cache. The Redis database is **flushed** around
every test in that module, so point it at a throwaway too.

## Running

Start the two throwaway containers (non-default ports, so they cannot collide
with anything already running locally):

```bash
docker run -d --rm --name meetiq-test-pg \
  -e POSTGRES_PASSWORD=testpw -e POSTGRES_DB=meetiq_test \
  -p 55432:5432 pgvector/pgvector:pg18

docker run -d --rm --name meetiq-test-redis \
  -p 56379:6379 redis:7-alpine
```

Then, from `backend/`:

```bash
TEST_DATABASE_URL=postgresql://postgres:testpw@localhost:55432/meetiq_test \
TEST_REDIS_URL=redis://localhost:56379 \
  python -m pytest
```

PowerShell:

```powershell
$env:TEST_DATABASE_URL = "postgresql://postgres:testpw@localhost:55432/meetiq_test"
$env:TEST_REDIS_URL = "redis://localhost:56379"
python -m pytest
```

Tear down with `docker stop meetiq-test-pg meetiq-test-redis`.

## In CI

`.github/workflows/ci.yml` (Phase B2) runs this suite on every push to `main`
and every pull request, on **Python 3.12** — which is what `backend/Dockerfile`
deploys on, and which nothing had ever run this code on before that workflow
existed. `Dockerfile.dev` has since moved to 3.12 as well, so local containers
match CI and production; the developer venv is 3.13. It uses the
same two images and the **same host ports** as the commands above, so the
invocation in this file is the invocation that runs in CI, character for
character. No repository secret is involved, and the workflow has a step that
fails if one ever is.

### `PYTEST_REQUIRE_NO_SKIPS=1`

**A skip is a failure when this is set.** CI sets it; nothing else does.

The reason is the numbers in the section above: with Postgres but no Redis this
suite reports `259 passed, 52 skipped` and exits **0**. Those 52 are B1's
atomicity gate, C3's two-hosts-two-meetings gate, C4's per-platform auth gate,
A3's queue-durability gate and B3's real-Redis enqueue-recovery test — the
concurrency work, and precisely the tests
nobody re-runs by hand. A workflow that provisioned Postgres and forgot Redis
would be green and blind to all of it, which is worse than having no CI at all:
it converts "nobody ran the tests" into "the tests passed".

The guard is a `pytest_sessionfinish` hook at the bottom of `conftest.py`. It
lists every test that skipped and why, then sets a failing exit status. It is
opt-in rather than always-on because locally a partial run is genuinely useful
— run without Redis and you get the 259 tests that do not need one, plus a note
about what you missed, instead of a red suite.

It refuses *any* skip, not just Redis ones. There is no legitimately
conditional test here today (with both services: `311 passed`, zero skipped),
so a new skip is a question someone should have to answer in a pull request.

## What's covered

| File | Phase | What it proves |
|---|---|---|
| `test_env_isolation.py` | B | No `Settings` field differs from its code default unless `conftest.py` or the runner's own environment set it, and never a credential; no key appears in `os.environ` that neither set. Failed on the old code listing 10 settings and 12 environment variables that came from `backend/.env`. |
| `test_scheduler_claim.py` | A1 | Two replicas sweeping the same due meeting dispatch exactly one bot; the missed-window and calendar-revalidation paths still behave with the claim moved ahead of them. |
| `test_transcription_queue.py` | A3 | A queued job survives with no worker running; a re-index interrupted between its delete and insert keeps the meeting's chunks; a retry over an existing transcript never calls Gemini; exhausted retries write a terminal state. |
| `test_observability.py` | A4 | Sentry is disabled and harmless with no `SENTRY_DSN`; a request that raises inside a route produces an event with no Supabase JWT anywhere in it - header, frame locals or exception message; `meeting_id`/`user_id` ride along on transcription events. |
| `test_bot_dispatch_queue.py` | C2 | A meeting requested while the recorder is full reaches `queued` and joins when capacity frees; a queued meeting survives the sweep that would have killed it in `joining`, and is still swept at its own TTL; a deleted or stopped meeting is dropped rather than joined; a 409 re-queues instead of failing; exhausted waiting writes a terminal failure naming the cause; stopping a queued meeting cancels it without calling the bot, and a stop that loses the race to the dispatcher stops the bot rather than overwriting the claim; the flag off still posts synchronously, raises on a busy bot, and enqueues nothing. |
| `test_bot_pool.py` | C3 | Two meetings dispatched at once against two single-slot recorders land one on each, read off `bot_host_id`; a recorder that stops answering freezes its cached `last_seen` rather than refreshing it with a failure, and drops out once past the TTL, while the survivor takes the next meeting on its first attempt; **the existing watchdog already sweeps a meeting whose host died** — no new sweep code, and the module mentions no host at all; stop, delete and re-upload reach the URL of the host the meeting actually landed on; an unresolvable host is a 409, never a 500, and never a call to some other recorder; a `queued` meeting cancels without the host column being resolved at all; and an empty cache after a Redis restart repopulates in one poll cycle instead of reading as "all hosts down". |
| `test_bot_auth_health.py` | C4 | A host whose Google session has expired stops receiving Google meetings while still receiving Zoom ones — per platform, never per host, because the two identities expire independently; `unknown` (booted, first keepalive cycle not finished) and a bot that reports no health at all both count as usable, so a restart is not an outage and the backend can ship ahead of the bots; a pool with every Google session dead fails the meeting with a message naming the credential and the command that fixes it, worded distinctly from the busy case and from a mixed one; and restoring the credential brings the host back on the next heartbeat with nothing restarted. |
| `test_meeting_status_machine.py` | B3 | The status machine closes: every non-terminal status but `scheduled` has a TTL and no status literal in `app/` is outside the vocabulary; past its TTL each of the six is failed and inside it none is, with every TTL pinned to a distinct value so a crossed mapping cannot pass; the scheduler's claim restarts the watchdog clock, and a claim that crashes mid-revalidation is still swept; late webhook pings and late failure reports never move a meeting backwards; a session with no file and a completed report with a missing file both end `failed`. The four findings it turned up, and how each was resolved, are in docs/scaling-plan.md B3. |
| `test_webhook_idempotency.py` | B3 | The same `completed` payload delivered twice - sequentially or racing on a barrier - calls `submit_transcription` once and answers `already_processed`; so does one arriving after `completed`; a mismatched `user_id` is a 409 before any storage call or row write; a duplicate progress ping answers `received` and only moves `updated_at`. |
| `test_webhook_enqueue_failure.py` | B3 finding 1 | A transcription hand-off that fails does not strand the meeting in `transcribing`: the claim is undone to `failed` with `recording_url` cleared and the webhook answers 503, the bot's retry claims it again and submits exactly one job, later deliveries stay `already_processed`, and the undo never overwrites a meeting that already moved on; concurrent deliveries around a failed hand-off still submit at most once; a real unreachable Redis followed by a real one leaves exactly one job in arq. Also pins the residual: a crash between claim and enqueue is bounded by the watchdog and recovered by Retry. |
| `test_webhook_late_completed.py` | B3 finding 2 | A `completed` report may revive a `failed` meeting only if no completed report was ever accepted for it (`recording_url IS NULL`): redeliveries after a transcription failure or a swept `transcribing` are `already_processed` and keep their failure, while late reports for meetings the watchdog failed before any hand-off, a storage miss, or a bot dispatch wrongly failed still recover - including when two race. |
| `test_scheduled_without_time.py` | B3 finding 3 | A `scheduled` row with no `scheduled_at` is failed by the watchdog once past the joining TTL; valid bookings are never swept, whatever their age or whether the scheduler is enabled; `POST /meetings` inserts its dispatch status directly, so a crash mid-create leaves no such row. |
| `test_webhook_uploading_pings.py` | B3 finding 4 | No code change, pinned: a progress ping can only meet `uploading` from a still-live session during a re-upload the bot refuses, where it is the true state, and the meeting still closes on the bot's final report. |
| `test_jwt_auth.py` | A5 | Every rejection path 401s - expired, wrong signature, wrong `aud`, wrong `iss`, `alg: none`, HS256-signed-with-the-public-key; 50 unknown-`kid` requests cost exactly one JWKS refetch; the fallback wrapper keeps an unverifiable token logged in and counts itself; the user-row lookup happens once, not per request. |

`test_observability.py` needs neither Postgres nor Redis of its own, but it
lives in the same suite so `conftest.py`'s `TEST_DATABASE_URL` interlock still
applies. It never opens a network connection: `init_sentry` is handed a
`CapturingTransport` that keeps the envelope, and the placeholder DSN points at
`.invalid`.

`test_jwt_auth.py` uses Postgres (the user-row cache test counts real queries)
but no network: it generates its own ES256 key pair, serves a JWKS through a
stubbed `urllib.request.urlopen`, and counts the stub's calls - that count
*is* the unknown-`kid` refetch assertion.

What it deliberately does **not** prove is that `JWT_AUDIENCE` and `JWT_ISSUER`
match what Supabase actually issues. Its tokens are self-signed, so they carry
whatever claims the fixture chose; a wrong value in `config.py` would pass this
suite and 401 every user in production. Those two were read off a real access
token instead - see docs/scaling-plan.md A5.

Test dependencies live in `requirements-dev.txt`, which `Dockerfile.dev`
installs. The production image (`backend/Dockerfile`) installs
`requirements.txt` only — but note that `arq` and `redis` are in
**`requirements.txt`**, not the dev file: they are runtime dependencies of both
the API (which enqueues) and the worker container.
