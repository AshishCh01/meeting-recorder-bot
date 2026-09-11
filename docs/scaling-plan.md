# Scaling & Production-Readiness Plan

Date: 2026-09-08 (restructured 2026-09-09 around execution phases)
Status: **A1** (`8dc0844`), **A3** (`d3b424d`), **A4** (`69e595a`) and **A5**
(`588ceb9`) shipped - Phase A is functionally complete except A2, which is
blocked on infrastructure, not on code. A5 was the highest-risk phase in this
document and was verified against a real token, real forged tokens, and a
real host-side benchmark. **Phase C is underway: C1 (`GET /capacity`) and C2
(dispatch behind a queue) have shipped, so a meeting requested while the
recorder is busy now waits instead of being lost. C3-C5 are the multi-host
step and should wait until concurrent recordings are demonstrably the binding
constraint** (Phase B stays deferred by decision). One thing A5's own benchmark surfaced and left open: a Supabase
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
| **B** | Tests, structured logging, rate limiting | — | Deferred by decision |
| **C** | Bot pool — the recording tier — **C1, C2 done** | High | Per sub-step |

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

Deliberately *not* done here, both moved to Phase B: converting the 45 `print()`
calls in `backend/app` to `logger.*`, and turning `log_cost` into a real metric.
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
- **The chat routes hold a connection for a whole LLM turn.** `POST /chat` and
  `/chat/stream` query through the request's session (`_assert_chattable`) and
  never commit, so SQLAlchemy keeps that connection checked out while the answer
  is generated, and the tools take a second one alongside it. Eight
  simultaneous chats would occupy the backend's entire pool for the length of a
  Gemini call. The fix is to release the session before awaiting the model, in
  `app/api/chat.py`, which was outside this change.

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
# Phase B — Tests, logging, rate limiting (deferred by decision)

## Tests

**A suite now exists** — 40 tests in `backend/tests/`, built as the gates of the
phases that needed them rather than as a phase of its own:

| File | Tests | Landed with |
|---|---|---|
| `test_scheduler_claim.py` | 16 | A1 |
| `test_transcription_queue.py` | 15 | A3 |
| `test_observability.py` | 9 | A4 |

That was the deliberate consequence of deferring this phase: A1 and A3 carry
their own tests because a concurrency fix is unobservable without one, and A3
could silently leave a `completed` meeting with nothing to search. Each was
*falsified* — the fix reverted, the test shown failing with the real symptom.

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

(A1's scheduler race is already pinned by `test_scheduler_claim.py` and is no
longer on this list.)

`pytest` is in `requirements-dev.txt`. `pytest-asyncio` is **not** needed - the
async paths are driven with `asyncio.run(...)` directly, which keeps the async
boundary explicit in each test. **CI is the real gap**: nothing runs these
automatically, so they only protect you when someone remembers. A workflow with
a `pgvector/pgvector:pg18` service (SQLite will not do - pgvector, `ARRAY`, RLS)
is the highest-value item in this phase.

## Remaining observability

A4 shipped Sentry, root logging config for both entrypoints, and `meeting_id` /
`user_id` on transcription events. Two pieces were deliberately left here:

1. **Convert the 45 `print()` calls in `backend/app` to `logger.*`**, with
   `meeting_id` and `user_id` as structured fields rather than interpolated
   into the message. This was originally listed under A4 and moved here on the
   A4 deploy: the calls already emit (`PYTHONUNBUFFERED` is set in both
   Dockerfiles) and are already visible in `docker compose logs`, so the
   rewrite buys log *structure*, not log *visibility* — and it is a diff across
   every file in `app/services/`, which is the opposite of what a phase whose
   whole purpose is de-risking the A5 deploy wants to ship. A4 left root
   logging on stdout so the two styles interleave in order in the meantime.
2. **Turn `log_cost` into a real metric.** Per-user AI spend is a business
   number, not a debug line.

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
>   failure — "Waited 20 minutes for a free recorder and never got one - the
>   recorder was busy (1/1 in use)". The watchdog's 30-minute `queued` TTL
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
| `BOT_DISPATCH_USE_QUEUE` | C2 | The cutover flag. Reuses A3's `REDIS_URL` and worker. |
| `BOT_DISPATCH_MAX_ATTEMPTS`, `BOT_DISPATCH_RETRY_DELAY_SECONDS` | C2 | How long a meeting may wait (40 x 30s = 20 min) before it is failed with a specific message. |
| `WATCHDOG_QUEUED_TTL_MINUTES` | C2 | 30. Must stay above the product of the two above. |
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
