# Scaling & Production-Readiness Plan

Date: 2026-09-08 (restructured 2026-09-09 around execution phases)
Status: **A1 shipped** (commit `8dc0844`). A2 onwards not started.
Deployment target: single AWS EC2 instance — see `docs/aws-ec2-deploy.md`.

Scope: what it takes to move this from a well-built single-node system to one
that scales. The code quality is already production-grade — atomic status
transitions, provider fallbacks on every AI stage, a watchdog backstop, RLS,
constant-time token comparison. What is missing is the step from "one box" to
"many boxes."

**This document is organised by execution order, not by priority.** An earlier
draft was numbered as steps 1–7; those numbers still appear in conversation, so
[Appendix A](#appendix-a-step-number--phase) maps them to the phases below.

---

## The plan at a glance

| Phase | What ships | Risk | Revert by |
|---|---|---|---|
| ~~**A1**~~ | ~~Scheduler claims rows atomically~~ — **done, `8dc0844`** | Low | Reverting one function |
| **A2** | Sweep loops confined to one replica; scale out | Very low | Scaling back to 1 replica |
| 🎯 | **Milestone: horizontally scalable. Safe to stop here indefinitely.** | | |
| | *A2 is blocked on infrastructure — on the current single EC2 instance, go A1 → A3.* | | |
| **A3** | Transcription moves to a queue + worker | Medium | Env flag back to the executor |
| **A4** | Sentry | None | Removing the DSN |
| **A5** | JWTs verified locally | High | Fallback wrapper, then revert |
| **B** | Tests, structured logging, rate limiting | — | Deferred by decision |
| **C** | Bot pool — the recording tier | High | Per sub-step |

**The rule for every phase:** independently deployable, independently revertable,
and it leaves the system in a coherent state. Never half-migrated.

**Why five phases and not one:** these changes have four completely different
failure signatures — a meeting dispatched twice, a deploy losing in-flight jobs,
a `completed` meeting silently left with no searchable chunks, and a 401 storm.
Bundled, a failure means bisecting three subsystems under pressure. Separated,
each failure points at exactly one deploy. Auth and the queue are the worst pair
to combine, because a chunkless meeting is *silent* — it looks completed in the
UI — and may not surface for days, long enough to blame the wrong change.

---

## Where the three tiers stand today

| Tier | Scales horizontally? | Blocker |
|---|---|---|
| **frontend** | **Yes, today.** nginx serving a static bundle. | None. |
| **backend** | Code is ready; deployment is not. | A1 done. A2 needs somewhere to put a second replica — the current target is one EC2 instance running docker-compose. |
| **meeting-bot** | **No.** Vertical only. | In-memory registry, shared auth files, single `MEETING_BOT_URL`. Phase C. |

Two things narrow the work, and both are worth knowing before starting:

**The watchdog is already multi-replica safe.** `sweep_stale_meetings`
([watchdog.py:54-64](../backend/app/services/watchdog.py#L54-L64)) is a single
`UPDATE Meeting WHERE status = :status AND updated_at < :cutoff`. That is atomic.
Run it on ten replicas and each row still transitions exactly once — the losers
simply get `rowcount == 0`. Wasteful, not dangerous. The scheduler is the only
loop that actually races.

**Chat history is already durable.** As of the `chat_messages` work, a cold
session rehydrates from the database rather than from a per-process
`LRUSessionCache`. Before that, two replicas meant two different conversations
depending on which one you hit. That blocker is already gone.

---

# Phase A1 — Scheduler claims rows atomically ✅ done

> **Shipped** in `8dc0844`. **Risk:** low. **Revert:** one function.
>
> Landed as `_claim()` in `scheduler.py`, plus 16 tests in `backend/tests/`
> against a real Postgres. Two consequences that were not in this plan's
> original text and are worth knowing:
>
> - **The reschedule path had to hand the claim back.** Claiming before
>   revalidation means the "event moved to a later time" branch holds a row
>   in `joining`; it now returns it to `scheduled`, or no later sweep would
>   ever pick it up and the watchdog would eventually fail it.
> - **`_mark_failed` stayed unconditional, deliberately.** Every remaining
>   caller is inside revalidation, i.e. post-claim, where no other replica
>   can be writing. Making it conditional would have silently no-op'd them
>   all.

**This is a no-op the day you deploy it.** With one replica the claim always
succeeds, so behaviour is identical. You are installing the safety mechanism
*before* the thing that stresses it — which is exactly why it must not be
bundled with A2.

## Problem

[scheduler.py:117-137](../backend/app/services/scheduler.py#L117-L137) reads and
writes in two separate statements:

```python
due_meetings = db.query(Meeting).filter(
    Meeting.status == "scheduled",
    Meeting.scheduled_at.isnot(None),
    Meeting.scheduled_at <= due_cutoff,
).all()

for meeting in due_meetings:
    ...
    meeting.status = "joining"
    db.commit()
    trigger_bot_join(...)
```

Two replicas sweeping concurrently both read the same row as `scheduled`, both
write `joining`, and both call `trigger_bot_join` — the same meeting dispatched
to the bot twice. Nothing in between is atomic.

This is not a pattern you have to invent. The webhook path already does it
correctly, in this codebase
([webhooks.py:71-84](../backend/app/api/webhooks.py#L71-L84)):

```python
result = db.execute(
    update(Meeting)
    .where(Meeting.id == payload.meeting_id,
           Meeting.status.notin_(["transcribing", "completed"]))
    .values(status="transcribing", ...)
)
db.commit()
if result.rowcount == 0:
    return {"status": "already_processed"}
```

A1 is applying that same in-house pattern to the scheduler.

## Design

Keep the `SELECT` as the candidate list, but treat it as advisory. Before doing
any work on a meeting, claim it with a conditional `UPDATE` and act only if
`rowcount == 1`:

```python
claimed = db.execute(
    update(Meeting)
    .where(Meeting.id == meeting.id, Meeting.status == "scheduled")
    .values(status="joining")
)
db.commit()
if claimed.rowcount == 0:
    continue          # another replica got it — not ours to run
```

Ordering matters and is easy to get subtly wrong:

- **Claim before `_revalidate_calendar_meeting`.** That call makes a network
  request to Google. Two replicas both revalidating the same meeting is wasted
  quota and duplicated latency even if only one eventually joins.
- **The claim must be the transition to `joining`,** not a separate flag. A
  `claimed_by` column would work but is more schema than this needs — the
  `scheduled → joining` transition is already the thing that must happen once.
- **`_mark_failed` on the missed-window path should also be conditional.** Two
  replicas both writing `failed` is harmless (same value), but making it
  conditional keeps one rule rather than two.
- **The failure path after `trigger_bot_join` raises** already sets `failed`
  unconditionally ([scheduler.py:145-149](../backend/app/services/scheduler.py#L145-L149)).
  Since only the claiming replica reaches it, that is fine as-is.

## Files touched

- `backend/app/services/scheduler.py` — `trigger_due_meetings`, and
  `_mark_failed` if you make it conditional.

## Gate — do not advance to A2 until

Do not gate on "the diff looks right." Prove the race is closed:

1. Two `SessionLocal()` connections in one test. Both `SELECT` the same
   `scheduled` row. Both attempt the claim. **Assert exactly one gets
   `rowcount == 1`.**
2. Monkeypatch `trigger_bot_join` to append to a list, run two
   `trigger_due_meetings` sweeps concurrently against one due meeting, assert the
   list has exactly one entry.
3. Confirm the missed-window and revalidation-skip paths still behave — those are
   easy to break when reordering claim vs. revalidate.

Write these as real test files even though there is no suite yet
(see [Phase B](#phase-b--tests-logging-rate-limiting-deferred)). They are the
proof the change works, not extra credit.

**All three landed**, and the suite was additionally verified by reverting
`trigger_due_meetings` to the pre-A1 version: three tests failed, including
`bot dispatched 2 times`. A concurrency fix that cannot be shown to fail on
the old code has not been shown to do anything.

## Effort

Small — a focused change to one function. The test setup is most of the work, and
worth doing properly: this is the correctness keystone for the whole plan.

---

# Phase A2 — Confine the sweep loops, then scale out

> **Ships:** config only, no code. **Risk:** very low. **Revert:** scale back to 1.
>
> ⚠️ **Blocked on deployment infrastructure, not on code.** The current
> target is a single EC2 instance running docker-compose
> (`docs/aws-ec2-deploy.md`) — there is nowhere to put a second replica,
> so this phase is a no-op today. It becomes ten minutes of work the day
> you move behind a load balancer (an ALB with a second instance, ECS, or
> App Runner). **Until then, skip to A3** — see the milestone note above.

## Problem

[main.py:75-84](../backend/app/main.py#L75-L84) starts both loops in every
instance's lifespan. With N replicas you get N schedulers and N watchdogs.

Per the note above: the **watchdog is safe** (atomic single-statement update),
just wasteful. The **scheduler is the dangerous one** — and A1 already fixed that
at the data layer.

So this phase is strictly about cost and clarity, not correctness. N replicas ×
a sweep every interval means N× the Google Calendar revalidation calls, N× the
database churn, and N× the log noise for one unit of work.

## Design — two options

**Option A: config-only, available today.** Both loops are already gated by
env-driven booleans ([config.py:86,96](../backend/app/config.py#L86)):

```
WATCHDOG_ENABLED=false
CALENDAR_SCHEDULER_ENABLED=false
```

Deploy N web replicas with both off, plus exactly one "sweeper" replica with them
on. Zero code. The weakness is that it is a deployment invariant nobody can see
from the code — someone scales the wrong service group and the invariant breaks
silently. **Acceptable only because A1 makes that failure non-corrupting.**

**Option B: advisory-lock leader election.** Each replica tries
`pg_try_advisory_lock(<constant>)` before each sweep and skips if it does not get
it. No new infrastructure — Postgres is already there — and the lock releases
automatically when the connection dies, so a crashed leader does not wedge the
system. Self-describing in the code, and correct regardless of topology.

**Recommendation:** ship Option A now, Option B when you actually run more than
one replica in anger. Do not build Option B before you need it.

## Files touched

- Option A: environment/deployment config only.
- Option B: `backend/app/main.py` (wrap the sweep bodies), possibly a small
  `backend/app/services/leader.py`.

## Gate

- Staging first: two replicas, assert exactly one performs a given sweep.
- Confirm the sweeper replica is the one with the flags on, and that a deploy
  cannot accidentally roll it as a web replica.
- For Option B: kill the leader, assert the other takes over next interval.

---

## 🎯 Milestone — you are horizontally scalable

**After A1 and A2 you can safely run N backend replicas.** One of those two phases
was a single function; the other was environment variables.

This is a real stopping point, not a waypoint. A3–A5 make horizontal scaling
*good*; they are not what makes it *correct*:

- **Without A3,** transcription still works multi-replica — it runs on whichever
  replica received the webhook. The downside is that a deploy kills in-flight
  jobs and the watchdog reaps them to `failed`. Annoying, visible, recoverable.
- **Without A5,** auth still works. You pay a Supabase round-trip per request and
  put N× load on that dependency. At 2–3 replicas that is fine.

Run here as long as you like. Take A3 and A5 as deliberate hardening projects
when you have the appetite.

**On the current deployment this milestone is not yet reachable**, because
A2 needs a load balancer and a second instance that do not exist. That does
not block anything: A1 has already removed the correctness barrier, so the
backend is *ready* to be replicated whenever the infrastructure appears.
The practical running order is therefore **A1 → A3 → A4 → A5**, with A2
slotted in at whatever point you move off the single instance.

A3 is the right next step regardless of scaling, because it fixes a bug that
bites on one instance: every backend redeploy kills in-flight transcriptions,
and the user sees a failed recording they did not cause.

---

# Phase A3 — Transcription moves to a queue

> **Ships:** alone, in two deploys (see below). **Risk:** medium — the failure is
> *silent*. **Revert:** env flag back to the executor.

## Problem

[transcription_service.py:11](../backend/app/services/transcription_service.py#L11):

```python
transcription_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="transcribe")
```

Download, Gemini transcription, chunking and embedding all run inside the API
process. Three consequences:

1. **Web and transcription capacity cannot scale independently.** More replicas
   for request throughput also multiplies concurrent load on the AI providers.
2. **A deploy or restart kills in-flight jobs.** The meeting sits in
   `transcribing` until the watchdog TTL sweeps it to `failed`. The watchdog
   makes this recoverable, not invisible — the user still sees a failure they did
   not cause.
3. **A burst of uploads starves request handling.** Four heavy threads plus the
   GIL plus the event loop in one process.

`submit_transcription`
([transcription_service.py:418-440](../backend/app/services/transcription_service.py#L418-L440))
already wraps the executor so an escaped exception marks the meeting failed
rather than vanishing into an unchecked `Future`. That defensive wrapper is
exactly the contract a real queue gives you for free, with durability added.

## Design

The job payload is already tiny and serializable — `(meeting_id, storage_path)` —
so this is a clean extraction, not a refactor.

**Broker.** Redis. You need it for Phase B rate limiting and Phase C dispatch
anyway, so it earns its keep three times.

**Library: `arq`. Decided — not an open question.** It is asyncio-native like
FastAPI and far smaller than Celery, and the sync task body is handled the way
this codebase already handles every other blocking call: `await
asyncio.to_thread(transcribe_recording, ...)`, the same pattern as
`chat_service.py`'s tool dispatch and `main.py`'s `_run_with_session`. Celery's
prefork model would suit a synchronous body marginally better in isolation, but
it is a much heavier dependency for one job type and a second idiom for this
codebase to carry. Do not hybridise, and do not revisit this mid-implementation.

**Keep `submit_transcription` as the seam.** Every call site already routes
through it ([meetings.py:140](../backend/app/api/meetings.py#L140),
[webhooks.py:85](../backend/app/api/webhooks.py#L85)). Change its body to enqueue
instead of `executor.submit`, and no call site moves. This is the most important
decision in the phase — it keeps the blast radius to one function.

**What the worker must own:**

- Its own `SessionLocal()` per job, closed on the same thread (the
  `_run_with_session` reasoning in
  [main.py:17-34](../backend/app/main.py#L17-L34) applies here too).
- **Retry safety.** The risk, and not the one an earlier draft of this document
  claimed. See [Retry safety, from the actual code](#retry-safety-from-the-actual-code)
  below — it is written from `index_transcript` and `transcribe_recording` as
  they stand, and it is the part of this phase to read before writing any code.
- Bounded retries, marking the meeting `failed` when exhausted — preserving what
  the `_on_done` callback does today.

**Do not remove the watchdog when this lands.** A queue reduces stuck
`transcribing` meetings; it does not eliminate them.

<a id="retry-safety-from-the-actual-code"></a>
## Retry safety, from the actual code

An earlier draft of this plan warned that *"a retried job must not double-write
chunks."* **That is wrong, and worth correcting rather than deleting**, because
building a guard against it would waste effort and miss the two real problems.

`index_transcript` ([embedding_service.py:179-240](../backend/app/services/embedding_service.py#L179-L240))
already deletes a meeting's chunks before inserting the new ones, and the insert
is a single `add_all` + `commit`. So a re-run **cannot** duplicate rows, and
cannot leave a partially-inserted set either: the insert is all-or-nothing.

### 1. Delete and insert are not atomic — a crash leaves *zero* chunks

The two writes are separate transactions:

```python
db.query(MeetingChunk).filter(MeetingChunk.meeting_id == meeting_id).delete()
db.commit()                      # <- commit 1: the old chunks are now gone

db.add_all(meeting_chunks)       # ...build and insert the new ones
meeting.embedding_provider = provider
db.commit()                      # <- commit 2
```

A process that dies between those two commits — an arq shutdown, a job timeout,
an OOM kill, a lost database connection — leaves the meeting with **no chunks at
all**. Two things make that worse than it sounds:

- **The meeting already says `completed`.** `transcribe_recording` sets
  `status = "completed"` and commits *before* it calls `index_transcript`
  ([transcription_service.py:317-322](../backend/app/services/transcription_service.py#L317-L322)).
  So the UI shows a finished meeting with a transcript, summary and action
  items, while RAG search over it returns nothing and chat answers every
  question with "that isn't in the transcript." Nothing errors. Nobody is
  paged.
- **A re-index of an already-good meeting destroys working data.** The delete
  commits first, so an interrupted retry is strictly worse than never having
  retried: chunks that were fine are now gone.

The window is narrow today because nothing kills the thread mid-function. A
queue with retries and worker shutdown is exactly what makes it reachable, which
is why it belongs in this phase and not in Phase B.

**There is a free detector for this state.** `embedding_provider` is set in the
same commit as the insert, so:

```sql
SELECT id FROM meetings WHERE status = 'completed' AND embedding_provider IS NULL;
```

identifies exactly the meetings that finished transcription but have no usable
index. Useful as a repair sweep, and as the assertion in the gate test below.

### 2. An arq retry re-bills the full pipeline

`transcribe_recording` has no guard at the top: it does not check whether the
meeting is already `completed`. A retry therefore re-runs everything —
re-download from Supabase Storage, silence detection, **re-upload to the Gemini
File API and a full re-transcription**, then a full re-embed.

The transcription call is the expensive one by a wide margin; `log_cost`'s
`meeting_total` line already reports `transcription_usd` and `embedding_usd`
separately, so the split is measurable rather than theoretical.

**The existing guard does not help here.** `webhooks.py` checks
`status.notin_(["transcribing", "completed"])` before *enqueuing*. Once a job
is on the queue, an arq retry re-executes the task directly and never passes
that check again. Under the current `ThreadPoolExecutor` there are no retries at
all, so this has never mattered — introducing a queue is what turns it into a
real cost exposure. A crash loop on one meeting could re-transcribe it on every
attempt.

### 3. What has to change to make the pipeline safely retryable

In rough order of value:

1. **Make the re-index one transaction.** Drop the `db.commit()` after the
   delete so delete-and-insert land together. One line, and it closes the
   zero-chunk window entirely. Do this even if nothing else on this list gets
   done.
2. **Resume instead of restarting.** The task should check, at execution time,
   what work already exists:
   - `meeting.transcript` already populated → skip transcription, go straight
     to `index_transcript`. This is the single biggest cost lever, because it
     makes a retry cost embedding-only instead of transcription + embedding.
   - `status == "completed"` *and* `embedding_provider` set → the job is fully
     done; return immediately.
   Put this check inside the task, not in `submit_transcription` — an arq retry
   never re-enters the enqueue path.
3. **Decide what an indexing failure means now.** Today `index_transcript`
   raising is swallowed: it writes `error_message = "Transcript ready, but RAG
   indexing failed"` and the meeting stays `completed`
   ([transcription_service.py:324-331](../backend/app/services/transcription_service.py#L324-L331)).
   Under a queue that is a wasted opportunity — the retry machinery could fix
   it. Let the exception propagate so arq retries the (now cheap, thanks to
   step 2) indexing step, and only swallow it once retries are exhausted.
4. **Bound the retries and preserve the failure contract.** `submit_transcription`'s
   `_on_done` callback currently marks a meeting `failed` when the task crashes
   ([transcription_service.py:418-440](../backend/app/services/transcription_service.py#L418-L440)).
   arq's `max_tries` replaces the retry-less executor, but something still has
   to write that terminal `failed` state when the last attempt gives up.
5. **Keep the watchdog.** It stops being the only backstop, but a job that never
   gets picked up at all is still invisible to arq.

Steps 1 and 2 are what make retries *safe* and *affordable* respectively. Steps
3-5 are about not losing behaviour the current code already has.

## Ship it in two deploys

This is the safety valve that makes A3 manageable:

1. **Infrastructure only.** Redis, the worker service, the task definition —
   with `submit_transcription` still using the `ThreadPoolExecutor`. Nothing
   changes behaviourally; you are proving the infrastructure runs.
2. **Flip the path.** `submit_transcription` enqueues, behind an env flag.

That turns the riskiest part of the phase into a config toggle you can flip at
10am on a Tuesday, rather than a deploy you have to roll back.

## Files touched

- `backend/requirements.txt` — broker client + queue library.
- `backend/app/services/transcription_service.py` — `submit_transcription` body;
  `transcribe_recording` becomes the task function.
- `backend/app/config.py` — `REDIS_URL`, worker concurrency, the cutover flag.
- New: `backend/app/worker.py` — worker entrypoint and task registry.
- `docker-compose.yml`, `docker-compose.prod.yml` — `redis` + `worker` services.
- `backend/Dockerfile` — no change if the worker reuses the image with a
  different command.

## Gate

- **Durability:** enqueue with the worker stopped; assert the job is still
  pending after an API restart, then runs when the worker comes up. This is the
  property the `ThreadPoolExecutor` cannot provide and the whole point of the
  phase.
- **Atomic re-index:** kill the worker between the delete and the insert (the
  easiest way is a fault injected into `index_transcript`), then assert the
  meeting still has its original chunk count — not zero. Assert on the row
  count and on `embedding_provider`, never on `meeting.status`, which says
  `completed` throughout and is exactly what makes this failure invisible.
- **Retries do not re-transcribe:** run a task whose meeting already has a
  `transcript`, and assert the Gemini transcription call is never made. Stub it
  to raise if invoked — that is the assertion.
- Assert the meeting reaches `failed` with a useful `error_message` when retries
  are exhausted.

## Effort

Largest of Phase A. Most of the risk is in retry safety, not plumbing — see the
section above before starting.

---

# Phase A4 — Sentry

> **Ships:** alone. **Risk:** none. **Revert:** remove the DSN.

Pulled forward out of its original grouping for one reason: **A5 is a change you
cannot safely validate without seeing its error rate in production.**

Roughly a day:

- Sentry for unhandled exceptions in both the API and the A3 worker.
- Structured logging with `meeting_id` and `user_id` on every line — there are
  currently 40 `print()` calls in `backend/app`.

The rest of the observability work (turning `log_cost` into a real metric) stays
in Phase B. This phase is deliberately the minimum that de-risks A5.

---

# Phase A5 — Verify JWTs locally

> **Ships:** alone, on a quiet deploy window. **Risk:** highest — fails for
> every user at once. **Revert:** fallback wrapper, then revert.

## Problem

[auth.py:11-34](../backend/app/api/auth.py#L11-L34) — `get_current_user` runs on
essentially every endpoint and does two things per request:

```python
auth_response = supabase.auth.get_user(token)   # network round-trip
...
db_user = db.query(User).filter(User.id == user_id).first()   # DB query
```

An outbound HTTPS call to Supabase in the hot path of every request. It sets a
latency floor nothing else can get under, makes your API's availability strictly
worse than Supabase Auth's, and exposes you to their rate limits. At N replicas
it becomes N× the load on that dependency.

## Why this one is the scariest

JWT verification is all-or-nothing. If your expected `aud` or `iss` does not
match what Supabase actually puts in the token, **every** token fails, **every**
request 401s, and every user is logged out simultaneously the moment you deploy.
Not a slow degradation — instant and total. Hence A4 first.

## Design

First establish which signing scheme your project uses. This changes the
implementation and the answer is project-age dependent — **check your project's
settings, do not assume:**

- **Asymmetric (newer projects):** RS256/ES256, public keys at
  `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`. Fetch once, cache, refresh on
  unknown `kid`. Nothing secret stored in the backend.
- **Legacy symmetric:** HS256 with the project's JWT secret as a shared secret.
  Simpler, but the secret must be stored and rotated like any other credential.

Verify signature, `exp`, `aud` and `iss`. Extract `sub` as the user id and
`email` from the claims. `cryptography` is already a dependency; add `PyJWT`.

**Cache the user-row upsert separately.** The `SELECT`-then-maybe-`INSERT` on
`users` is a second per-request cost, and once verification is local it becomes
the dominant round-trip. A small TTL cache of "user id X is known to exist"
removes it from the steady-state path. In-process is fine — a stale positive is
harmless, and the worst case is one redundant `INSERT` the primary key rejects.
**Skipping this makes the phase's win much smaller than expected.**

**Keep a fallback for the first release.** Wrap local verification and fall back
to `supabase.auth.get_user` on any verification error, logging when the fallback
fires. Once the logs show zero fallbacks under real traffic, delete it as a
separate small change.

**Accept the behaviour change deliberately:** local verification cannot see
server-side session revocation. A signed-out user's token stays valid until it
expires. With Supabase's default access-token lifetime this is a short window,
but decide it is acceptable rather than discover it.

## Files touched

- `backend/requirements.txt` — `PyJWT`.
- `backend/app/api/auth.py` — `get_current_user`.
- `backend/app/config.py` — JWKS URL / JWT secret, audience, issuer.
- New: `backend/app/services/jwt_verifier.py` — JWKS fetch/cache and verify.

## Gate

- Valid token → correct `sub`. Expired → 401. Wrong signature → 401. Wrong `aud`
  or `iss` → 401. **`alg: none` → 401** (test this explicitly; it is the classic
  JWT bug).
- Unknown `kid` triggers exactly one JWKS refetch, not one per request.
- Measure p50 latency on an authenticated endpoint before and after. It should be
  visibly faster — if it is not, something is still round-tripping.
- In production: fallback counter at zero before deleting the fallback.

---

## Phase A exit criteria

Do not declare Phase A done on "the code is merged." Prove all four:

1. Two schedulers racing one due meeting trigger exactly one bot join.
2. An API restart mid-transcription loses no work.
3. Two replicas serving traffic share chat threads and meeting state correctly.
4. An authenticated request makes zero outbound Supabase Auth calls in the steady
   state.

---

<a id="phase-b--tests-logging-rate-limiting-deferred"></a>
# Phase B — Tests, logging, rate limiting (deferred by decision)

## Tests

There is currently **no test suite**. `backend/.pytest_cache` exists but there
are no test files anywhere in the repo.

For a system whose correctness lives in retry ladders, provider fallbacks and a
multi-status state machine, this is the single largest production risk. Recent
chat work had to be verified with throwaway scripts that were then deleted.

Highest-value targets, in order:

1. **The meeting status machine** — every transition, and that no path leaves a
   meeting non-terminal forever.
2. **Webhook idempotency** — the `rowcount == 0` "already processed" branch.
3. **Scheduler claiming** — A1's race, permanently pinned.
4. **The AI fallback ladders** — Gemini→Groq for chat, Gemini→Sarvam for
   transcription, Gemini→Jina for embedding. Assert the fallback fires on
   transient errors and does *not* fire on a 400.
5. **`chat_service` reset semantics** — a mid-answer stream break resets and
   falls back rather than erroring, and a failed request stores nothing.

Add `pytest` + `pytest-asyncio`, and a Postgres service in CI (SQLite will not do
— pgvector, `ARRAY`, RLS).

## Remaining observability

A4 covers Sentry and structured logging. Left here: turning `log_cost` into a
real metric. Per-user AI spend is a business number, not a debug line.

## Rate limiting

There is none anywhere. `/chat/stream` proxies to paid LLMs with no per-user cap;
`question` is capped at 4000 characters
([meeting.py:26](../backend/app/models/meeting.py#L26)) but nothing stops a loop.
Per-user limits on chat and meeting creation, backed by A3's Redis.

## What deferring this costs you

Deferring is defensible — Phase A unblocks scaling and this does not. But the
consequence is explicit and already priced into the plan above: **A1 and A3 carry
their own tests as part of the phase.** Those are the two gates you cannot skip,
because A1 is a concurrency fix that is unobservable without one, and A3 can
silently leave a `completed` meeting with nothing to search.

That is why Sentry was pulled forward into A4 rather than left here.

---

# Phase C — The bot pool

This is the real scaling story and the one that caps the business. Everything
before it makes the *serving* tier scale; this makes the *recording* tier scale,
and recording capacity decides how many customers can be in meetings at 10am on a
Tuesday.

Budget for this as a redesign, not a refactor.

## Problem

`meeting-bot` is a stateful singleton in four independent ways:

1. **In-memory registry.** `activeMeetings` is a plain `Map`
   ([server.js:19](../meeting-bot/src/api/server.js#L19)). Nothing outside the
   process knows what it is recording.
2. **Per-process capacity.** `MAX_CONCURRENT_MEETINGS`
   ([server.js:18](../meeting-bot/src/api/server.js#L18)) defaults to 1. Each
   session is a headful browser plus ffmpeg at roughly 2GB — prod compose sizes
   `shm_size: 4gb` for two.
3. **Shared auth identity.** `auth.json` and `zoom-auth.json` are single
   host-mounted files (`docker-compose.prod.yml`). The comment at
   [server.js:9-17](../meeting-bot/src/api/server.js#L9-L17) is explicit that
   audio is isolated per session but Xvfb display and auth identity are still
   global.
4. **The backend has no notion of "which bot."** `MEETING_BOT_URL` is a single
   URL ([bot_service.py:29](../backend/app/services/bot_service.py#L29)).

And there is no backpressure: `trigger_bot_join` is a synchronous
`httpx.post(timeout=10)`, so a bot at capacity returns 409 and the meeting fails
outright. Two meetings scheduled at 10:00 with `MAX_CONCURRENT_MEETINGS=1` means
one is simply lost.

## Design

**C1. Make capacity externally visible.** Add `GET /capacity` to the bot
returning `{active, max, available}`. Cheap, immediately useful for debugging,
and the foundation for everything else.

**C2. Move dispatch behind a queue.** Reuse A3's broker. `trigger_bot_join`
enqueues a join request instead of posting synchronously; a dispatcher assigns
queued meetings to bots with free capacity. This turns "meeting lost" into
"meeting waits."

**C3. Bot registry.** Bots register themselves (host, capacity, heartbeat) in
Postgres or Redis on boot; the dispatcher picks a host with free capacity and
records the assignment. `stop_bot` looks up the assigned host rather than
assuming one URL. A missed heartbeat means the host is dead and its in-flight
meetings get swept — the existing watchdog TTLs already handle the meeting-side
cleanup.

**C4. Per-host auth identity.** The hard part, and the reason this is a redesign.
Each bot host needs its own Google/Zoom identity — a shared `auth.json` across
hosts means concurrent sessions fighting over one credential, and
`BrowserManager`'s `storageStateWriteQueues`
([BrowserManager.js:73-80](../meeting-bot/src/core/BrowserManager.js#L73-L80))
only serialises writes *within* a process. Options: a credential per host from a
pool, or moving auth state into shared storage with proper locking. **Decide this
before C3** — it constrains how hosts are provisioned.

**C5. Remove the single-session fallback.** `/stop` without a `meetingId`
resolves to "the only active meeting"
([server.js:119-132](../meeting-bot/src/api/server.js#L119-L132)). Once the
backend always knows the assignment, make `meetingId` required and delete the
fallback. The code already anticipates this.

## C1 and C2 are worth shipping on their own

They fix the worst user-visible failure — "meeting rejected because the bot was
busy" becomes "meeting waits" — **on your existing single host, with no
multi-host work.** C3–C5 are the actual horizontal step and should wait until
concurrent recordings are demonstrably the binding constraint.

## Gate

- A meeting requested while the bot is full is queued and joins when capacity
  frees, rather than failing.
- With two bot hosts, two simultaneous meetings land one on each.
- Killing a bot host mid-recording marks its meetings failed within the watchdog
  TTL and does not strand capacity in the registry.
- Two hosts recording simultaneously do not corrupt each other's auth state.

---

## Dependencies between phases

```
A1 (scheduler claim) ──── independent, do first
   └──> A2 is safe only because A1 landed

A2 (config)  ──────────── no code

A3 (queue + worker) ───┬── introduces Redis
                       ├──> Phase B rate limiting reuses it
                       └──> C2 dispatch queue reuses it

A4 (Sentry) ───────────── independent
   └──> pulled forward specifically to de-risk A5

A5 (local JWT) ────────── independent of A1–A3; wants A4 first

C  ────────────────────── needs A3's broker for C2
```

A3 is the hinge: it introduces the broker that Phase B and C2 both build on.

## New configuration this plan introduces

| Var | Phase | Notes |
|---|---|---|
| `WATCHDOG_ENABLED`, `CALENDAR_SCHEDULER_ENABLED` | A2 | Already exist — used as a deployment invariant. |
| `REDIS_URL` | A3 | Broker. Reused by Phase B and C2. |
| `TRANSCRIPTION_WORKER_CONCURRENCY` | A3 | Replaces the hardcoded `max_workers=4`. |
| `TRANSCRIPTION_USE_QUEUE` | A3 | The two-deploy cutover flag. |
| `SENTRY_DSN` | A4 | |
| `TEST_DATABASE_URL` | A1 | Already in use — `backend/tests/conftest.py` refuses to run without it. Test-only, never set in production. |
| `SUPABASE_JWKS_URL` / `SUPABASE_JWT_SECRET` | A5 | Which one depends on your project's signing scheme. |
| `SUPABASE_JWT_AUDIENCE`, `SUPABASE_JWT_ISSUER` | A5 | Must be verified, not just decoded. |
| `BOT_HOST_ID`, `BOT_REGISTRY_URL` | C | Per-host identity for the registry. |

## Open questions to settle before starting

1. **Which Supabase JWT signing scheme is this project on?** Determines A5's
   implementation entirely. Worth answering now even though A5 is last.
2. ~~`arq` or Celery for A3?~~ **Settled: `arq`.** See Phase A3's Design.
3. **Per-host bot auth (C4):** credential pool, or shared state with locking?
   Constrains provisioning, so decide before C3.
4. **Is session revocation latency acceptable in A5?** Local verification means a
   signed-out token stays valid until expiry.

<a id="appendix-a-step-number--phase"></a>
## Appendix A — step number → phase

The original draft numbered this work 1–7. Mapping, since those numbers still
come up in conversation:

| Original step | Phase | Moved because |
|---|---|---|
| 1 — scheduler claim | **A1** | unchanged |
| 2 — transcription queue | **A3** | not a scaling blocker; deferred behind the milestone |
| 3 — sweep loops | **A2** | config-only, so it pairs with A1 to reach the milestone fastest |
| 4 — local JWT | **A5** | highest risk, so it ships last and alone |
| 5 — tests | **B** | deferred, minus the A1/A3 gate tests |
| 6 — observability + rate limiting | **A4** (Sentry) + **B** (rest) | Sentry pulled forward to de-risk A5 |
| 7 — bot pool | **C** | unchanged |
