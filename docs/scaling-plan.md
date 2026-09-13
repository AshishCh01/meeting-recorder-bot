# Scaling & Production-Readiness Plan

Date: 2026-09-08 (restructured 2026-09-09 around execution phases)
Status: **A1** (`8dc0844`), **A3** (`d3b424d`), **A4** (`69e595a`) and **A5**
(`588ceb9`) shipped - Phase A is functionally complete except A2, which is
blocked on infrastructure, not on code. A5 was the highest-risk phase in this
document and was verified against a real token, real forged tokens, and a
real host-side benchmark. **Phase C is complete: C1 (`GET /capacity`), C2
(dispatch behind a queue), C3 (the bot registry), C4 (per-host auth identity)
and C5 (`meetingId` required on `/stop`) have all shipped. A meeting requested
while the recorder is busy waits instead of being lost; there can be more than
one recorder for it to wait on; each recorder signs in as its own account; a
recorder whose credential has died stops being given meetings it could only
shred; and no request can stop a recording it did not name.** Two things Phase
C closing does **not** close are listed under "What Phase C leaves open"
below — neither is a code gap, and neither should be discovered later by
someone assuming a finished phase means a finished problem.

**B1 (per-user rate limiting) has shipped**, pulled out of Phase B on its own
because it was the only item in that bucket blocking a deploy: until it landed,
`POST /{id}/chat` and `/chat/stream` proxied to a paid model with no per-user
cap at all, and a single script on a public box could run up an unbounded bill
against your API keys. Chat and meeting creation are now capped per user
against A3's Redis, with an atomic counter and a deliberate **fail-open** on a
Redis outage — see [B1](#b1--per-user-rate-limiting--done) for that decision
and what it costs. **B2 has shipped too**: both suites now run on every push
to `main` and every pull request, on the Python 3.12 that `backend/Dockerfile`
deploys on and that had never run this code before — it passes. A skip guard
makes a missing service container a red build rather than a green
`166 passed, 51 skipped`. B3–B5 (broader coverage, cost as a metric, structured
logging) remain deferred; none of them block a deploy.

Between A5 and C3, a batch of hardening landed that this plan treats as
prerequisites rather than phases of their own — found by testing the phases
above, not planned in advance: the connection-pool budget (`101a114`), chat
no longer holding a connection during the model's answer (`79551d0`), the
same fix applied to `search_transcript`/`_ensure_user_row`/`get_meeting`
(`0460fac`, `c5f2c84`, `d505d46`), both test suites made independent of the
developer's real `.env` (`a2a80f9`, `d424ea9`), and two dead npm dependencies
removed (`c9c3718`). Each is written up in place, next to the phase whose
testing found it.

**Decision (2026-09-12): proceed straight to C3, C4 and C5** rather than wait
for concurrent recordings to demonstrably bottleneck, which is what this plan
originally recommended (see "C1 and C2 are worth shipping on their own",
below) — the user wants every phase implemented before the first full-stack
deploy and end-to-end test pass, not before this specific limit is hit in
production. HTTPS, CI, and the low-traffic audit routes are deliberately
deferred until after C5, for the same reason. One thing A5's own benchmark surfaced and left open: a Supabase
connection-pool limit that isn't sized for concurrent load - see the end of
the A5 section.
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
| | *A2 is blocked on infrastructure — do it when you move off the single EC2 instance.* | | |
| ~~**A3**~~ | ~~Transcription moves to a queue + worker~~ — **done, `d3b424d`** | Medium | Env flag back to the executor |
| ~~**A4**~~ | ~~Sentry~~ — **done, `69e595a`** | None | Removing the DSN |
| ~~**A5**~~ | ~~JWTs verified locally~~ — **done, `588ceb9`** | High | Fallback wrapper, then revert |
| **B1** | Per-user rate limiting on chat and meeting creation — **done** | Low | `RATE_LIMIT_ENABLED=false` |
| **B2** | CI on every push and PR — **done** | None | Deleting the workflow |
| **B3–B5** | Broader tests, cost as a metric, structured logging | — | Deferred by decision |
| ~~**C**~~ | ~~Bot pool — the recording tier~~ — **done, C1–C5** | High | Per sub-step |

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
| **meeting-bot** | **Yes, as of C3 + C4.** | The in-memory registry and single `MEETING_BOT_URL` are gone (C3); each host now signs in as its own account and is taken out of rotation per platform when that account's session dies (C4). The remaining caveat is not a blocker: `MAX_CONCURRENT_MEETINGS > 1` still shares one identity *within* a host, which is what that setting has always meant. |

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
(see [Phase B](#phase-b--rate-limiting-tests-logging)). They are the
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

# Phase A3 — Transcription moves to a queue ✅ done

> **Shipped** in `d3b424d`, in the two deploys described below. **Revert:**
> `TRANSCRIPTION_USE_QUEUE=false` and restart — no code change, no rebuild.
>
> Landed as `app/worker.py` (arq), Redis and a `worker` service in both compose
> files, and 15 tests in `backend/tests/test_transcription_queue.py`. Three
> things worth knowing, none of which were in this plan's original text:
>
> - **`IndexingFailed` had to become a real exception type.** Letting an
>   indexing error propagate so arq can retry it means it must not be caught
>   by `transcribe_recording`'s broad `except Exception`, which would turn a
>   perfectly good transcript into a failed meeting. It is raised where the
>   indexing call used to be swallowed, and re-raised by a handler placed
>   ahead of both `except TRANSIENT_EXCEPTIONS` and `except Exception`. The
>   Sarvam fallback path needed the same treatment.
> - **The resume guard went in `transcribe_recording`, not the arq task.** One
>   level lower than planned, so both sides of the cutover behave identically:
>   deploy 1 on the executor gets the same guard as deploy 2 on the queue.
> - **The enqueue deliberately has no fixed `job_id`.** Deduplicating on
>   `meeting_id` would make arq silently drop the user-initiated re-run behind
>   `POST /meetings/{id}/retry` whenever an earlier result was still in Redis.
>   A duplicate is cheap: the resume guard returns immediately for a meeting
>   that is already done.

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

**Broker.** Redis. You need it for B1 rate limiting and Phase C dispatch
anyway, so it earns its keep three times. (Both have since shipped, and B1 is
the one that put it on the *request* path — see the note at the end of B1.)

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

### How the gate was met

All four, by tests that were **falsified** — each fix was reverted and the
matching test failed with the real symptom, because a test that has never been
shown to fail on broken code proves nothing:

| Fix reverted | Test failure |
|---|---|
| `db.commit()` put back between delete and insert | `the delete committed on its own - chunks were lost / assert 0 == 2` |
| Resume guard removed | `re-downloaded a recording for a meeting that already had a transcript` |
| `except IndexingFailed: raise` deleted | `DID NOT RAISE IndexingFailed`, with `Marking failed: Transcription failed: embedding provider down` — a good transcript thrown away over a RAG error |

One of those tests was rewritten during implementation because the first
version passed with the fix deleted: it reached the re-raise through the resume
path, which raises from outside the guarded block. The replacement drives the
full first-run pipeline with Gemini stubbed.

### Verified live, not just in tests

Deployed to a local stack in the two-deploy sequence:

- **Deploy 1** — Redis and `worker` up, `TRANSCRIPTION_USE_QUEUE=false`. Every
  `[transcription]` line stayed on `backend`, the worker logged `db_keys=0` and
  took no job. Infrastructure proven, behaviour unchanged.
- **Deploy 2** — flag on. `backend` logged `queued meeting ... as job <id>` and
  never logged `[transcription] Starting`; `worker` picked up the same job id
  and returned a real transcript.
- **Durability** — the API container was SIGKILLed (`exited with code 137`)
  *while* a transcription was running. The job completed 72.9s after it
  started, and the meeting reached `completed` with its chat history intact.
  Under the `ThreadPoolExecutor` that restart would have stranded the meeting
  in `transcribing` until the watchdog TTL failed it. **This is the single
  behaviour the phase exists to produce.**
- **Redis restart with a job queued** — the last design claim without
  evidence, now closed. A job was enqueued through the real `_enqueue()`
  path with `worker` stopped, then Redis was restarted:

  | Stage | Redis state |
  |---|---|
  | after enqueue | `DBSIZE=2` — `arq:job:fdddd4d0…`, `arq:queue` |
  | after `docker compose restart redis` | `DBSIZE=2` — identical keys |

  The startup log is the proof it came from the AOF and not a snapshot:
  `Done loading RDB, keys loaded: 0` followed by `DB loaded from incr file
  appendonly.aof.1.incr.aof`. arq then read it back as a complete job with
  its original `enqueue_time`, and on starting `worker` executed it:
  `52.00s → …:transcribe_job(…) delayed=52.00s` then `1.68s ← … ●`. The
  `delayed=52.00s` is the whole test in one line - the job waited across
  the restart and still ran. `--appendonly yes` plus the `redis-data`
  volume are doing real work.

### Still unproven in a running system

One item, and it is the difference between tested and observed rather than a
gap in the work:

- **No job has ever failed.** arq reports `j_failed=0 j_retried=0`, so the
  retry ladder, `record_terminal_failure`'s branch on transcript, and the
  `IndexingFailed` re-raise have run only against stubs. They are covered by
  the falsified tests above; they have not run against a real provider
  outage. Deferred by decision - A4 configures the worker's logging, which
  is what would make a real retry observable when one happens.

### Fixed while testing this phase

Neither is part of A3, both were found by running it end to end:

- **`meeting-bot` had no DNS fallback** (`b7ab3c8`). A recording was lost to
  `[SupabaseUploader] Upload failed: fetch failed` - Docker Desktop's embedded
  resolver intermittently failing on Windows/WSL2, the same fault `backend`
  already carried a `dns: 8.8.8.8 / 1.1.1.1` block for. Node's `fetch` does
  not retry a failed lookup, so one unlucky resolve failed the upload
  outright. `meeting-bot` now has the same block; verified afterwards with
  5/5 clean HTTPS round-trips to Supabase from inside the container.
- **`PYTHONUNBUFFERED` was committed but never built** (`1c00428`). The fix
  was in git while the running images predated it, because `docker compose
  start` reuses the existing container - only `up --build` applies a
  Dockerfile change. Worker `print()` output was still being buffered away
  until `backend` and `worker` were rebuilt. Worth remembering before the
  EC2 deploy: **`start` reuses, `up --build` applies.**

## Effort

Largest of Phase A, as expected. The risk was in retry safety, not plumbing.

---

# Phase A4 — Sentry ✅ done

> **Revert:** unset `SENTRY_DSN` and restart — no code change. The logging fix
> below has no DSN to remove and should stay either way.
>
> Landed as `backend/app/observability.py` (`configure_logging`, `init_sentry`,
> `scrub_event`, `set_meeting_context`), called from both entrypoints, plus
> `sentry-sdk` in `requirements.txt` and 8 tests in
> `backend/tests/test_observability.py`. Two things worth knowing, neither of
> which was in this plan's original text:
>
> - **Sentry's own credential filtering was not enough, and that was only
>   visible by looking at a real payload.** Every request to this API carries a
>   Supabase JWT. `send_default_pii=False` does filter it out of
>   `request.headers`, and the SDK's `EventScrubber` does check frame locals
>   whose *name* looks sensitive — but a request that raised inside a route
>   still arrived with the caller's token in roughly twenty stack frames, under
>   names no denylist flags: `scope.headers`, `conn.headers`,
>   `request.headers`, the bound `functools.partial` in `func`, and FastAPI's
>   `solved_result`. Any frame in `app/services/*` would additionally have
>   carried a `settings` local, whose repr is every API key this app has. The
>   answer is `include_local_variables=False` — fail closed, at the cost of
>   variable values in tracebacks — plus a shape-based JWT scrub in
>   `before_send`, because a token can also arrive inside an exception
>   *message* (`api/auth.py` raises
>   `HTTPException(detail=f"Could not validate credentials: {e}")`).
> - **The worker's logging fix works because of arq's startup ordering.** The
>   `arq` CLI imports the settings module *before* applying its own log config,
>   and that config names only the `arq` logger — it has no `root` key and
>   `disable_existing_loggers: False` — so configuring root at import time in
>   `app/worker.py` survives it. `logging.getLogger("arq").propagate = False`
>   goes with it, or every arq line prints twice. `WorkerSettings.on_startup`
>   re-asserts both after arq is fully up, so the fix does not depend on that
>   ordering staying true.

Pulled forward out of its original grouping for one reason: **A5 is a change you
cannot safely validate without seeing its error rate in production.**

What shipped:

- Sentry for unhandled exceptions in both the API and the A3 worker. Two
  processes, two `init_sentry()` calls; the worker gets `ArqIntegration`, which
  opens a per-job isolation scope and captures what escapes the job, so nothing
  wraps `transcribe_job` by hand.
- Python logging configured at both entrypoints, so `logger.*` emits at all.
- `meeting_id` and `user_id` as Sentry tags on transcription events —
  `arq-job.args` is redacted under `send_default_pii=False`, so without them a
  failed transcription arrives with no indication of which meeting it was.

`SENTRY_DSN` is optional and empty by default, like `GROQ_API_KEY` and
`JINA_API_KEY`: unset means Sentry is never initialised and both processes start
exactly as before. Local dev and the test suite need no DSN.

**What was verified, precisely.** The credential-scrubbing and tagging claims
were checked against the payload **as it would have been sent** — captured at
the transport, after `before_send`, before the wire (`CapturingTransport` in
`backend/tests/test_observability.py`). No event was transmitted to a real
Sentry project, so nothing here evidences *delivery*: that a real DSN is
accepted and the event lands in the UI is a separate check, and it is
deliberately the pre-A5 step in
[aws-ec2-deploy.md §8b](aws-ec2-deploy.md). The distinction matters in this
direction too — a Sentry project's UI would only show what survived their
server-side processing, which is the weaker claim for "nothing leaked".

The disabled path was checked in the running stack, not argued: with
`SENTRY_DSN` unset, `docker compose up -d --build backend worker` starts both
services clean and all 40 tests pass (the 31 from A1/A3, unchanged, plus 9 new).

**A3 surfaced one concrete gap, and this fixed it.** `app/worker.py` used
`logging.getLogger(__name__)`, but arq configures only its own `arq.*` logger,
so the root logger stayed at WARNING and every `logger.info(...)` in the worker
was dropped. That is why the running stack showed arq's own job lines but never
`[worker] transcribing meeting ... (attempt 1/3)`. The consequence that mattered:
**a job that succeeded on attempt 3 looked identical to one that succeeded first
time**, because the retry `logger.warning` and the "gave up after N attempts"
`logger.error` were the only evidence either way.

(The related symptom in local dev — `print()` output buffered away because
`Dockerfile.dev` lacked `PYTHONUNBUFFERED` — was fixed in `1c00428`.)

Deliberately *not* done here, both moved to Phase B: turning `log_cost` into a
real metric (B4), and converting the `print()` calls in `backend/app` to
`logger.*` (B5). (This said "45" until B2 re-counted it: 44.)
The `print()` calls already emit and are already visible in `docker compose
logs` — a bulk rewrite touching every service file is a large diff that de-risks
nothing about A5, which is the only reason this phase exists. `configure_logging`
writes to stdout rather than logging's default stderr specifically so the
converted and unconverted lines interleave in order until that happens.

---

# Phase A5 — Verify JWTs locally ✅ done

> **Shipped** in `588ceb9`. **Revert, in order of cost:**
> `JWT_LOCAL_VERIFICATION_ENABLED=false` and
> restart — no rebuild, no code change, straight back to
> `supabase.auth.get_user()` on every request. The fallback wrapper below sits
> under that as an automatic net. Reverting the commit is the third resort,
> not the first.
>
> Landed as `backend/app/services/jwt_verifier.py`, a rewritten
> `backend/app/api/auth.py`, `PyJWT` in `requirements.txt`, and 26 tests in
> `backend/tests/test_jwt_auth.py`. Four things worth knowing, none of which
> were in this plan's original text:
>
> - **`PyJWKClient` refetches the JWKS on *every* unknown `kid`, with no
>   cooldown.** So the naive implementation of "refresh on unknown `kid`" is
>   one outbound request to Supabase Auth *per request* — a key rotation, or
>   anyone replaying junk tokens in a loop, becomes a self-inflicted DDoS on
>   the dependency this phase exists to stop leaning on. `_SigningKeys` does
>   the `kid` lookup itself and puts a locked cooldown in front of the
>   refetch: 50 unknown-`kid` requests cost one refetch, and a real rotation
>   is still picked up by the first request that sees the new key.
> - **The published JWKS is a public key set - an algorithm-agnostic verifier
>   turns it into a forgeable shared secret.** Not something this plan asked
>   for; found during implementation. If `alg` were accepted from the token
>   rather than pinned to `["ES256"]`, anyone could HMAC-sign a token with
>   Supabase's own public key as the secret and mint a session for any `sub`.
>   Verified independently, not just by the implementer's own tests: hand-built
>   (not PyJWT-encoded, since PyJWT refuses to produce either shape) `alg:none`
>   and HS256-signed-with-the-real-public-key tokens were both rejected with
>   `bad_algorithm` against the running server.
> - **`include_local_variables=False` closes a leak `send_default_pii=False`
>   does not.** Verified, not assumed: a captured payload with locals on put
>   the caller's JWT into ~20 stack frames under ordinary-looking names -
>   `scope.headers`, `conn.headers`, `request.headers`, FastAPI's
>   `solved_result` - none of which a name-based scrubber would flag. Any
>   frame in `app/services/*` also holds a `settings` local whose `repr`
>   contains the Supabase **service-role key** and every other secret this app
>   has, independently confirmed by checking `repr(settings)` directly. Cost:
>   captured tracebacks lose variable values; file/line/function/source
>   context survive, which is what an error rate needs.
> - **The user-row cache was worth more than the JWT change itself.** Measured
>   below. Both the Supabase Auth call and the `users` SELECT are round-trips
>   to remote services, and the SELECT was the slower of the two.
> - **`alg` is checked against an allow-list before the JWKS is touched.** That
>   is what makes `alg: none` a rejection, and it also means a junk `alg` can
>   never cost a network fetch. The allow-list must never gain an HMAC
>   algorithm: the JWKS is public, so HS256 would make the published key a
>   valid shared secret. Both are tested with hand-built raw tokens, because
>   PyJWT refuses to *encode* either and testing through its encoder would
>   prove nothing about what an attacker actually sends.
> - **The fallback fires on expired tokens too, which costs a round-trip to
>   confirm a rejection.** Deliberate for this release — see the fallback
>   section — but it is the first thing to narrow when the wrapper comes out.

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

**Signing scheme — resolved, no longer an open question.** This project is
**asymmetric, ES256**. `${SUPABASE_URL}/auth/v1/.well-known/jwks.json` returns
200 with exactly one key:

```
kty=EC  alg=ES256  use=sig  kid=59cc524a-85fb-4cd2-bae7-7004d50bf9aa
```

So there is no JWT secret to store, rotate or leak — the backend holds only
public keys, fetched at runtime. `PyJWT`'s `PyJWKClient` does the fetching and
caching; `cryptography` was already a dependency and is what makes ES256 work.

**`aud` and `iss` were read off a real access token, not assumed.** This is the
one detail a unit test cannot validate — a fixture encodes whatever value the
test author guessed, so a wrong guess passes the suite and fails every user
simultaneously in production. Decoded without verification, a live token
carries:

| claim | value |
|---|---|
| `alg` / `kid` | `ES256` / `59cc524a-85fb-4cd2-bae7-7004d50bf9aa` (matches the JWKS) |
| `aud` | `authenticated` |
| `iss` | `https://<project-ref>.supabase.co/auth/v1` |
| `sub` | the user id |
| `email` | present as a top-level claim |

Both happen to match the documented convention, but they are now configuration
(`JWT_AUDIENCE`, `JWT_ISSUER`, blank meaning "derive from `SUPABASE_URL`")
rather than hardcoded, precisely because being wrong about either is an outage
that has to be fixable by an env var and a restart.

Verify signature, `exp`, `aud` and `iss`. Extract `sub` as the user id and
`email` from the claims.

**Legacy HS256 tokens still in flight** — if a project was migrated to
asymmetric keys, sessions issued before the migration carry HS256 tokens that
local verification will reject. They are not special-cased, and the JWT secret
is deliberately *not* stored in order to validate them. They take the fallback
path instead: `bad_algorithm`, one Supabase round-trip, user stays logged in,
and they drain as sessions refresh. Decided, rather than discovered.

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

- `backend/requirements.txt` — `PyJWT==2.13.0`.
- `backend/app/api/auth.py` — `get_current_user`, the fallback wrapper and its
  counters, and the user-row TTL cache.
- `backend/app/config.py` — JWKS URL, audience, issuer, algorithm allow-list,
  the kill switch, the fallback switch and both cache TTLs. No JWT secret —
  the scheme is asymmetric.
- `backend/app/observability.py` — `capture_message`, so the fallback is
  visible in Sentry and not only on stdout.
- New: `backend/app/services/jwt_verifier.py` — JWKS fetch/cache and verify.
- New: `backend/tests/test_jwt_auth.py` — 26 tests.

## Gate — results

- **Valid token → correct `sub`.** Verified with a real access token against
  the live JWKS, not only a self-signed fixture.
- **Every rejection path returns 401:** expired, wrong signature, wrong `aud`,
  wrong `iss`, `alg: none`, HS256-signed-with-the-public-key, missing `sub`,
  and unparseable garbage. Each asserted twice — at `verify_token`, for the
  specific reason, and end-to-end through the FastAPI dependency for the
  status code.
- **Unknown `kid` triggers exactly one JWKS refetch.** 50 consecutive
  unknown-`kid` requests cost 1 refetch. With the cooldown set to 0 the same
  test goes back to one fetch per miss, which is what demonstrates the
  cooldown is doing the work rather than some incidental caching.
- **p50 on `GET /users/me`**, 100 samples after 15 warm-up requests, same
  image and same network path, configuration switched by env var:

  | configuration | p50 | p95 |
  |---|---|---|
  | pre-A5 (Supabase Auth call + `users` SELECT per request) | 128.42 ms | 173.22 ms |
  | local JWT, no user-row cache | 106.88 ms | 282.59 ms |
  | **local JWT + user-row cache (shipped)** | **59.90 ms** | **83.56 ms** |

  **53% off p50.** Note the middle row: removing the Supabase Auth round-trip
  on its own bought 22 ms of the 69 ms. The `users` SELECT was carrying the
  larger share — the concrete version of the warning above. Shipping the JWT
  change without the user-row cache would have captured roughly a third of the
  available win and looked like the phase underdelivered. (The remaining 60 ms
  is real work: `/users/me` runs its own `SELECT` in the route body, against
  the same remote Postgres.)
- **Fallback counter: 0** across the 116 authenticated requests of the
  benchmark run — no `[auth] local verification failed` line, so every one was
  verified locally. That is also **Phase A exit criterion 4** ("an
  authenticated request makes zero outbound Supabase Auth calls in the steady
  state") satisfied.
- **Still open, in production:** watch that counter under real traffic before
  deleting the fallback. Zero here is one user and one token, which is not the
  same claim.

## The fallback wrapper, and how it comes out

`AUTH_FALLBACK_ENABLED=true` wraps local verification: on **any** verification
failure, ask `supabase.auth.get_user` before rejecting. That is what makes a
wrong `aud` a log line rather than an outage, and it is why this phase can ship
on a quiet window instead of needing a maintenance one.

Because it works, it is silent — a fallback firing on 100% of requests and one
that never fires look identical from the outside. So every fire is counted by
reason, logged with the running total, and reported to Sentry (A4, `69e595a`),
rate-limited to one event per reason per minute with the totals carried inside
the event so the volume stays legible without sending 100% of requests to
Sentry.

Deleting it is a separate, later change, gated on that counter sitting at zero
under real traffic. When it happens, narrow `expired` first: an expired token
is unambiguous — local verification cannot be wrong about it the way it can be
wrong about `aud` — so falling back on it spends an HTTPS round-trip to confirm
a rejection, and it is reachable by anyone replaying an old token.

## Verified independently, and from the host

Two passes beyond the implementer's own report, because a phase this
security-sensitive is only worth as much as evidence someone else can
reproduce:

**Forgery, run against the live server, not re-derived from the code:**

```
alg=none                                    -> rejected (bad_algorithm)
alg=None (case)                             -> rejected (bad_algorithm)
HS256 forged with the real public key (PEM) -> rejected (bad_algorithm)
HS256 forged with the real public key (text)-> rejected (bad_algorithm)
garbage / empty token                       -> rejected (malformed)
```

`frames carrying vars: 0` when the same exception was captured with a real
Supabase key and Gemini key sitting in scope - neither reached the payload.

**Host-side benchmark** (`backend/benchmark_auth.py`, crossing Docker
Desktop's port proxy the way a browser does - a different vantage point
from the implementer's in-container numbers, and the more honest one for
"what a user experiences"), isolated run, `/meetings`:

| | before | after | change |
|---|---|---|---|
| min | 301.91 ms | 68.03 ms | -77% |
| p50 | 368.69 ms | 151.01 ms | -59% |
| p99 | 1127.44 ms | 400.51 ms | -64% |
| max | 8086.32 ms | 513.24 ms | -94% |

The tail matters more than the median here: an 8-second authenticated
request was a visible hang, and it is gone - not because it got faster, but
because the Supabase round-trip that occasionally stalled that badly no
longer happens on the hot path at all.

## Found by this phase's own benchmark ✅ fixed

> **Fixed** as a standalone change — see [the fix](#the-fix-a-15-connection-budget-and-503-not-500)
> below. The write-up that follows is the bug as it was found.

A later run with `-c 8` (concurrent) surfaced a real, pre-existing bug this
phase did not cause and does not fix:

```
psycopg.OperationalError: ... FATAL: (EMAXCONNSESSION) max clients reached
in session mode - max clients are limited to pool_size: 15
```

60 occurrences in one run; 5-6 requests came back as 500s, and a `/meetings`
serial pass right after the concurrent one had a 6-second p99 - almost
certainly the same exhaustion showing up as a pool-checkout wait instead of
an outright rejection.

**Cause:** `backend/app/db/database.py` sets `pool_size=10, max_overflow=20`
- 30 connections per process. Two processes import it and each get their own
engine: `backend` and A3's `worker`. Worst case, 60 connections requested
against Supabase's session-mode pooler, which is capped at 15 - a limit set
by the Supabase plan/pooler config, not by this app, and not something A5's
fix touches.

**This predates A5.** It surfaced now because A5's benchmark is the first
thing that has ever driven real concurrent load at this backend - the same
way A3's atomicity bug predated A3 but only became reachable once a queue
introduced retries.

Deferred, by decision, to its own change - not bundled into A5 and not
blocking the move to Phase C. When it is picked up: size `pool_size` +
`max_overflow` for both processes combined with headroom under 15, consider
whether Supabase's transaction-mode pooler (port 6543) removes the ceiling
rather than just fitting under it, and reproduce this exact benchmark
command first so the fix is proven against the failure that found it.

### The fix: a 15-connection budget, and 503 not 500

**The budget.** Pool sizes are now settings (`db_pool_size`, `db_max_overflow`,
`db_pool_timeout_seconds`), and the arithmetic sits beside them in
`config.py`:

| | pool | overflow | why |
|---|---|---|---|
| `backend` | 8 | 0 | request handlers, plus the watchdog and scheduler sweeps |
| `worker` | 4 | 0 | `worker_max_jobs = 4`, one session per job |
| headroom | 3 | | `alembic upgrade head` at boot, SQL editor, ad-hoc `psql` |
| **total** | **15** | | was 30 + 30 = 60 worst case |

Both processes import the same `database.py`, and `env_file` is shared, so the
worker's 4 comes from an `environment:` override on the `worker` service in
both compose files. Overflow is 0 because an overflow connection still takes a
pooler slot. The arithmetic breaks the moment a process is added:
`--scale worker=2` is 16, and a second backend replica (A2) has to split the
backend's 8.

**The trap it had to avoid.** Once the pool fits under 15, excess demand stops
being *rejected* by the pooler and starts *queueing* in SQLAlchemy — for its
default `pool_timeout` of 30s, which nothing set. So the backend's timeout is
now 3s, and `main.py` answers **503** with `Retry-After: 2` for both shapes of
saturation: `sqlalchemy.exc.TimeoutError` (the pool had no free connection)
and an `OperationalError` carrying `EMAXCONNSESSION` (the pooler refused).
Any other `OperationalError` is re-raised and stays a 500. The handler is
app-level because `get_db()` never touches the database. A side effect worth
having: the 503 carries CORS headers, which the old unhandled 500 did not, so a
browser sees a retryable error instead of a CORS failure. The worker's timeout
is 30s, not 3s. No user is waiting on a job, and a job that gives up on a pool
wait burns an arq retry.

**Results**, `benchmark_auth.py -c 8`, host-side, same token and stack:

| | before (10 + 20 per process) | after (8 + 0 / 4 + 0) |
|---|---|---|
| `EMAXCONNSESSION` in logs | 1 rejection | 0 |
| 500s | 1 (`/meetings`, concurrent) | 0 of 840 |
| `/users/me` x8, p50 / p95 / p99 | 198 / 537 / 924 ms | 301 / 413 / 858 ms |
| `/meetings` x8, p50 / p95 / p99 | 318 / 529 / 1034 ms | 267 / 305 / 317 ms |

One run is noise at this network distance: two identical runs on the same
config differed by 190 ms at `/users/me` p50. So the latency question was
settled A/B instead, alternating backend pool config on the same code and the
same client, 200 requests x8 per endpoint, two runs each. The new pool's
p99s were 431–811 ms; the old 10 + 20 pool's were 829–1723 ms. A pool of 8 is
not queueing requests that 30 would have served.

**Forced exhaustion**, backend at `DB_POOL_SIZE=1`, 32 concurrent clients, 200
requests to `/meetings`: 56 × 200, **144 × 503, 0 × 500**, 0 tracebacks. The
503s came back at p50 3.03s, max 4.1s — the configured timeout, not SQLAlchemy's
30s. Each logged one line, `[db] connection pool checkout timed out - returning
503: QueuePool limit of size 1 overflow 0 reached`.

**Verified inside the running containers**, not read off compose: the worker's
`arq` process (PID 1) carries `DB_POOL_SIZE=4 DB_MAX_OVERFLOW=0
DB_POOL_TIMEOUT_SECONDS=30` and builds `QueuePool size=4 max_overflow=0
timeout=30.0`; the backend carries no `DB_*` vars and builds `size=8
max_overflow=0 timeout=3.0`.

Tests: `backend/tests/test_db_pool_errors.py` (6). One exhausts a real
1-connection pool against Postgres for the timeout shape. With the handlers
disabled, the three 503 tests fail `500 == 503`.

**Still open:**

- **One unexplained 30-second stall.** In the first post-fix benchmark run, one
  `/meetings` request passed the client's 30s read timeout. The backend logged
  no 503, no error and no access line for it. It did not recur in the five
  runs after it, about 2,400 requests, two of them on the old pool config. Pool checkout cannot be the cause, since it
  gives up at 3s. The benchmark opens a new TCP connection per request through
  Docker Desktop's port proxy, which is the main suspect but not proven.
- ~~**The chat routes hold a connection for a whole LLM turn.**~~ **Fixed** —
  see below.

### Follow-up: chat no longer holds a connection during the answer ✅

Both chat routes ran `_assert_chattable` through the request's session and then
spent the whole model call — for `/chat/stream`, the route the frontend uses,
the whole stream, because FastAPI 0.141 runs `get_db`'s cleanup after the last
chunk — with that transaction open and its connection checked out. Eight
concurrent chats would take all 8. The routes now `db.close()` the request's
session after the checks and before any model work: in the route body, so a
bad or not-ready meeting still fails as a plain 404/409 before a stream starts.

It has to be the *request's* session. `get_current_user` shares it, and on a
user-row cache miss `_ensure_user_row` runs a `SELECT` and returns without
committing. A separate short session for the checks alone was tried as a
falsification. It passed the cache-hit tests and **failed both cache-miss
tests**, with auth's connection still held.

**Measured** with the model call replaced by a fake that reads
`engine.pool.checkedout()` mid-answer (`tests/test_chat_connection_release.py`,
8 tests): before, **1** on both routes on a cache hit and a cache miss; after,
**0** on all four. Removing the release fails all four again.

**Live**, backend at `DB_POOL_SIZE=2`, 3 concurrent streamed chats on a
completed meeting, `GET /meetings` ×3 while they streamed:

| | before | after |
|---|---|---|
| `GET /meetings` | 3 × 503, each at ~3.0s | 3 × 200, 390–472 ms |
| the 3 chats | 1 × 503; 2 completed, 57s and 62s | 3 completed, 11–29s |
| pool timeouts inside chat | thread load failed, a tool call timed out, one exchange not saved | none |

The test Q&A rows were deleted afterwards.

**Residual, observed rather than fixed.** A chat turn still takes short-lived
connections of its own: `_load_thread`, one per tool call (run in parallel with
`asyncio.gather`), and `_save_exchange`. At pool 2 with 3 chats, the run after
the fix logged no contention. The *before* run showed what saturation looks
like there: the history load and the save each give up after the 3s pool
timeout and log `[chat] could not load/store thread`. The answer still streams,
without its history or without being stored. At the real pool of 8 this
needs many chats in their tool phase at the same instant; watch for those log
lines rather than redesign for it now.

**Audit — same pattern elsewhere, not fixed here.** Routes that check out a
connection with a query and then make a slow network call before the
transaction ends (from reading the code, not measured):

- ~~`app/rag/tools.py:142-151` — `search_transcript` queries the meeting, then
  calls `embed_query` (Gemini embeddings) before its vector search; inside every
  parallel tool call.~~ **Fixed** — see below.
- `app/api/calendar.py:108-121` `list_events` and `:148-156` `schedule_event` —
  `calendar_service.get_valid_access_token` queries `CalendarConnection`
  (`calendar_service.py:31`) and then refreshes the token with Google (`:35`);
  the route then calls Google again (`list_upcoming_events` / `get_event`).
- ~~`app/api/meetings.py:99-107` `get_meeting` — query, then a Supabase Storage
  signed-URL request.~~ **Fixed** — see below.
- `app/api/meetings.py:131-140` `retry_meeting` — commits, but reading
  `meeting.user_id` at `:136` after the commit re-loads it (`expire_on_commit`
  is the default), so the storage listing at `:140` runs with a connection out.
  Same at `:182-202` in `_reupload_from_bot`, around the bot call (10s timeout).
- `app/api/meetings.py:227-252` `stop_meeting` and `:272-297` `delete_meeting` —
  query, then `stop_bot` (10s timeout) and, for delete, a Storage removal before
  the commit.
- `app/api/meetings.py:69-70` `create_meeting` — the `User` query after the
  commit holds through `trigger_bot_join` (a 10s bot call with the queue off, a
  Redis enqueue with it on).
- `app/api/meetings.py:308-316` `export_meeting_pdf` — holds through PDF
  generation, but that is local CPU work, not a network call.

And in general: any route using `get_current_user` holds a connection from auth
onward on a user-row cache miss, whether or not the route itself queries first.

### Follow-up: three more places release before the network call

One commit each, in priority order. Every test stubs the slow call and reads
`engine.pool.checkedout()` from inside it.

- **`search_transcript`** (`rag/tools.py`). `embed_query` retries Gemini four
  times with backoff sleeps and then falls back to Jina, which is about 10s on a
  429, and it runs inside chat's parallel tool calls. Now three steps: a short
  session reads the ownership check and `embedding_provider` into plain values,
  the embed runs with no session open, and a second short session does the
  vector search. The retry logic is untouched. Checked out during the embed:
  **1 → 0**. Real pgvector rows with fixed vectors return the same chunks in the
  same order before and after, another meeting's identical vector stays
  excluded, and the meeting's own provider is still passed through
  (`tests/test_search_transcript_session.py`, 7 tests). Reverting the file fails
  the connection test again.
- **`_ensure_user_row`** (`api/auth.py`), on every route that uses
  `get_current_user` and misses the user-row cache, which after a restart means
  every user's first request at once. It shares the route's session and
  returned with a transaction open on two paths: the existing-user `SELECT`,
  and the insert race, where a concurrent insert makes `commit()` raise
  `IntegrityError`, followed by a rollback and a second `SELECT`. Both now roll
  back, and so does the email-collision path before it re-raises. It uses
  rollback rather than `close()`, because the route keeps using the session.
  Each path is tested for no open transaction and nothing checked out; the race
  uses a real `IntegrityError` from a second connection. A route that calls a
  stub straight after a cache-miss auth read **1 → 0**
  (`tests/test_auth_user_row_session.py`, 5 tests; reverting fails 4). Chat
  keeps its own `_release_request_session`: `_assert_chattable` opens a new
  transaction after auth.
- **`get_meeting`** (`api/meetings.py`). The session is closed after
  `meeting_to_dict`, before the Storage signed-URL request, and only the plain
  dict is read afterwards. Checked out while signing: **1 → 0**, on both a
  cache hit and a cache miss (`tests/test_get_meeting_session.py`, 4 tests;
  reverting fails both).

**How fixes 2 and 3 interact:** not the way the task predicted. With only fix
3, a cache-miss `get_meeting` already reads 0 (the cache miss was asserted to
have happened). `close()` ends the shared session's transaction, including the
one auth left open. Fix 2 matters for routes that never release their session;
the after-auth route test reads 1 without it.

| | `get_meeting` miss | `get_meeting` hit | route that doesn't release |
|---|---|---|---|
| only fix 3 | 0 | 0 | **1** |
| only fix 2 | **1** | **1** | 0 |
| both | 0 | 0 | 0 |

**Live, inconclusive.** The post-restart burst of concurrent `GET
/meetings/{id}` was run at `DB_POOL_SIZE=2`, from a fresh restart and again with
the pool's two connections pre-opened, alternating code versions. It did not
cleanly separate before from after. At 16 concurrent, both versions were all
200s whenever the pool was warm. At 32, before gave 15 and 1 × 503, after gave
1 and 3 × 503. From a cold restart the counts swung the other way. The first
connections to the pooler took 0.7–2.5s to open, and that dominates a
burst against a 3s pool timeout. At pool 2, each request's two remote
`SELECT`s cost about as much as the signing call itself. So the fix roughly
halves how long a connection is held, but cannot remove contention at 32 on 2.
The deterministic tests above are the evidence; the live run is not.

**Live search.** One real question ("quote what was said about deployment,
with the timestamp") called `search_transcript` twice. Both Gemini embedding
requests returned 200, with no Jina fallback and no pool timeouts, and the
answer quoted the transcript at 00:17. The test rows were deleted.

## Behaviour change accepted: revocation is no longer immediate

Local verification cannot see server-side session revocation. After a sign-out,
or an admin revoking a session, **that access token keeps working until its
`exp`** — up to one hour on Supabase's default lifetime; the token used for the
measurements above had a 60-minute window.

What still holds: the *refresh* token is revoked immediately, so the session
cannot extend itself past that one access token's expiry. The exposure is
bounded by the access-token lifetime rather than open-ended.

Accepted knowingly, not overlooked. If it ever stops being acceptable — a
compliance requirement, or a "sign out everywhere" feature that has to be
instant — the options are shortening the access-token lifetime in Supabase, or
`JWT_LOCAL_VERIFICATION_ENABLED=false` to go back to asking Supabase on every
request and pay the latency again.

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
<a id="phase-b--rate-limiting-tests-logging"></a>
# Phase B — Rate limiting, tests, logging

> **B1 (rate limiting) has shipped.** The rest of this phase is still deferred
> by decision — it does not block a deploy, and B1 did.

Phase B was written as one undifferentiated bucket and deferred wholesale. That
was defensible while every item in it was a *quality* item. It stopped being
defensible once the bucket was examined: one of the four things in it was the
only thing in this entire document standing between a public EC2 box and an
unbounded bill against a paid API key. So the phase is now numbered, and the
one item that blocked the deploy was done on its own.

| Step | What | Status |
|---|---|---|
| **B1** | Per-user rate limiting on chat and meeting creation | **done** — below |
| **B2** | CI: run both suites automatically | **done** — below |
| **B3** | Broaden test coverage — the status machine, webhook idempotency, the AI fallback ladders | Deferred |
| **B4** | `log_cost` → a real metric rather than a debug line | Deferred |
| **B5** | The remaining `print()` calls → `logger.*` with structured fields | Deferred |

---

<a id="b1--per-user-rate-limiting--done"></a>
## B1 — Per-user rate limiting ✅ done

> **Risk:** low. **Revert:** `RATE_LIMIT_ENABLED=false`, no rebuild.
>
> Landed as `backend/app/services/rate_limit.py`, four settings groups in
> `config.py`, a dependency on three routes, and 35 tests in
> `tests/test_rate_limit.py` (suite: **182 → 217**).

### The problem it closes

There was no rate limiting anywhere. `POST /meetings/{id}/chat` and
`/chat/stream` proxy straight to Gemini (with a Groq fallback) with no per-user
cap. `question` is capped at 4000 characters
([meeting.py:26](../backend/app/models/meeting.py#L26)) and nothing at all
stopped a loop. On a public box, one script runs up an unbounded bill against
your API keys — and unlike every other item in Phase B, that is not a quality
problem you can carry into production and fix later.

### What is limited, and what deliberately is not

| Route | Limited | Scope |
|---|---|---|
| `POST /meetings/{id}/chat` | yes | `chat` |
| `POST /meetings/{id}/chat/stream` | yes | `chat` — **the same counter** |
| `POST /meetings` | yes | `meeting-create` |
| `POST /webhooks/*` | **no, deliberately** | — |

**The two chat routes share one budget, and that is the point.** They are the
same operation with different transports. Two counters would mean a caller
alternating between them gets exactly double the allowance, which is the first
thing anyone trying would find. One scope, one key.

**Webhooks are not limited and must not be** — there is a comment at the top of
`webhooks.py` saying so, because this is exactly the kind of omission a later
reader would "fix". The caller is meeting-bot, holding the shared bearer token,
and its rate is a function of how many recordings are running — the thing
scaling up is supposed to increase. Throttling it saves nothing (no paid model
is on that request path) and breaks recordings already in progress: a dropped
`completed` report means a finished recording whose file is never fetched, and
the user loses a meeting that was successfully recorded. There is also nowhere
to key it — the only identity is one token shared by the whole pool.

**`retry` / `reupload` were left out, and probably should stay out.** `retry`
is gated to `failed` meetings and flips status before doing anything, so it is
self-limiting in a way chat is not; `reupload` talks to a bot, not a model.
Both are worth a second look if the audit routes ever land, but neither is a
cost surface and neither justified widening this change.

### Design

**Redis, not a library.** `slowapi` is the obvious reach and is the wrong shape
twice: it keys on IP — which for this app is a shared NAT or a corporate egress
as often as it is a person — and it installs as middleware, which runs before
`get_current_user` has resolved the only key that means anything. A3 already
put Redis in `requirements.txt` and C3 already keeps a cache in it. No new
dependency was added.

**A dependency, not middleware.** The key is the `user_id` from
`get_current_user`, so this has to run *after* auth, and the limits differ per
route. As a `Depends(...)` that itself depends on `get_current_user`, ordering
is guaranteed by construction rather than by convention — the limiter
*replaces* `Depends(get_current_user)` in the route signature rather than
sitting beside it, and FastAPI's dependency cache means auth still runs exactly
once.

**Async client — the opposite of `bot_registry`, on purpose.**
`bot_registry` uses a synchronous client and documents why: everything calling
it runs in a thread. That reasoning does not transfer here.
`chat_with_meeting_stream` is `async def` and runs on the event loop, so a
blocking `redis-py` call inside it would stall *every* request in flight — a
500ms Redis hiccup becoming 500ms added to every concurrent request in the
process, not just its own. So: `redis.asyncio` and an `async def` dependency.
That is also correct for `create_meeting`, which is a plain `def` route that
runs in the threadpool: FastAPI solves dependencies on the event loop
regardless of how the body will be run, so one async dependency covers both
flavours and there is no second code path to keep in step.

The client is cached **per event loop** (a `WeakKeyDictionary`), because a
`redis.asyncio` pool's connections belong to the loop that opened them. In
production that is a singleton opened on the first limited request; it is the
tests, which drive async code with `asyncio.run(...)` per test, that would
otherwise be handed connections bound to a closed loop.

**No pooled DB connection is held across the Redis call.** This codebase has
been bitten by that three times (`101a114`, `79551d0`, `0460fac`) and the pool
is 8 against Supabase's 15. `get_db` takes its connection lazily on the first
query and `_ensure_user_row` ends its transaction on every path, so a
dependency that runs before the route body holds nothing. Gate 8 measures this
rather than assuming it, and the falsification below shows what moving the
check into the body would cost.

**The counter is atomic.** `INCR` then `EXPIRE` as two calls is the classic
bug: a process dying between them leaves a key with no TTL, which limits that
user *forever* and is invisible. This is a fixed window incremented and expired
in one Lua script, so the two cannot be separated — plus a `ttl < 0` repair
branch for a key left behind by an older build or a hand edit.

Fixed window rather than sliding: the worst case is 2N across a boundary, and
with defaults sized so a person never approaches N, that burst is tolerance
rather than a leak. A sliding-window ZSET would store N members per user to buy
back a factor of two on a *cost cap* — the wrong trade for the extra moving
part. A refused request still increments but never extends the TTL, so a client
retrying in a loop delays nothing.

### The decision: Redis is down → **fail open**

**If Redis is unreachable, the request is served.** Both choices were
defensible; this is why this one was taken, recorded here and in a block
comment in `rate_limit.py` so it is a decision rather than an accident:

- **This is a cost cap, not an authorisation check.** Nothing behind it is
  unsafe to serve, only expensive to serve a lot of. Failing closed treats an
  infrastructure blip as a permissions decision.
- **Chat is the feature that still works when Redis does not.** Transcription
  (A3) and dispatch (C2) are queued through Redis and are already degraded
  during an outage — but answering a question about an existing transcript
  needs only Postgres and Gemini. Failing closed would take the one still-
  working feature offline to protect a budget: a self-inflicted second symptom,
  during an incident, on the most visible surface in the product.
- **The exposure is bounded and attended.** Abuse still needs a valid Supabase
  JWT, the question is still capped at 4000 characters, and the user is still
  bounded by how fast a model answers. The first failure in each interval
  raises a Sentry event (A4) saying in as many words that the cap is off, with
  a running total, so the window is "until someone looks", not "indefinitely
  and silently".

What was never on the table is letting an unhandled exception decide it: an
uncaught `ConnectionError` here is fail-*closed* with a worse error message and
a Sentry traceback per request.

**The cost, measured:** during the live outage below, each request paid
`rate_limit_redis_timeout_seconds` (0.5s) waiting on a black-holed host before
being served. That is the price of fail-open on a hung Redis, and it is why the
timeout is set short and set on both `socket_connect_timeout` and
`socket_timeout` — "Redis is down" has two shapes and only the refused-socket
one is fast on its own.

**If this turns out to be wrong** — if a real bill arrives — the change is the
`return` in that `except` block becoming a `raise`, plus accepting that a Redis
outage is then a chat outage.

### The 429

`{"detail": "..."}` plus `Retry-After`, mirroring `_db_busy`
([main.py:129-137](../backend/app/main.py#L129-L137)). `Retry-After` is the
counter's real remaining TTL, not the window length.

> You've sent a lot of questions in a short time. Please try again in about a
> minute.

It says what to do and does not name the mechanism — "rate limit", "429",
"quota", "window" are our words for our problem, and a test asserts none of
them reach a chat bubble.

**No frontend change was needed, and this was confirmed rather than assumed.**
`useMeetingChat.js`'s `!response.ok` branch does
`throw new Error(detail?.detail || ...)` and the `catch` renders
`` `*${err.message}*` `` into the thread, so the sentence above is what the user
reads verbatim. The test that pins it also asserts the refusal is a real status
code with a JSON content-type and no `data:` frames — a 429 delivered *inside*
a 200 stream would surface as the generic "Something went wrong" instead.

### The defaults, and what they are based on

All in `config.py` in the existing style, all overridable by env var.

| Setting | Default | Basis |
|---|---|---|
| `RATE_LIMIT_ENABLED` | `true` | The revert. False is a genuine no-op — it never opens a connection. |
| `CHAT_RATE_LIMIT_REQUESTS` | `30` | per user, across every meeting they own |
| `CHAT_RATE_LIMIT_WINDOW_SECONDS` | `300` | |
| `MEETING_CREATE_RATE_LIMIT_REQUESTS` | `10` | |
| `MEETING_CREATE_RATE_LIMIT_WINDOW_SECONDS` | `300` | |
| `RATE_LIMIT_REDIS_TIMEOUT_SECONDS` | `0.5` | connect *and* read |

**Chat: 30 per 5 minutes** is one question every 10 seconds, sustained. A chat
turn is a retrieval pass plus a streamed answer — `chat_timeout_seconds` is 30
on its own — so a person cannot *read* 30 answers in 5 minutes, let alone ask
30 considered ones. Per user rather than per meeting, because the bill is per
user. On cost: a turn is roughly 10k input + 500 output tokens against
`gemini_input/output_cost_per_mtok` (0.30 / 2.50), about $0.004, so 30 per 5
minutes ceilings one user running flat out near $2/hour — a number you would
notice on a bill but not one that empties an account overnight. Unlimited is
the only genuinely dangerous value here; the exact number is not load-bearing.

**Meeting creation: 10 per 5 minutes.** Cheaper per call, but each one
dispatches a recorder — a headful Chrome plus ffmpeg at roughly 2GB — so what
it protects is the bot pool, not a model bill. Ten covers pasting several links
in a row plus every retry a frustrated user makes. It is *not* the path
calendar sync uses: scheduled meetings are dispatched by the scheduler sweep,
which never comes through this route.

A limit configured as `0` requests (or a `0` window) is treated as **off**,
with a warning, rather than as "refuse everyone". A limit that refuses everyone
is not a limit, it is an outage caused by a typo in an env var.

### Gates — what ran, and against what

35 tests. **Against a real Redis** (`TEST_REDIS_URL`, the throwaway container
in `tests/README.md`) — 21 of them; the other 14 are the fail-open ones, which
need a Redis that is deliberately *not* there.

| # | Gate | Verified |
|---|---|---|
| 1 | The N+1th chat request is a 429 with `Retry-After` and a readable `detail` | real Redis, over HTTP |
| 2 | `/chat` and `/chat/stream` share one budget | real Redis, over HTTP |
| 3 | Per user — A exhausting their limit does not touch B | real Redis, over HTTP |
| 4 | The window rolls; retries do not push it out | real Redis, wall clock |
| 5 | **N+5 simultaneous, exactly N pass** | real Redis, twice |
| 6 | Redis unreachable behaves as documented | real socket + **a really stopped container** |
| 7 | `POST /meetings` limited; a webhook is not, at 50 in a row | real Redis, over HTTP |
| 8 | No pooled DB connection held during the Redis call | real Redis, `engine.pool.checkedout()` |
| 9 | Full suites | backend **217** (was 182), meeting-bot **44** (unchanged) |

**Gate 5 was run two ways**, because it is the gate that matters and a
non-atomic counter passes every other test in the file: N+5 coroutines racing
`check()` on one loop (which is what catches a read-modify-write interleaving
at its awaits), and N+5 threads each with its own `TestClient`, its own event
loop and its own Redis connection — real concurrency at the socket. Plus a TTL
assertion on the surviving key, which is what catches INCR-then-EXPIRE
specifically: that variant lets exactly N through and leaves a counter that
never expires.

**Gate 6 was proven by actually stopping Redis, not by mocking a client.** Two
levels: the committed tests point `redis_url` at a port with nothing listening,
which is a genuine `ConnectionError` off a genuine refused socket; and
`docker stop` on the throwaway container was run by hand, which produced both
real shapes in sequence — `ConnectionError: Connection closed by server` for
the in-flight connection, then `TimeoutError: Timeout connecting to server` at
exactly the 0.5s budget once the port was gone. Every request was served, each
logged at ERROR, one Sentry event carried the running total, and the cap came
back on its own when the container returned — no restart.

**Each gate was falsified**, the way A1 and A3's were:

| Broken deliberately | What failed |
|---|---|
| Lua script → read-modify-write in Python | gate 5 coroutines: **8 of 8 passed, expected 3**; gate 5 HTTP: `[429,429,200,429,200,200,200,429]` — 4 passed, out of order; and the TTL assertion caught the orphaned key (`ttl == -1`) |
| `chat/stream` given its own scope | gate 2: alternating gave `[200]*6` — exactly double the allowance |
| Limiter moved into the route body, after `_assert_chattable` | gate 8: `[1] connection(s) checked out while the limiter was talking to Redis`, on all four route/cache variants |

### Consequences worth knowing

- **The suite runs with rate limiting off.** `conftest.py` sets
  `RATE_LIMIT_ENABLED=false`, because the limiter is a dependency on three
  routes and every unrelated test posting to one of them would otherwise open
  a connection to the *code default* `redis://localhost:6379` — passing (it
  fails open) but carrying a connection attempt and an error log for a
  subsystem it is not about, and quietly depending on whether the machine has
  a Redis on 6379. Set as an env var in `conftest.py` rather than
  monkeypatched, so `test_env_isolation.py` accounts for it the same way it
  accounts for the other deliberate stubs. Verified: the suite gives identical
  outcomes with the flag inverted from the shell.
- **`REDIS_URL` is now on the request path.** It was a broker (A3) and a cache
  (C3), both of which are worker-side and tolerate a blip invisibly. A
  misconfigured `REDIS_URL` now costs 0.5s per limited request. It does not
  cost correctness, which is the whole point of failing open.

---

<a id="b2--ci--done"></a>
## B2 — CI ✅ done

> **Risk:** none to the running system — this phase adds no application code.
> **Revert:** delete `.github/workflows/ci.yml`.
>
> Landed as one workflow, two jobs, plus a skip guard in
> `backend/tests/conftest.py`. No application code changed; no repository
> secret exists or is needed.

Until this, nothing ran either suite automatically. 217 backend tests and 44
meeting-bot tests only protected you when someone remembered.

### The trap it was built to avoid

A green CI that silently tests less than you think is worse than no CI: it
converts "nobody ran the tests" into "the tests passed."

This repo had that failure mode loaded and ready. Five backend test files skip
themselves when `TEST_REDIS_URL` is unset — `test_rate_limit.py`,
`test_bot_pool.py`, `test_bot_dispatch_queue.py`, `test_bot_auth_health.py`,
`test_transcription_queue.py`. Measured, not guessed: with Postgres and no
Redis the suite reports **`166 passed, 51 skipped`** and exits **0**.

Those 51 are B1's atomicity gate, C3's two-hosts-two-meetings gate, C4's
per-platform auth gate and A3's queue-durability gate — the concurrency work
these phases existed to do, and precisely the tests nobody re-runs by hand. A
workflow that provisioned Postgres and forgot Redis would have been green and
blind to all of it.

### The skip guard

**Mechanism: a `pytest_sessionfinish` hook at the bottom of `conftest.py`,
armed by `PYTEST_REQUIRE_NO_SKIPS=1`.** It collects every skipped report, lists
each nodeid with its reason, and sets `session.exitstatus` to a failure —
`session.exitstatus` being what pytest actually returns to the shell after that
hook, so printing alone would have left the run green.

Chosen over the alternatives on purpose:

- **Not a hard-coded test count.** It would need editing on every added test,
  and a stale expected-count is itself a way to go quietly wrong.
- **Not `--strict-markers`.** That polices marker *registration*, not skips.
- **Not grepping pytest's output in the workflow.** Parsing a summary line in
  YAML puts the check somewhere no local run ever exercises it.

Two deliberate properties:

- **Opt-in, not always-on.** Locally a partial run is genuinely useful: a
  developer with no Redis gets the 166 tests that do not need one plus a note
  about what they missed, rather than a red suite that teaches them to ignore
  it. Only CI sets the variable.
- **It refuses *any* skip, not just Redis ones.** There is no legitimately
  conditional test here today (with both services: `217 passed`, zero skipped),
  so a new skip is a question someone should have to answer in a pull request
  rather than a category pre-approved in advance.

### Why CI runs Python 3.12

Three Pythons were in play and the one that mattered had never run:

| Where | Version at B2 | Had ever run this code? | Version now |
|---|---|---|---|
| `backend/Dockerfile` — what deploys | **3.12** | **No** | 3.12 |
| `backend/Dockerfile.dev` — local containers | 3.11 | Yes | **3.12** (reconciled, below) |
| the developer's `backend/venv` | 3.13 | Yes | 3.13 |

`backend/Dockerfile` has never been built — every local image comes from
`docker compose up`, which uses `Dockerfile.dev`. The evidence was in the tree:
`backend/app/services/__pycache__/` held `cpython-311` and `cpython-313`
artifacts and no `cpython-312`. **The Python this project will deploy on had
never executed a line of it**, and the 217-test runs everyone quotes happened
on 3.13.

So CI runs 3.12. GitHub provisions it on its own runners, so this costs no
rebuild and changes nothing on anyone's machine — it just makes CI the first
place the deploy-time runtime is exercised at all.

**Result: 3.12 is clean.** All 217 pass on it, first try, in 16-18s. That was
the phase's largest open risk and it is now closed — a finding of "no finding",
which is worth recording precisely because it was not knowable beforehand.

**`Dockerfile.dev` reconciled to 3.12 — done.** It was deliberately left at
3.11 while B2 landed and sequenced after CI had shown 3.12 sound. The change is
the one `FROM` line; nothing else in the file needed to move. In
`docker-compose.yml` both `backend` and `worker` carry their own `build:` of
`./backend` + `Dockerfile.dev` (two images from one Dockerfile), so both were
rebuilt and restarted together; `meeting-bot`, `meeting-bot-2` and `frontend`
were not touched. `docker-compose.prod.yml` builds both from
`backend/Dockerfile`, which was already 3.12. The spread is now 3.12 local
compose / 3.12 CI / 3.12 deploy, with only the developer venv on 3.13.

What the gates showed:

- **Runtime:** `backend` and `worker` report Python 3.12.14 (both were 3.11.16).
- **Build:** 307s for both images on a cold cp312 wheel cache — ~204s of it the
  `apt-get install ffmpeg` layer, ~71s pip. Every compiled dependency came as a
  published cp312 or abi3 wheel; nothing built from source. The 83 installed
  packages are the same set in both images, and `pip check` is clean. Image
  size 1.24GB → 1.23GB.
- **Boot:** `alembic upgrade head` was a no-op (DB already at head
  `f4a7c2e9b1d6`, checked before the rebuild), uvicorn started, `GET /health`
  200, no import errors. The worker started arq with both job functions and the
  5s heartbeat loop, no import errors. The `/app/venv` anonymous volume was not
  a factor; no `--renew-anon-volumes` needed.
- **Suite on the 3.12 image:** `217 passed` with `PYTEST_REQUIRE_NO_SKIPS=1`.
  Host suites unchanged: backend 217 (venv, 3.13), meeting-bot 44.

**Nothing behaved differently on 3.12.** Two things about running the suite
*inside the compose `backend` container* did not work as written, and neither
is about Python — both are compose configuration that predates this change,
and both would have bitten a 3.11 container identically:

1. **`host.docker.internal` does not resolve in the compose services.** Their
   `dns: [8.8.8.8, 1.1.1.1]` (the Supabase-pooler DNS fix above) replaces
   Docker Desktop's resolver, which is what answers that name. A throwaway
   container on the same network resolves it to `192.168.65.254` without the
   override and fails with it. The `docker compose exec ... @host.docker.internal`
   invocation this reconciliation's brief prescribed (the repo's docs only
   describe running the suite from the host) therefore errors in all 217 tests
   at connect time.
2. **Compose injects `backend/.env` and `BOT_HOSTS` as real environment
   variables.** `IGNORE_DOTENV` stops the app reading the file, but `env_file:`
   puts its keys straight into the process. Via the gateway IP the run is
   `211 passed, 6 failed`: `test_env_isolation` correctly names six real
   credentials present in the environment, a webhook test gets 401, and four
   `test_retry_reupload` tests get 409 because two recorders are configured.
   The isolation guard is doing its job.

So the 217 above was first run in the rebuilt image with the same bind-mounted
code but outside compose's environment (`docker run --rm` of the backend image),
where `host.docker.internal` resolves as intended.

**It has since also passed inside the real compose `backend` container:**
`217 passed, 6 warnings in 11.94s` on 3.12.14, with no configuration change.
Two things make that run isolated. The throwaway test containers are attached
to the compose network (`docker network connect meeting-recorder-bot_meeting-net
meetiq-test-pg`, and the same for `meetiq-test-redis`), so they resolve by name
through Docker's embedded DNS, which the `dns:` override does not replace. And
pytest runs under `env -i`, so it inherits nothing compose injected:

```
docker compose exec backend env -i \
  PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 HOME=/root \
  PYTHONUNBUFFERED=1 \
  TEST_DATABASE_URL=postgresql://postgres:testpw@meetiq-test-pg:5432/meetiq_test \
  TEST_REDIS_URL=redis://meetiq-test-redis:6379 PYTEST_REQUIRE_NO_SKIPS=1 \
  python -m pytest -q -p no:cacheprovider
```

The `env -i` is there to keep the real credentials from `backend/.env` out of the
test process, not only to make the six failures go away. During that run
uvicorn did not reload, and the dev Redis gained no keys. Dockerfile.dev's own comment
that `docker compose exec backend python -m pytest` "works without installing
anything" is true of the install and not of the environment; that is left as an
open note, not fixed here.

One incidental difference, not a Python one: the 3.12 run shows one extra
`DeprecationWarning` (anyio's `BlockingPortal` alias, via Starlette's test
client). `anyio` is an unpinned transitive dependency; the fresh image resolved
4.15.1 while the older venv has 4.14.2.

### The finding this actually turned up

Not Python. **The first CI run failed in the meeting-bot suite**, three tests in
`stop.endpoint.test.js`, with `spawn pactl ENOENT`.

Those tests stage a session that fails fast — they point `AUTH_STATE_PATH` at a
file that does not exist, so `bot.join()` fails at its first step, and no
Chromium is ever launched. The file's own comment says the registered lifetime
was "measured at well over a second", and every case asserts the session is
still active before relying on it, "so a shorter window would fail loudly
rather than pass hollow." It did exactly that, which is the test design working.

What the measurement had not accounted for is that it was taken on Windows.
`AudioSink.provision()` is a documented no-op off Linux
([AudioSink.js:24-27](../meeting-bot/src/recording/AudioSink.js#L24-L27)), so on
the developer's machine the auth read really is the first thing that fails. On
Linux it shells out to `pactl` — and it runs at
[MeetingLifecycle.js:24](../meeting-bot/src/core/MeetingLifecycle.js#L24),
*before* `bot.join()`. With no PulseAudio it throws in milliseconds, the
session is deregistered before the assertion, and the precondition fails.

**Those three tests had never run their intended path on the platform this bot
deploys to.** The fix is to make CI that platform rather than to change the
tests: `meeting-bot/Dockerfile` installs `pulseaudio` and
`docker-entrypoint.sh` starts a daemon, so the workflow now does the same, with
the same flags (`-D --exit-idle-time=-1 --disallow-exit
--disallow-module-loading=no`) rather than relying on autospawn. 14-18s, and
all 44 pass.

**Two things this leaves open**, neither in scope here, both worth writing down
while they are understood:

1. `AudioSink.provision()` is called *outside* `runMeetingLifecycle`'s
   `try`/`catch`, so a provision failure escapes before the block that would
   have reported it. On a Linux host with a broken PulseAudio, a join answers
   202 and the session then vanishes with no failure webhook — the backend sits
   in `joining` until the watchdog sweeps it ten minutes later, instead of
   getting a specific error. Worth a B3 test.
2. Running `npm test` on a Linux dev machine without PulseAudio reproduces the
   same three failures. Nothing says so; the tests read as environment-neutral.

### No secrets, enforced

CI needs no repository secret, and none is configured. `conftest.py` sets
`IGNORE_DOTENV=1` and placeholder values, and `a2a80f9` / `d424ea9` made both
suites independent of any `.env` precisely so a runner with none behaves
identically to a developer's machine.

This is enforced rather than trusted, by a step that fails if any of it stops
being true: the workflow references no `secrets.` expression, no `.env` is
present in the checkout, and none of `GROQ_API_KEY`, `JINA_API_KEY`,
`SARVAM_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_TOKEN_ENCRYPTION_KEY`, `SUPABASE_KEY`, `SENTRY_DSN` or `DATABASE_URL`
is set in the job. The hazard is concrete: real Groq, Jina, Sarvam and Google
keys in a test process are one unstubbed fallback away from a billed call on
every push. If a future change here seems to need a secret, the isolation has
regressed and *that* is the bug.

### Shape

Two jobs, deliberately independent, so a Node failure never masks a Python one:

| Job | Runtime | Services | Notes |
|---|---|---|---|
| `backend` | Python **3.12** | `pgvector/pgvector:pg18`, `redis:7-alpine` | `PYTEST_REQUIRE_NO_SKIPS=1` |
| `meeting-bot` | Node **22** (matches `node:22-bookworm-slim`) | — | PulseAudio installed + started; `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` |

Triggers: pushes to `main`, and all pull requests. `concurrency` with
`cancel-in-progress` so a quick second commit does not leave two runs racing —
the repo is private and Actions minutes are metered, which is also why the
matrix is one Python and one Node rather than a grid.

The service containers are published on the **same non-default host ports**
`backend/tests/README.md` uses (55432, 56379), so the command in that file is
the command that runs in CI, character for character. Nothing on a runner would
collide at 5432/6379; the point is having one connection string to keep correct
instead of two.

`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1` on `npm ci`: `playwright` is a dependency
but no test launches a browser, and only `src/core/BrowserManager.js` imports it
(importing the module needs no binaries — only `.launch()` does). Without the
variable, `npm ci` spends minutes and several hundred MB fetching three browsers
nothing in the job opens. With it, `npm ci` is **1-2s**.

### Speed

**67–85 seconds wall clock** for a full green run, both jobs in parallel
(measured across two: runs `34707285275` and `34708004003`).

| | |
|---|---|
| `backend` job | 65–84s — containers 22–33s, `pip install` 18–19s, **pytest 15–18s** |
| `meeting-bot` job | 37–38s — PulseAudio 14–15s, `npm ci` 1–2s, **tests 14–17s** |

Not slow enough for anyone to route around, which was the bar. The pip and npm
caches are keyed on `requirements-dev.txt` and `package-lock.json`. The largest
single cost is Postgres + Redis container startup (15-33s), which is not
something to optimise away — it is the thing that makes the run mean anything.

### Gates

| # | Gate | Evidence |
|---|---|---|
| 1 | Both jobs pass on a real run against real service containers | **push to `main`: run `34708392463` — green.** Plus pull-request runs `34707285275` (85s), `34708004003` (67s) and `34708245941`. Both triggers exercised |
| 2 | **With the Redis service removed, the workflow fails** | run `34707389734` — backend red; the log reads `PYTEST_REQUIRE_NO_SKIPS=1 and 51 test(s) skipped`, then all 51 nodeids. Without the guard this exact run is `166 passed, 51 skipped` and **green** |
| 3 | A deliberately broken test fails the build | run `34708134338` — one sabotaged assertion per suite, **both** jobs red. Independently, runs `34706302175` and `34706516909` went red on the three genuine `pactl` failures |
| 4 | No repository secret configured; no real key in the job | none configured; the "No credentials" step passes and would fail if that changed |
| 5 | Local suites unchanged | backend **217**, meeting-bot **44** |

Gate 2 is the one that mattered and it was demonstrated by actually deleting
the service from the workflow and pushing, not by asserting it — the same
standard B1's Redis-outage gate was held to. Gate 3's proof was run on a
throwaway draft branch that was closed and deleted the moment it reported, so
no deliberately-broken commit is reachable from `main`.

**One process note, because it cost a red `main`.** The gate-2 commit was
merged to `main` while the demonstration was still in progress, so `main`
briefly carried a workflow with no Redis service. Restored in the same pull
request that added this section. If red runs are going to be part of proving a
CI change again, they belong on a branch nobody is watching for merge — which
is what gate 3 then did.

### Consequences

- **B3 now lands with CI already protecting it.** Broadening coverage was
  always the larger piece of work; it is much safer to do second, because every
  test it adds is run automatically from the moment it is written, and the skip
  guard means a new test that quietly does not run is a red build rather than a
  line in a table.
- **A `pull_request` run is the gate, and `main` is not protected.** Nothing
  prevents merging a red PR; the workflow reports, it does not enforce. Turning
  on a required status check is a repository setting, not a code change, and is
  the natural next tightening.

## B3 — Broaden test coverage

**This now lands with CI already under it**, which changes the phase's
character rather than just its safety margin. Every test B3 writes runs
automatically from the moment it is committed — on 3.12, against real Postgres
and real Redis — instead of protecting the repo only when someone remembers to
run it. And B2's skip guard means a new test that quietly *does not run* is a
red build rather than a row in a table nobody re-derives. Writing coverage into
a repo with no CI would have been the wrong order; that order is now fixed.

B2 also produced B3's first concrete item, found by CI rather than by reading:
`AudioSink.provision()` throws outside `runMeetingLifecycle`'s `try`/`catch`,
so on a Linux host with a broken PulseAudio a join answers 202 and then
disappears with no failure webhook — the backend waits in `joining` for the
watchdog instead of getting a specific error. See the finding in B2.

**A suite exists.** It was built as the gates of the phases that needed it
rather than as a phase of its own:

| File | Tests | Landed with |
|---|---|---|
| `test_rate_limit.py` | 35 | **B1** |
| `test_bot_dispatch_queue.py` | 32 | C2 |
| `test_bot_pool.py` | 31 | C3 |
| `test_jwt_auth.py` | 26 | A5 |
| `test_scheduler_claim.py` | 16 | A1 |
| `test_bot_auth_health.py` | 16 | C4 |
| `test_transcription_queue.py` | 15 | A3 |
| `test_observability.py` | 9 | A4 |
| `test_chat_connection_release.py` | 8 | `79551d0` |
| `test_search_transcript_session.py` | 7 | `0460fac` |
| `test_db_pool_errors.py` | 6 | `101a114` |
| `test_retry_reupload.py` | 5 | C3 |
| `test_auth_user_row_session.py` | 5 | `c5f2c84` |
| `test_get_meeting_session.py` | 4 | `d505d46` |
| `test_env_isolation.py` | 2 | `a2a80f9` |
| **total** | **217** | |

*(An earlier version of this table said "40 tests across A1/A3/A4". That was
true when Phase B was written and has been stale since A5.)*

The meeting-bot suite is 44, unchanged by B1 — nothing in that phase touches
it, which is the point of limiting on the backend side of the webhook.

Every one of those was *falsified* — the fix reverted, the test shown failing
with the real symptom.

What remains is **broadening coverage**, not creating a suite. For a system
whose correctness lives in retry ladders, provider fallbacks and a multi-status
state machine, the untested parts are still the largest production risk.
Highest-value targets, in order:

1. **The meeting status machine** — every transition, and that no path leaves a
   meeting non-terminal forever.
2. **Webhook idempotency** — the `rowcount == 0` "already processed" branch.
3. **The AI fallback ladders** — Gemini→Groq for chat, Gemini→Sarvam for
   transcription, Gemini→Jina for embedding. Assert the fallback fires on
   transient errors and does *not* fire on a 400.
4. **`chat_service` reset semantics** — a mid-answer stream break resets and
   falls back rather than erroring, and a failed request stores nothing.
5. **Worker failure and retry** — A3's one untested-live path. `j_failed=0
   j_retried=0` to date, so `record_terminal_failure`'s branch on transcript
   and the `IndexingFailed` re-raise have only ever run against stubs.

`pytest` is in `requirements-dev.txt`. `pytest-asyncio` is **not** needed — the
async paths are driven with `asyncio.run(...)` directly, which keeps the async
boundary explicit in each test.

**Isolation fixed, a prerequisite for CI.** The backend suite used to read the
developer's `backend/.env` in two independent ways: `load_dotenv()` in
`database.py` copied every key into `os.environ`, and `Settings`' `env_file` read
the file directly. So results depended on the machine; four scheduler tests
failed whenever `BOT_DISPATCH_USE_QUEUE=true` was in someone's `.env`. Real
Groq, Jina, Sarvam and Google secrets were also sitting in the test process,
one unstubbed fallback away from a billed call. A CI runner, having no `.env`,
would have run yet another configuration.
`conftest.py` now sets `IGNORE_DOTENV=1` before any `app.*` import, and both
paths honour it. Either path alone still leaks: closing only `load_dotenv`
left 10 settings read from the file, and closing only `env_file` left 12 keys
in `os.environ`.

- **The guard:** `test_env_isolation.py` failed on the old code with those
  names, and never values. A scan for every real `.env` value found 0 of 17 in
  the output.
- **Real `.env` in place:** the suite now passes with both queue flags `true`
  there and nothing pinned on the command line. A `git worktree` with no `.env`
  anywhere above it gives the same result.
- **Real runs are unchanged:** the backend container still reads
  `bot_dispatch_use_queue=True`, and host `alembic current` still resolves.
- **Flag dependencies:** running the suite with each boolean setting inverted by
  env var exposed two in `test_scheduler_claim.py`.
  `calendar_scheduler_enabled=false` failed 12 tests, and
  `bot_dispatch_use_queue=true` failed the original 4. Both are now pinned in
  that file, and every inversion gives identical per-test outcomes —
  `rate_limit_enabled` included, checked when B1 added it.

- **The meeting-bot suite is isolated too.** `SupabaseUploader.js` called
  `dotenv.config()` at import, so every test importing `server.js` loaded
  `meeting-bot/.env`: the real `BEARER_TOKEN`, `BACKEND_WEBHOOK_URL`, and the
  service-role `SUPABASE_KEY` for any test that hadn't set its own. That call
  was redundant in production: `src/index.js` imports `dotenv/config` first,
  and compose supplies the env. It is removed.
  - `test/env.isolation.test.js` failed on the old code naming 6 keys that
    appeared on import, with 0 values in the output.
  - Three capacity test files turned out to have been relying on `.env` for
    `SUPABASE_URL`, and crashed at import without it. They now set the same
    placeholders the upload tests already did.

## B4 — `log_cost` as a real metric

Per-user AI spend is a business number, not a debug line. B1 gives this a
second reason to exist: the rate limit defaults above were sized from an
*estimated* per-turn cost, and nothing currently measures the real one. A
metric would let the caps be set from data rather than from arithmetic.

**Why this comes before B5, not after.** `cost_tracker.log_cost` ends in a
`print()` ([cost_tracker.py:28](../backend/app/services/cost_tracker.py#L28)),
so it is *one of the 44 calls B5 sweeps*. Running the sweep first would convert
that line to `logger.*` and then immediately rewrite the same function into a
metric — one file touched twice, the first pass discarded. Doing B4 first
retires that `print()` as part of the work that replaces it, and B5 inherits a
smaller sweep.

## B5 — `print()` → `logger.*`

Convert the remaining `print()` calls in `backend/app` to `logger.*`, with
`meeting_id` and `user_id` as structured fields rather than interpolated into
the message. This was originally listed under A4 and moved here on the A4
deploy: the calls already emit (`PYTHONUNBUFFERED` is set in both Dockerfiles)
and are already visible in `docker compose logs`, so the rewrite buys log
*structure*, not log *visibility* — and it is a diff across every file in
`app/services/`, which is the opposite of what a phase whose whole purpose was
de-risking the A5 deploy wanted to ship. A4 left root logging on stdout so the
two styles interleave in order in the meantime.

**Last in Phase B deliberately.** It is the widest and most mechanical diff in
the plan and the one that reduces risk least, so it lands when the safety net is
strongest: B3's broadened coverage, run automatically by B2's CI — which now
exists, so this sweep will be the first wide diff in the project's history that
is checked by something other than the author.

**The count, re-measured by B2: 44, not the 45 this plan carried from A4.** By
AST, not by `grep print(` — which reports 46, two of them a docstring in
`observability.py` and a shell one-liner in a `config.py` comment. B4 retires
one more (`cost_tracker`), leaving **43**.

Re-counting also corrected the *shape*, which matters more than the number.
This is not "a diff across every file in `app/services/`": it is one file plus
a scattering.

| File | Calls |
|---|---|
| `services/transcription_service.py` | 26 |
| `rag/chat_service.py` | 5 |
| `api/meetings.py` | 3 |
| `services/embedding_service.py` | 3 |
| `services/transcription_fallback_sarvam.py` | 2 |
| `api/chat.py`, `api/webhooks.py`, `rag/chat_fallback_groq.py`, `services/cost_tracker.py`, `services/embedding_fallback_jina.py` | 1 each |

**26 of 44 are in `transcription_service.py`** — 59% of the work in one file,
and the file where structured `meeting_id`/`user_id` fields are worth the most,
since A4 already established that an unattributed transcription failure is the
least useful kind. That makes this splittable in a way the plan previously
assumed it was not: `transcription_service.py` alone is a coherent, reviewable
change that delivers most of the value, and the remaining 18 across nine files
can follow or wait indefinitely.

## What deferring the rest costs you

Deferring B3–B5 is defensible — Phase A unblocks scaling and none of them do.
(B2 is no longer among them: CI shipped, because "the tests only protect you
when someone remembers" stopped being acceptable once there were 261 of them.)
The consequence is explicit and already priced into the plan above: **A1, A3
and every phase since carry their own tests as part of the phase.** Those are
the gates you cannot skip, because A1 is a concurrency fix that is unobservable
without one, and A3 can silently leave a `completed` meeting with nothing to
search. That is why Sentry was pulled forward into A4 rather than left here —
and why B1 was pulled out of here entirely.

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

## Known bug in this tier, found while testing A3 ✅ fixed

> **Fixed** — see [the fix](#the-fix-retry-keep-recover) at the end of this
> section. The write-up below is the bug as it was found, kept for the record.

**A failed upload destroys the only copy of the recording.** Independent of
the bot-pool work below, and worth fixing sooner - it is data loss, not a
scaling limit.

`MeetingLifecycle.js` unlinks the local `.m4a` in a block deliberately placed
outside the upload's success/failure handling:

```js
} catch (uploadErr) {
  session.markFailed(`Upload failed: ${uploadErr.message}`);
}

// Cleanup is best-effort and deliberately separate from the
// upload's success/failure ...
fs.unlinkSync(localPath);
```

The comment's intent is sound - a file still locked by ffmpeg on Windows
should not be misreported as an upload failure - but the consequence is that
when the upload genuinely fails, the audio is deleted anyway. Observed for
real: meeting `4e7fe19b` recorded fine (`ffmpeg closed with code 0`), the
upload failed on the DNS fault above, and the file was unlinked one line
later. Checked afterwards - `storage files = EMPTY`, nothing on disk.

**Retry cannot help.** `retry_meeting`
([meetings.py:127-132](../backend/app/api/meetings.py#L127-L132)) re-runs
*transcription* from a file already in storage; it checks the bucket first
and correctly 404s with "Recording file not found in storage. Cannot retry."
There is no path that re-uploads, because there is nothing left to upload.

The DNS fix makes this rarer, not safe - a Supabase outage, an expired token
or a dropped transfer would strand a recording the same way. The fix is to
unlink only when the upload succeeded, and to keep the file otherwise; making
that *useful* also needs a backend path that retries the upload rather than
only the transcription, which is why it is written up here rather than done
in passing.

### The fix: retry, keep, recover

Three parts, each useless without the others.

1. **Retry the upload.** `SupabaseUploader.upload` makes up to 4 attempts
   (waits of 2s, 5s, 10s — at most 17s waiting). Network failures and 5xx are
   retried; a Storage 4xx other than 408/429 fails at once, since re-sending
   an 86MB body to be refused again helps no one. `upsert: true` makes a
   repeat upload to the same key safe. **Each attempt opens its own read
   stream**: a stream consumed by a failed attempt cannot be re-read, and
   reusing it makes attempt 2 upload zero bytes *and report success*. A test
   pins this by byte count. With the stream hoisted out of the loop it fails
   `0 !== 300000`. The plain "retry succeeds" test *passes* in that state,
   which is exactly why it is not enough on its own.
2. **Delete only after a confirmed upload.** The unlink in
   `MeetingLifecycle.uploadRecording` is gated on a storage key having been
   returned. It is still best-effort (the Windows lock concern stands), just no
   longer unconditional. On final failure the file stays and the meeting fails
   with `Upload failed: <cause>. The recording is preserved on the recorder and
   can be retried.` That message is distinct from a recording that is actually
   gone. Putting the unconditional unlink back fails the "permanent failure"
   test with `recording must survive a failed upload`.
3. **Make a kept file recoverable.** meeting-bot has `POST /reupload`
   (`requireAuth`, `{meetingId, userId}`). The file is found by id through
   `RecordingFile.pathFor`. No file → 404; an active or already re-uploading
   session → 409; otherwise **202**, then the upload (with retry), the unlink
   on success, and the normal `completed` webhook carrying `recording_path`.
   It is async because the backend calls the bot with `timeout=10`.
   Re-uploads are tracked apart from `activeMeetings`, so they never count
   against `/capacity`.

   On the backend, `retry_meeting` asks the bot to re-upload when storage has no
   file. **The meeting is moved to `uploading`, and committed, before the bot is
   called.** `retry_meeting` claims `failed → transcribing`, and the webhook's
   `completed` branch only accepts meetings `notin_(["transcribing",
   "completed"])`. A meeting left in `transcribing` would have the recovered
   recording's completion rejected as `already_processed`, and it would never
   be transcribed. `uploading` also carries the watchdog's 20-minute TTL,
   which bounds a re-upload whose webhook never arrives. If the bot call errors,
   the meeting goes back to `failed` through an update conditional on
   `uploading`, so a slow-but-successful bot whose webhook already landed is not
   overwritten. A bot 404 means the recording is on neither side, and the old
   "Recording file not found in storage. Cannot retry." message is kept.

Tests: `meeting-bot/test/upload.retry.test.js` and
`reupload.endpoint.test.js` (8 tests); `backend/tests/test_retry_reupload.py`
(5 tests, including the full retry → `uploading` → `completed` webhook
accepted → `transcribing` chain). With the flip to `uploading` removed, 4 of
those 5 fail. Besides the webhook rejection, the fail-back conditional on
`uploading` never matches, so a failed bot call strands the meeting in
`transcribing`.

**Verified live** against the compose stack and real Supabase, using two
throwaway meeting rows that were deleted afterwards:

- *Recovery.* A `failed` meeting, storage empty, with a 61.5s spoken `.m4a` at
  `recordings/<id>.m4a`. Retry → `{"status": "reuploading"}`, row `uploading` →
  bot `Upload successful` → `Local recording file cleaned up` → webhook
  `200` → `transcribing` → `completed` about 11s later, with transcript and 1
  chunk. The storage object was 1,493,134 bytes, byte-identical in size to the
  local file.
- *Genuinely gone.* A `failed` meeting with no file on either side. Retry →
  404 `Recording file not found in storage. Cannot retry.`, row back to
  `failed` with that message. The bot, asked directly, answered
  `404 No preserved recording for meeting <id>`.

**Known limitations, deliberately not built:**

- **Kept files accumulate.** A preserved recording now outlives its meeting
  until someone presses Retry, and nothing sweeps `meeting-bot/recordings/`.
  This predates the fix: the failed-before-upload branch has always kept
  files, and `3427298f-….m4a` (43KB, Aug 23) has been sitting there since. A
  retention policy is a separate decision, not a side effect of this one.
  Deleting a meeting does not remove its kept file either.
- **Retry now recovers any kept file, not only failed uploads.** A meeting
  cancelled by the user mid-recording also keeps its (finalised) file, so Retry
  on it uploads and transcribes what was recorded up to the cancel, where it
  used to say "not found".
- **A crashed bot's partial `.m4a` is not recoverable.** ffmpeg writes the
  index at the end, so a file from a crash mid-meeting will not play. Nothing
  here tries to detect that: Retry would re-upload it, and it would fail at
  transcription.
- **Duration is not reported on a re-upload.** The original session's timings
  are gone. Nothing is lost, though: the backend never stored a duration for the
  failed meeting either.

## Design

**C1. Make capacity externally visible.** ~~Add `GET /capacity` to the bot
returning `{active, max, available}`.~~ **done** — see below.

**C2. Move dispatch behind a queue.** ~~Reuse A3's broker. `trigger_bot_join`
enqueues a join request instead of posting synchronously; a dispatcher assigns
queued meetings to bots with free capacity. This turns "meeting lost" into
"meeting waits."~~ **done** — see below.

> **Resolved (2026-09-12): Option A, a credential pool** — a separate
> Google/Zoom account provisioned per bot host, each with its own
> `auth.json`/`zoom-auth.json`, permanently assigned. No shared login state
> and no locking to build; `AuthKeepAlive` already keeps one identity alive
> per bot, so this is more of the same per host rather than a new mechanism.
> Chosen over shared storage with locking because it isolates failures — one
> host's login trouble can't affect another's — and it's the option this
> plan can actually verify without multiple accounts to test locking against.
> Cost accepted: N accounts to create and keep session-alive instead of one.
> This is what C3's registry now has to model — one identity per host, not a
> pool shared across hosts.

**C3. Bot registry.** ~~Bots register themselves (host, capacity, heartbeat) in
Postgres or Redis on boot; the dispatcher picks a host with free capacity and
records the assignment. `stop_bot` looks up the assigned host rather than
assuming one URL. A missed heartbeat means the host is dead and its in-flight
meetings get swept — the existing watchdog TTLs already handle the meeting-side
cleanup.~~ **done** — see below. Shipped *pull*-based rather than
self-registering, which is the one thing the text above got wrong; the rest of
it, including the claim about the watchdog, is what shipped.

**C4. Per-host auth identity.** ~~The hard part, and the reason this is a
redesign.~~ **done** — and that framing was wrong, which is worth recording
rather than quietly deleting. See C4's section below: the path-resolution
mechanism already existed, so the credential half was config. The work that
actually needed building was something this design text never mentioned —
auth health gating dispatch. Each bot host has its own Google/Zoom identity; a
shared `auth.json` across hosts would mean concurrent sessions fighting over
one credential, and `BrowserManager`'s `storageStateWriteQueues`
([BrowserManager.js:73-80](../meeting-bot/src/core/BrowserManager.js#L73-L80))
only serialises writes *within* a process. **Decided: a credential per host
from a pool** (not shared storage with locking) — see the resolved note above
C3's Design.

**C5. Remove the single-session fallback.** ~~`/stop` without a `meetingId`
resolves to "the only active meeting"
([server.js:119-132](../meeting-bot/src/api/server.js#L119-L132)). Once the
backend always knows the assignment, make `meetingId` required and delete the
fallback. The code already anticipates this.~~ **done** — see below. It was
what it looked like: a deletion, in `meeting-bot` only, with no backend
change.

## C1 — `GET /capacity` ✅ done

> **Shipped** in `meeting-bot` only. **Risk:** none — one new read-only route;
> no existing route, handler or dependency changed. **Revert:** delete the
> route.
>
> Returns `{active, max, available, meetingIds}`. `meetingIds` is not in the
> design text above and costs nothing: it turns "the bot says it's busy" into
> "busy with *which* meeting", which is the question actually asked when
> debugging. Three things worth knowing:
>
> - **It requires auth, unlike `/health`.** `/health` is deliberately
>   unauthenticated because health checks must be; `/capacity` discloses
>   operational state, and its only consumer from C2 onward is the backend,
>   which already sends the bearer token.
> - **Availability and admission are now one expression.** The join handler's
>   `activeMeetings.size >= MAX_CONCURRENT_MEETINGS` check was replaced by
>   `computeCapacity(...).available === 0`, the same call `/capacity` answers
>   with. Had the two been computed separately they would eventually have
>   disagreed, and an endpoint that advertises capacity a join then rejects is
>   worse than no endpoint. `available` is floored at 0, which is reachable by
>   lowering `MAX_CONCURRENT_MEETINGS` while meetings are in flight.
> - **`meeting-bot` now has tests, and no test dependency.** There were none.
>   The image is `node:22-bookworm-slim`, so `node --test` with `node:test`,
>   `node:assert` and global `fetch` needed nothing added to `package.json`
>   beyond `"test": "node --test"` — no devDependencies, no framework.
>   13 tests in `meeting-bot/test/`. Two constraints shaped them:
>   `MAX_CONCURRENT_MEETINGS` is read once at module load, so a test wanting a
>   non-default `max` must set it *before* importing the app (each `node --test`
>   file gets its own process, which makes that safe); and `activeMeetings` is
>   module-private with no way to fake a recording in-process, so the
>   arithmetic lives in an exported pure function tested at any count, with the
>   endpoint tests covering only the wiring. The agreement in point 2 is pinned
>   at the one boundary reachable without a browser: with
>   `MAX_CONCURRENT_MEETINGS=0`, `/capacity` reports `available: 0` and a
>   valid `POST /google/join` returns 409, because `0 >= 0` trips admission
>   before any `MeetingSession` is constructed.
>
> **Deliberately not built:** no backend consumer. `bot_service.py` is
> untouched — C2 is what dispatches against this endpoint, and a caller written
> before the queue exists would be speculative.

## C2 — Dispatch moves behind a queue ✅ done

> **Shipped** in `backend/` plus one `frontend/src/lib/status.js` entry.
> **Risk:** medium — it changes what `POST /meetings` returns when the bot is
> full. **Revert:** `BOT_DISPATCH_USE_QUEUE=false` and recreate — no code
> change, no rebuild, same two-deploy pattern as A3 and A5.
>
> `trigger_bot_join` stays the seam both callers (`meetings.py` and
> `scheduler.py`) go through; with the flag on it enqueues
> `dispatch_bot_join_job` instead of posting. The job runs in the **existing**
> arq worker against the **existing** `SessionLocal` — no new broker, no new
> process, no second `create_engine` (the Supabase pool limit at the end of A5
> is still open and unfixed). It asks C1's `GET /capacity`, posts the join if
> there is room, and re-enqueues itself deferred by 30s if there is not.
> 25 tests in `backend/tests/test_bot_dispatch_queue.py` (32 with the
> follow-up below). Five things worth
> knowing:
>
> - **A queued meeting could not reuse `joining`, and that was the whole
>   phase.** `watchdog_joining_ttl_minutes` is 10, so a meeting parked in
>   `joining` while it waited would be swept to `failed` after 11 minutes of
>   doing exactly what it was asked to do — the same lost recording this phase
>   exists to prevent, just slower. Hence a distinct `queued` status with its
>   own 30-minute TTL, and `queued` added to `_NON_TERMINAL_STATUSES`.
> - **The dispatcher's cap and the watchdog's TTL are ordered on purpose.**
>   40 attempts x 30s = 20 minutes of waiting, then the *dispatcher* writes the
>   failure — "Waited 20 minutes for a recorder that could take this meeting and never got one - the recorder was busy (1/1 in use)". The watchdog's 30-minute `queued` TTL
>   never fires first, so the user gets that message rather than "Timed out
>   while 'queued' - swept by watchdog". A test asserts the inequality on the
>   shipped defaults, because it is a config relationship, not a code path.
> - **`/capacity` is an optimisation; the bot's 409 is the authority.** The
>   check and the join are not atomic, so two jobs can both read
>   `available: 1`. A 409 re-queues rather than fails. C1 made the endpoint and
>   the admission rule share one expression, so the number is honest — it is
>   just not a lock.
> - **The dispatcher claims `queued -> joining` *before* posting**, the same
>   conditional-UPDATE pattern as `scheduler._claim`, so two workers cannot
>   both post; a join that then fails is put back to `queued`. The cost is that
>   a crash between the claim and the post leaves the meeting in `joining` with
>   no bot — bounded by the 10-minute joining TTL, which is exactly what a
>   failed synchronous join did before C2.
> - **This was not backend-only.** `status.js` maps status to label and tone,
>   and a status missing from it renders as nothing. `queued` is labelled
>   "Waiting for a recorder" — not "Joining", which would be a lie — and its
>   `processing` tone is load-bearing: `Dashboard.jsx` polls while any meeting
>   has that tone, so a queued meeting refreshes itself into "Joining" on its
>   own.
>
> **Deliberate behaviour change.** `POST /meetings` used to return **502
> "Could not start recording bot"** when the bot was full or unreachable. With
> the flag on it returns **success**, and the meeting shows as `queued`. That
> is the point of the phase, but it means the API no longer tells the caller
> synchronously that recording will not start — the meeting's status does,
> moments later. The one case that still 502s is the *enqueue* failing (Redis
> down), which is the only remaining "this genuinely will not happen" answer.
>
> **Two gaps from the first cut, since closed:**
>
> - **A queued meeting can be stopped, not only deleted.** `stop_meeting`
>   originally 409'd on anything outside `("joining", "waiting_for_admission",
>   "recording")`. A queued meeting has no bot session, so it is now cancelled
>   directly by `bot_dispatch.cancel_queued_meeting` — a `queued -> failed`
>   conditional UPDATE that never calls the bot. It races the dispatcher's
>   `queued -> joining` claim fairly: both are guarded on `status == "queued"`,
>   so Postgres lets exactly one match. If the stop wins, the job's next
>   attempt drops without contacting the bot; if the dispatcher won, the route
>   falls through and stops the now-joining bot the ordinary way. Because the
>   terminal state is known immediately, the route returns the updated meeting
>   and `MeetingView` applies it at once, rather than re-enabling the button
>   for up to 5s while waiting on a webhook that will never come.
> - **`MeetingDetails.jsx` shows a progress card for `queued`** — "Waiting for
>   a free recorder…", with a line explaining the recorder is busy with
>   another meeting, and a **Cancel** button (not "Stop bot": there is no bot
>   yet).
>
> 7 more tests in the same file (32 total): the cancel never reaches the bot,
> a stopped meeting's job drops, a stop that loses the race to the dispatcher
> stops the bot instead of overwriting the claim, and meetings with nothing to
> stop still 409.

## C3 — The bot registry ✅ done

> **Shipped** in `backend/` plus a second `meeting-bot` service in
> `docker-compose.yml`. **`meeting-bot/src/` is untouched** — see the first
> decision below. **Risk:** high; this is the redesign the phase was budgeted
> as. **Revert:** unset `BOT_HOSTS` and recreate. The pool becomes a pool of
> one built from `MEETING_BOT_URL`, `require_host` resolves every meeting's
> `bot_host_id` (including `null`) back to it, and the behaviour is C2's
> exactly. No rebuild, no code change, and the new column can stay — it is
> nullable and nothing requires it to be set.
>
> **Two decisions departed from the design text above, both deliberate.**
>
> - **Hosts are an operator-maintained list; bots do not self-register.** The
>   text said they would. C4 already commits to creating a Google/Zoom account
>   and provisioning a machine by hand for every host — that is deliberate
>   human work, not autoscaling, so the host list is *already* human-maintained
>   and `BOT_HOSTS` just writes it down. The payoff is that **`meeting-bot`
>   needed no new code, no `REDIS_URL` and no new outbound credential** to join
>   a pool: the backend pulls `GET /capacity`, which C1 already shipped. A
>   push-based registry would have meant a second service writing to the
>   broker, a credential for it, and a boot-order dependency, to learn the same
>   number.
> - **Two lifetimes, two stores.** "Which hosts are alive and how loaded" is
>   ephemeral and refreshes every few seconds, so it is **Redis** — a poll loop
>   in the existing arq worker writes `{host_id, active, max, available,
>   last_seen}` per host, and a host not refreshed inside
>   `BOT_HEARTBEAT_TTL_SECONDS` is treated as dead. "Which host this meeting
>   went to" has to outlive that cache and survive a Redis restart, because
>   stop/delete need it for as long as the row exists, so it is **a column**:
>   `meetings.bot_host_id`, nullable, migration `f4a7c2e9b1d6`.
>
> Seven things worth knowing:
>
> - **The dispatcher reads the cache; it does not call the hosts.** With N
>   hosts and a queue of meetings each retrying every 30 seconds, live-calling
>   every host on every attempt is O(queued × hosts) requests to answer a
>   question whose answer changes about as often as a meeting starts. Polling
>   is O(hosts) per interval regardless of queue depth. Visible in the dead-host
>   run below: one "did not answer" line per 5-second poll, not one per
>   dispatch attempt.
> - **The host is written in the same UPDATE as the `queued → joining`
>   claim.** One statement, so there is no window in which a meeting is
>   `joining` with no host recorded — which is precisely the state stop and
>   delete cannot act on. A test peeks at the row from another connection at
>   the moment the join is posted to pin it.
> - **A reservation, not a lock.** C2's caveat stands: the cache and the bot's
>   admission rule are not read atomically, and the bot's 409 is still the
>   authority. What C3 adds is an atomic `INCR` per host so two dispatches
>   arriving together do not both pile onto host A while B sits idle — without
>   it the loser waits a full 30-second retry for a recorder that was free the
>   whole time. Reservations expire rather than being released on success: a
>   bot registers a session before it answers 202, so one poll later the real
>   `available` already reflects the join.
> - **A failed poll writes nothing at all.** It does not refresh `last_seen`
>   with an `available: 0` entry, because that would make a dead host
>   indistinguishable from a live idle one at a glance. A dead host's timestamp
>   simply freezes, which is what makes "last heard from bot-b 94s ago" a
>   diagnosis. Entries are retained several TTLs past death so that stays
>   readable, rather than expiring into a missing key.
> - **`null` fails *open* on one host and *closed* on several.** Three things
>   legitimately have no host: every meeting from before this migration, every
>   meeting still `queued`, and every meeting joined on the synchronous
>   `BOT_DISPATCH_USE_QUEUE=false` path. With one host configured there is
>   nothing to get wrong, so `require_host(None)` resolves to it and a
>   single-host install is untouched by this phase. With two, the question is
>   genuinely unanswerable and it raises `UnknownBotHost`, which the routes turn
>   into a **409 naming the reason** — not a coin flip between hosts, and not a
>   500. Delete is the exception: not knowing which bot to tell is a reason to
>   skip the stop, not to refuse to delete the user's meeting.
> - **`POST /reupload` routes by host too**, though it is not in the gate. A
>   recording kept after a failed upload is on the disk of the host that made
>   it and nowhere else, so with a pool, asking "the" bot means asking a
>   machine that has no such file. The second compose service therefore gets
>   its own `recordings-2/` — unlike the auth files, which are deliberately
>   shared until C4.
> - **The watchdog needed no new code, and that was checked rather than
>   assumed.** See below.
>
> ### The watchdog claim, verified
>
> The design text asserts that a missed heartbeat needs no meeting-side cleanup
> because the existing watchdog TTLs already handle it. That is checkable, so
> it was checked instead of trusted. `watchdog._ttl_minutes_for` sweeps purely
> on `(status, updated_at)` and has no notion of a host; a dead host stops
> sending status webhooks, so the meeting's `updated_at` stops advancing and
> the per-status TTL fails it exactly as it would any other stuck meeting. The
> claim holds, so **C3 adds no sweep code** — the proof is
> `test_the_existing_watchdog_sweeps_a_meeting_whose_host_disappeared`
> (a meeting recorded against an absent host, `updated_at` frozen past the
> `recording` TTL, swept to `failed`), plus a test asserting the watchdog
> module mentions neither `bot_host` nor `bot_registry`, so a redundant second
> cleanup path cannot be added here by accident.
>
> ### Gate — results
>
> Run against the compose stack with two `meeting-bot` containers, both at
> `MAX_CONCURRENT_MEETINGS=1`, using four throwaway meeting rows that were
> deleted afterwards.
>
> 1. **Two bots, two meetings, one each.** Two meetings queued and dispatched
>    together landed 28ms apart on *different* hosts — read off the column, not
>    a log line:
>
>    ```
>    10085c18-…  status=joining  bot_host_id=bot-a
>    ca305627-…  status=joining  bot_host_id=bot-b
>    ```
>
>    This is the phase. Before it, the second meeting would have waited 30
>    seconds for the first recorder while the second sat idle, because there
>    was no way to address it.
> 2. **Dead host excluded.** `docker compose stop meeting-bot-2`. `bot-b`'s
>    `last_seen` froze at `1789199881.8` while `bot-a`'s kept advancing
>    (`…891.9 → …917.4`); once past the 20s TTL, `live_hosts()` returned
>    `['bot-a']` alone. The next meeting went to `bot-a` **on its first
>    attempt** — no retry against the dead host.
> 3. **The watchdog claim, proven by test** — above. No new sweep code.
> 4. **Stop routes to the right host.** A meeting recording on `bot-b`:
>    `stop_meeting` → `{'status': 'stopping'}`, and
>    `[server] Stop requested for meeting ca305627-…` appears in
>    **meeting-bot-2's** log and nowhere in meeting-bot's. The unit test asserts
>    the same thing at the URL (`http://meeting-bot-2:3000/stop`), because a
>    stub of `stop_bot` would happily "route" to a host whose URL was never used.
> 5. **A `queued` meeting is unaffected.** With one recorder busy and the other
>    stopped, a new meeting sat at `status=queued bot_host_id=None` and
>    `cancel_queued_meeting` cancelled it to `failed` without either recorder
>    ever seeing its id. The cancel path never resolves a host — a test makes
>    `require_host` raise to prove it is not called, since resolving would 409
>    on a two-host pool and break the Cancel button.
> 6. **Empty cache after a Redis restart.** `FLUSHALL` + restart: the cache read
>    back empty and `claim_host` returned *"no recorder is reporting in (none of
>    the 2 configured have answered /capacity — is the worker's heartbeat loop
>    running?)"* — a reason that names its own cause — and repopulated both
>    hosts within one 5-second interval once the poller ran. Never "all hosts
>    down forever".
> 7. **Suites.** Backend **166 passed** (135 on `main` before this phase, +31
>    in the new `tests/test_bot_pool.py`); meeting-bot **22 passed**, unchanged,
>    since `meeting-bot/src/` was not touched.
>
> **Found while running gate 6, and not fixed here:** `docker-compose.yml`
> (dev) sets no `restart:` policy on *any* service, so when Redis restarted the
> arq worker died inside its own shutdown path
> (`redis.exceptions.ConnectionError` from `arq/worker.py:869`) and stayed
> down — and with it the heartbeat loop. Pre-existing, and A3/C2 have the same
> exposure; C3 only raises the stakes, because the poller lives there too.
> **`docker-compose.prod.yml` already sets `restart: unless-stopped` on every
> service**, so production recovers on its own and this is a dev-only gap.
> Mirroring the prod policies into the dev file is a change to all five
> services, not one, so it is left as a decision rather than made in passing.
>
> **Deliberately not built:** per-host credentials (C4 — the two containers
> share one `auth.json`/`zoom-auth.json` on purpose, which is the expected
> state until then, and two *simultaneous real* recordings will fight over that
> one identity); removing `/stop`'s no-`meetingId` fallback (C5). No change to
> `docker-compose.prod.yml`: with `BOT_HOSTS` unset it is a pool of one built
> from `MEETING_BOT_URL`, which is what a single-host deployment should be.

## C4 — Per-host auth identity ✅ done

> **Shipped** in `meeting-bot/` (a new `AuthHealth.js`, plus wiring in
> `AuthKeepAlive.js`, `MeetingLifecycle.js`, `server.js`, `index.js` and both
> generator scripts), `backend/app/services/bot_registry.py` and
> `bot_dispatch.py`, and `docker-compose.yml`. **No migration, no new config on
> the backend.** **Risk:** low — far lower than this plan predicted, for the
> reason below. **Revert:** unset `AUTH_STATE_PATH`/`ZOOM_AUTH_STATE_PATH` and
> recreate; both hosts fall back to the repo-root files and the pool behaves as
> it did under C3. The health gating reverts with it, because a bot reporting
> nothing is treated as healthy by design.
>
> ### The "this is a redesign" framing was wrong
>
> This plan called C4 "the hard part, and the reason this is a redesign", and
> budgeted for building credential distribution. Checked against the code, most
> of the mechanism was already there and had been for some time:
>
> - `BrowserManager.resolveAuthStatePath(platform)` already honoured
>   `AUTH_STATE_PATH` / `ZOOM_AUTH_STATE_PATH`, falling back to the repo-root
>   files. It was written that way for Render Secret Files, not for a pool, and
>   it turned out to be exactly what a pool needs.
> - `AuthKeepAlive` already resolved through that same function, so it followed
>   per-host files with **no change at all**.
> - `persistStorageState`'s write queue is per-path. Once each host has its own
>   file, cross-host write contention does not need solving — it stops
>   existing. The lock this plan worried about was **deleted, not built**.
>
> So the credential half of C4 was config: a directory per host, two env vars,
> and two bind mounts. What genuinely needed building was something the design
> text above never mentions, and it is the whole reason this section is long.
>
> ### The real work: auth health has to gate dispatch
>
> `AuthKeepAlive` has always *detected* an expired session correctly — it loads
> an authenticated page, checks the landing URL against an allowlist, and logs
> a loud ALERT. Until now that was all it did, and on one host that was
> tolerable: a dead session meant "the bot is broken", which was obvious
> because nothing recorded.
>
> **C3 made that worse, not better.** A host with a dead Google session still
> answers `GET /capacity` with free slots. The registry keeps picking it, every
> join fails `AUTH_EXPIRED`, and the healthy host sits idle — a dead credential
> silently becomes a meeting-shredder that looks like a working recorder, and
> the pool hides the symptom that used to make it obvious. So:
>
> - The bot tracks health **per platform** (`AuthHealth.js`) and reports it on
>   `GET /capacity` as `auth: {google: {status, detail, checkedAt}, zoom: …}`.
> - `bot_registry.claim_host(platform)` skips a host whose credential for *that
>   platform* is expired. `bot_dispatch` passes `meeting.platform`, which it
>   already had in hand.
>
> **Per platform, never per host.** Google and Zoom are separate identities
> that expire independently, and collapsing them into one "unhealthy" flag
> would take a working recorder offline over a credential it was not going to
> use. Proven live below: one host was simultaneously refused Google meetings
> and given Zoom ones.
>
> Four things worth knowing:
>
> - **"unknown" counts as usable, and that is a decision, not an oversight.**
>   Between boot and the first keepalive cycle (a 10-second delay plus a headed
>   Chrome page load) nothing has observed the credential. Treating that as
>   expired would make every bot restart a brief pool-wide outage and make a
>   fresh install refuse meetings for its first half-minute — a guaranteed cost,
>   paid every time. Treating it as usable risks one meeting failing with a
>   clear `AUTH_EXPIRED`, which immediately corrects the state (below). A
>   bounded, self-correcting wrong guess beats a certain outage. The same rule
>   makes a bot running pre-C4 code, which sends no `auth` key at all, read as
>   "no opinion, carry on" — so the backend can be deployed ahead of the bots.
> - **A real `AUTH_EXPIRED` join failure marks the platform dead immediately.**
>   The keepalive interval is 15 minutes, so without this a credential that
>   dies one minute after a cycle keeps attracting meetings for the next
>   fourteen and shredding every one. One hook in `MeetingLifecycle`'s existing
>   `catch`. Every *other* join failure — a cancel, a bad URL, an admission
>   timeout, a network error — is deliberately ignored: none of them say
>   anything about the credential, and acting on one would take a working
>   recorder out of the pool for 15 minutes.
> - **A failed *check* is not a verdict.** A page load that threw says nothing
>   about the session, so it leaves the previous status standing and only
>   updates the note. Without that, one network blip during a cycle would
>   downgrade a healthy host and empty the pool.
> - **An unreadable credential file *is* a verdict** — found by running this
>   phase's own gate. A deliberately broken `auth.json` surfaced as
>   `Error reading storage state`, which the rule above correctly treats as
>   inconclusive, leaving the host advertised as usable and still collecting
>   meetings it could never record. A missing or unparseable storage-state file
>   fails every join identically and forever, so the keepalive now checks the
>   file before launching a browser and records that as expired.
>
> ### Two smaller gaps closed
>
> - **The generator scripts ignored the env vars everything else reads.**
>   `generate-auth.cjs` and `generate-zoom-auth.cjs` hardcoded `'auth.json'` /
>   `'zoom-auth.json'` in the working directory, so generating bot-b's
>   credentials meant generating and then remembering to move a file — a step
>   with no error message when you skipped it, which silently left bot-b on
>   bot-a's identity. Both now resolve through `resolveAuthStatePath` (imported,
>   not reimplemented, so they cannot disagree with the runtime) and print the
>   absolute path they wrote.
> - **`.gitignore` covers `meeting-bot/auth/` as a tree**, not two filenames,
>   with `meeting-bot/auth/README.md` as the one tracked file. An `auth.json`
>   has leaked into this repo's history once already (see
>   `docs/aws-ec2-deploy.md`); with N hosts there are 2N live session files plus
>   whatever backups someone makes while rotating one, and a rule listing only
>   the names that existed when it was written would not have covered `bot-c/`
>   or `auth.json.bak`.
>
> ### Gate — results
>
> **Which ran live, and which did not.** Gates 1–5 ran against the compose
> stack. **This repo has no second Google account and its *existing* Google
> session is expired** — confirmed by the keepalive on both hosts, not assumed
> — so two live simultaneous recordings on two real accounts were **not
> tested and remain unproven**. What was tested instead is better suited to the
> failure that matters: a deliberately invalidated credential, contrasted
> against a genuinely live one (Zoom's, which is current).
>
> 1. **Two hosts, two distinct auth files** — live, from the logs. Both
>    containers resolve the *same* container path, by design, so the startup
>    line now carries a size and a short digest (never any content — these are
>    live session cookies):
>
>    ```
>    meeting-bot-1   [startup] auth state google: /app/auth/auth.json (19759 bytes, sha256:b5d3ce9f8648)
>    meeting-bot-1   [startup] auth state zoom:   /app/auth/zoom-auth.json (680655 bytes, sha256:0c8128d9d7a6)
>    meeting-bot-2-1 [startup] auth state google: /app/auth/auth.json (19759 bytes, sha256:b5d3ce9f8648)
>    meeting-bot-2-1 [startup] auth state zoom:   /app/auth/zoom-auth.json (27 bytes,     sha256:dcbfcdab9989)
>    ```
>
>    That line is new and earns its place: "are these two recorders actually on
>    different accounts?" is the question a pool makes people ask, and nothing
>    else would have answered it until two bots started fighting over one login.
>    An earlier run of the same check showed the two hosts' *Zoom* digests
>    diverging on their own after a keepalive cycle — each host rotating its own
>    cookies, independently, which is C4 working.
> 2. **A dead credential takes the host out for that platform only** — live.
>    With bot-b's Zoom session deliberately invalidated and bot-a's genuinely
>    alive, both hosts reporting `available=1`:
>
>    ```
>    bot-a: google=expired  usable=False | zoom=ok       usable=True
>    bot-b: google=expired  usable=False | zoom=expired  usable=False
>    claim_host('zoom')   -> bot-a
>    ```
>
>    A real queued Zoom meeting dispatched through `dispatch_queued_meeting`
>    landed on `bot-a` and was never offered to `bot-b`. bot-a being *unusable
>    for Google and usable for Zoom at the same moment* is the per-platform
>    property, on one host, live. The mirror case — a healthy host still
>    receiving Google while an unhealthy one does not — is **unit-level only**
>    (`test_a_dead_google_session_stops_google_meetings_but_not_zoom`), because
>    no live Google credential exists here to be the healthy side.
> 3. **Every host's Google session dead names the credential** — live, and with
>    genuinely expired real credentials rather than a simulation:
>
>    ```
>    no recorder has a working Google session - bot-a (expired), bot-b (expired).
>    The recorders are running and have capacity; their Google sign-in has expired,
>    so regenerate it with `node generate-auth.cjs`
>    ```
>
>    The meeting stayed `queued` with no host recorded, and on exhaustion the
>    message reached `error_message` intact. The operator fix here is nothing
>    like "add a host", which is why it must not read as a capacity problem.
> 4. **Recovery, no restart** — live. `docker inspect -f '{{.State.StartedAt}}'`
>    was byte-identical before and after: `2026-09-12T09:03:25.580575951Z`.
>    Replacing bot-b's `zoom-auth.json` took it from `expired`/unusable to
>    `ok`/usable within one keepalive interval, with nothing restarted and
>    nothing to clear — the registry holds no per-host state of its own, only
>    what the last poll said. (The interval was shortened to 1 minute for the
>    run via a throwaway compose override, *before* the credential was broken,
>    so the recreate was setup rather than recovery.)
> 5. **A single-host install is untouched** — live, at the resolution layer:
>
>    ```
>    with AUTH_STATE_PATH set:  google -> /app/auth/auth.json
>    with it unset:             google -> /app/auth.json
>    ```
>
>    Plus `test_a_single_host_reporting_nothing_behaves_exactly_as_before`,
>    which dispatches every platform against a bot that reports no health at all.
> 6. **Suites.** Backend **182 passed** (166 before this phase, +16 in the new
>    `tests/test_bot_auth_health.py`); meeting-bot **37 passed** (22 before,
>    +11 in `test/auth.health.test.js` and +4 in `test/auth.path.test.js`).
>
> ### Still open after C4
>
> The first two are carried forward verbatim into [What Phase C leaves open](#what-phase-c-leaves-open) — they are the
> same two items, not additional ones.
>
> - **Two hosts recording simultaneously on two real accounts is unproven.**
>   The mechanism is in place and each host demonstrably loads and rotates its
>   own files; what has not happened is two concurrent real recordings on two
>   separate Google accounts, because the second account does not exist yet and
>   the first one's session is expired. Creating the accounts is the operator
>   work C4's design always assumed.
> - **`meeting-bot/auth/bot-b/` is currently seeded from bot-a's files.** That
>   is a bootstrap convenience for local testing, not the intended end state,
>   and it is exactly the shared-identity problem C4 exists to remove. It is
>   documented in `meeting-bot/auth/README.md`, which says in as many words to
>   sign into a *different* account per host.
> - **Within a host, `MAX_CONCURRENT_MEETINGS > 1` still shares one identity.**
>   Unchanged by C4 and not a blocker — that is what the setting has always
>   meant, and `persistStorageState`'s per-path queue already serialises those
>   writes correctly inside one process.

## C5 — `meetingId` required on `/stop` ✅ done

> **Shipped** in `meeting-bot` only — `server.js` plus a new
> `test/stop.endpoint.test.js`. **No backend change**, which was predicted and
> then checked rather than assumed: `bot_service.stop_bot`
> ([bot_service.py:192](../backend/app/services/bot_service.py#L192)) is the
> only caller of the bot's `/stop` anywhere in the backend, and it has always
> sent `{"meetingId": ...}` unconditionally. (`meetings.py`'s
> `POST /meetings/{id}/stop` is the *backend's* own route, which the frontend
> calls; it is a different endpoint and is untouched.) **Risk:** low — a
> deletion, with no caller of the deleted path. **Revert:** restore the
> fallback block; nothing else moved.
>
> **The handler's executable body went from 22 lines to 13** (28 lines to 27
> including comments — the new version carries a 12-line comment explaining
> what was removed and why the two surviving statuses stay distinct). No
> deprecation window, no warning header, no compatibility flag — there is no
> caller to be compatible with.
>
> ### The hazard was never the missing argument
>
> `meetingId` was optional, resolving to "the only active meeting" when
> exactly one was running. On a single-session bot that was unambiguous. On a
> pooled host with `MAX_CONCURRENT_MEETINGS > 1` — which is what C3 made
> ordinary — it meant the handler pulled an arbitrary entry out of
> `activeMeetings` and cancelled it: **someone else's recording, stopped
> silently, with a `200` reporting success.** Nothing in the response, the
> logs, or the stopped meeting's own failure would have pointed at the caller
> that did it.
>
> ### Three outcomes became two, on purpose
>
> | request | before | after |
> |---|---|---|
> | no `meetingId`, nothing active | 404 "No active meeting to stop" | **400 "meetingId is required"** |
> | no `meetingId`, exactly one active | **200 — cancels it** | **400 "meetingId is required"** |
> | no `meetingId`, several active | 400 "Multiple meetings active" | 400 "meetingId is required" |
> | `meetingId` not active | 404 | 404, unchanged |
> | `meetingId` active | 200, stops it | 200, unchanged |
>
> The collapse is deliberate and so is what survives it. **400 and 404 are
> kept distinct** because they are different operator problems: 400 is a
> malformed caller, 404 is a meeting that has already ended. One status for
> both would turn "your integration is broken" and "you lost a race" into the
> same line in a log. An empty-string `meetingId` is 400 for the same reason —
> falling through to the lookup would answer `No active meeting  to stop`,
> which reads as a race that never happened.
>
> ### Gate — results
>
> Seven tests in `meeting-bot/test/stop.endpoint.test.js`. Staging a *real*
> active session mattered here and took some care: `activeMeetings` is
> module-private with no seam to fake, and only a real `POST /{platform}/join`
> can register one. The join registers the session before answering 202 and
> only then starts the lifecycle, so pointing `AUTH_STATE_PATH` at a path that
> does not exist gives a genuinely-registered session whose lifecycle fails at
> its first step — **no Chromium, and no dependence on whether the developer
> has a valid `auth.json`**. Every case asserts the session is still active
> before relying on it, so a shorter window than the ~1.3s measured would fail
> loudly rather than pass hollow.
>
> 1. **A stop with no `meetingId`, while exactly one meeting is active, is
>    refused** — and the meeting is still running afterwards. **Verified to
>    fail against the pre-C5 code**, which is the point of the gate: with the
>    old `server.js` stashed back in, it fails `200 !== 400` — the old handler
>    cancelled the meeting and reported success. Two more of the seven fail
>    against the old code (the idle 404→400 collapse, and the empty-string
>    case); the other four pass on both, which is the evidence that the
>    unchanged behaviour really is unchanged.
> 2. **A `meetingId` that is not active is still 404** — passes against both
>    old and new, deliberately.
> 3. **A valid `meetingId` stops that meeting and no other** — a bystander
>    session is still registered afterwards *and* still answers 200 to its own
>    stop, which is what shows it was not consumed by the first call.
> 4. **Suites.** meeting-bot **44 passed** (37 before, +7 here). Backend
>    **182 passed**, unchanged — the number is the confirmation that no backend
>    change was needed.
>
> Also checked live against the running compose stack, on the built image
> rather than the source tree: `400 {"error":"meetingId is required"}` with
> nothing active, and `404 {"error":"No active meeting <id> to stop"}` for an
> unknown id.
>
> **One cost worth knowing about.** Each staged session runs its failure path
> to the end, including `notifyBackend`'s three webhook attempts with a
> hardcoded 3s + 6s backoff. The assertions finish in milliseconds; the process
> then waits ~9s for those to unwind. They unwind in parallel, so it is ~9s for
> the file however many meetings it stages — but it makes this the slowest file
> in the suite, and that is why.

---

## C1 and C2 are worth shipping on their own

They fix the worst user-visible failure — "meeting rejected because the bot was
busy" becomes "meeting waits" — **on your existing single host, with no
multi-host work.** C3–C5 are the actual horizontal step and should wait until
concurrent recordings are demonstrably the binding constraint.

**Overridden by the 2026-09-12 decision above:** shipped anyway, ahead of that
signal, because the plan is being finished end-to-end before the first
deploy rather than triggered by production load. The risk this recommendation
was guarding against doesn't disappear — C3-C5 add real complexity (a
registry, per-host credentials) that a single-host deployment doesn't
exercise, so it will be tested against a bot pool of size one until real
multi-host traffic arrives.

## Gate

- ~~A meeting requested while the bot is full is queued and joins when capacity
  frees, rather than failing.~~ **met — C2.**
- ~~With two bot hosts, two simultaneous meetings land one on each.~~ **met —
  C3, gate 1 above.**
- ~~Killing a bot host mid-recording marks its meetings failed within the
  watchdog TTL and does not strand capacity in the registry.~~ **met — C3,
  gates 2 and 3 above.** Capacity is not stranded because the registry holds no
  per-meeting state to strand: a dead host's cache entry goes stale and its
  slots stop being offered, and the reservation counter expires on its own.
- Two hosts recording simultaneously do not corrupt each other's auth state.
  **Met by construction, not yet proven by observation.** Each host reads and
  writes its own `auth.json`/`zoom-auth.json`, so there is no shared file left
  to corrupt: `persistStorageState`'s write queue is per path, and per-host
  files put the two hosts on different paths — the contention this line was
  written to guard against was removed rather than managed, and the two hosts'
  Zoom files were observed diverging on their own as each rotated its own
  cookies. **But two simultaneous real recordings on two real accounts have
  not been run**, because the second account does not exist and
  `meeting-bot/auth/bot-b/` is seeded from bot-a's files. This is the one
  Phase C gate item that closes on the operator, not on the code — see
  "What Phase C leaves open".

---

# What Phase C leaves open

Phase C is done. These are not, and they are listed here rather than left to be
rediscovered — a finished phase is not a finished problem.

**1. "Two hosts recording simultaneously do not corrupt each other's auth
state" is still unproven.** C4's mechanism is in place and each host
demonstrably loads and rotates its own credential files — the two hosts' Zoom
digests were observed diverging on their own. What has *not* happened is two
concurrent real recordings on two separate Google accounts, because
**`meeting-bot/auth/bot-b/` is currently seeded from bot-a's files and the
second Google/Zoom account does not exist yet**. That is the operator work C4's
design always assumed, and Phase C closing does not close it. Until those
accounts exist, the pool is two hosts sharing one identity — which is exactly
the state C4 was built to end.

**2. The dev `docker-compose.yml` has no `restart:` policy on any service.**
Found during C3's gate 6: restarting Redis killed the arq worker inside arq's
own shutdown path, and it stayed down — taking the heartbeat loop with it, so
the pool's health cache went cold and stayed cold. Pre-existing, and A3 and C2
have the same exposure; C3 and C4 only raised the stakes by putting the poller
there too. **`docker-compose.prod.yml` already sets `restart: unless-stopped`
on every service**, so production self-heals and this is a dev-only gap.
Mirroring the prod policies into the dev file is a change to all five services
rather than one, which is why it was not done in passing. **Queued as the next
thing to fix**, not unknown.

---

## Dependencies between phases

```
A1 (scheduler claim) ──── independent, do first
   └──> A2 is safe only because A1 landed

A2 (config)  ──────────── no code

A3 (queue + worker) ───┬── introduces Redis
                       ├──> B1 rate limiting reuses it (shipped)
                       └──> C2 dispatch queue reuses it

A4 (Sentry) ───────────── independent
   └──> pulled forward specifically to de-risk A5

A5 (local JWT) ────────── independent of A1–A3; wants A4 first

C  ────────────────────── needs A3's broker for C2
```

A3 is the hinge: it introduces the broker that B1 and C2 both build on.

## New configuration this plan introduces

| Var | Phase | Notes |
|---|---|---|
| `WATCHDOG_ENABLED`, `CALENDAR_SCHEDULER_ENABLED` | A2 | Already exist — used as a deployment invariant. |
| `REDIS_URL` | A3 | Broker. Reused by C2/C3 and, since B1, by rate limiting on the request path. |
| `TRANSCRIPTION_WORKER_CONCURRENCY` | A3 | Replaces the hardcoded `max_workers=4`. |
| `TRANSCRIPTION_USE_QUEUE` | A3 | The two-deploy cutover flag. |
| `SENTRY_DSN` | A4 | |
| `RATE_LIMIT_ENABLED` | B1 | The revert. False is a true no-op - no connection is opened at all. |
| `CHAT_RATE_LIMIT_REQUESTS`, `CHAT_RATE_LIMIT_WINDOW_SECONDS` | B1 | 30 per 5 min, per user, **shared by `/chat` and `/chat/stream`**. Sized so a person reading the answers cannot reach it; ceilings one user near $2/hour of Gemini. |
| `MEETING_CREATE_RATE_LIMIT_REQUESTS`, `MEETING_CREATE_RATE_LIMIT_WINDOW_SECONDS` | B1 | 10 per 5 min. Protects the bot pool, not a model bill. Calendar-scheduled meetings bypass this route entirely. |
| `RATE_LIMIT_REDIS_TIMEOUT_SECONDS` | B1 | 0.5, connect *and* read. Because the limiter fails open, this is the latency added per request during a Redis outage. |
| `BOT_DISPATCH_USE_QUEUE` | C2 | The cutover flag. Reuses A3's `REDIS_URL` and worker. |
| `BOT_DISPATCH_MAX_ATTEMPTS`, `BOT_DISPATCH_RETRY_DELAY_SECONDS` | C2 | How long a meeting may wait (40 x 30s = 20 min) before it is failed with a specific message. |
| `WATCHDOG_QUEUED_TTL_MINUTES` | C2 | 30. Must stay above the product of the two above. |
| `TEST_DATABASE_URL` | A1 | Already in use — `backend/tests/conftest.py` refuses to run without it. Test-only, never set in production. |
| `SUPABASE_JWKS_URL` / `SUPABASE_JWT_SECRET` | A5 | Which one depends on your project's signing scheme. |
| `SUPABASE_JWT_AUDIENCE`, `SUPABASE_JWT_ISSUER` | A5 | Must be verified, not just decoded. |
| ~~`BOT_HOST_ID`, `BOT_REGISTRY_URL`~~ | ~~C~~ | **Not built.** Both assumed bots register themselves. They do not — see C3's first decision. |
| `BOT_HOSTS` | C3 | The pool, as `id=url` pairs (or bare URLs). Empty = a pool of one from `MEETING_BOT_URL`, which is the C3 revert. Read by the backend (to route stop/delete/re-upload) and the worker (to poll and dispatch), so both compose services set it. |
| `BOT_HEARTBEAT_ENABLED`, `BOT_HEARTBEAT_INTERVAL_SECONDS`, `BOT_HEARTBEAT_TTL_SECONDS` | C3 | 5s polls, dead after 20s. The gap is the tolerance: a host misses three consecutive polls before it stops receiving meetings, so one slow answer or a container restart does not take it out of the pool. |
| `BOT_HOST_RESERVATION_SECONDS` | C3 | 20. How long a dispatcher's slot reservation on a host survives. Never a lock — the bot's 409 is still the authority. |
| `AUTH_STATE_PATH`, `ZOOM_AUTH_STATE_PATH` | C4 | Per-host credential files, set on each `meeting-bot` container. **Not new** — they predate this plan (added for Render Secret Files); C4 is what finally points them at a directory per host. Unset = the repo-root files, which is the C4 revert and what every single-host install does. |
| `AUTH_KEEPALIVE_INTERVAL_MINUTES` | C4 | Also pre-existing. 15 by default, and it now sets how long a dead credential can keep attracting meetings in the worst case — though a real `AUTH_EXPIRED` join failure corrects the state immediately, so the interval is the ceiling, not the typical latency. |

## Open questions to settle before starting

1. **Which Supabase JWT signing scheme is this project on?** Determines A5's
   implementation entirely. Worth answering now even though A5 is last.
2. ~~`arq` or Celery for A3?~~ **Settled: `arq`.** See Phase A3's Design.
3. ~~Per-host bot auth (C4): credential pool, or shared state with locking?~~
   **Settled: credential pool — and shipped.** See the resolved note above C3's
   Design, and C4's own section for how much less this turned out to be than
   the plan expected.
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
| 5 — tests | **B2/B3** | deferred, minus the per-phase gate tests every phase since A1 has carried |
| 6 — observability + rate limiting | **A4** (Sentry) + **B1** (rate limiting, done) + **B4/B5** (rest) | Sentry pulled forward to de-risk A5; rate limiting pulled out of B because it was the only item in that bucket blocking a deploy |
| 7 — bot pool | **C** | unchanged |
