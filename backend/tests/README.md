# Backend tests

These run against a **real PostgreSQL**, not SQLite, and the Phase A3 tests
additionally need **Redis**.

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
that is *not* there). The scheduler tests still run. Full suite: 217 with
Redis, 166 passed / 51 skipped without.

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
