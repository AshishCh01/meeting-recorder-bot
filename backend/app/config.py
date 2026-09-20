import os

from pydantic_settings import BaseSettings

# Opt-out for the test suite: tests/conftest.py sets IGNORE_DOTENV=1 before any
# app.* import, so settings come from code defaults plus explicitly set env vars
# and never from backend/.env. A .env reaches the process two ways, and both
# have to honour this: Settings' env_file below, and load_dotenv() in
# app/db/database.py. Unset everywhere else, so real runs are unchanged.
IGNORE_DOTENV = os.environ.get("IGNORE_DOTENV") == "1"


class Settings(BaseSettings):
    supabase_url: str
    supabase_key: str
    supabase_recordings_bucket: str = "recordings"

    meeting_bot_url: str = "http://localhost:3000"
    meeting_bot_bearer_token: str

    frontend_origin: str = "http://localhost:5173"

    # "development" additionally allows the hardcoded localhost CORS
    # origins in main.py; any other value (default: production) does not.
    environment: str = "production"

    gemini_api_key: str
    gemini_model: str = "gemini-3.6-flash"

    # RAG: embeddings + retrieval agent
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    rag_agent_model: str = "gemini-3.6-flash"
    # Transcript chunking, in approximate tokens (embedding_service estimates
    # ~4 chars per token). These replaced an earlier segment-count pair
    # (chunk_segments/chunk_overlap) that described an approach the chunker no
    # longer uses and that nothing read - the defaults below are the values
    # _build_chunks previously hardcoded, so changing nothing here preserves
    # existing behaviour. Changing them only affects meetings indexed
    # afterwards; existing chunks keep whatever sizing they were built with
    # until that meeting is re-indexed.
    chunk_target_tokens: int = 500
    chunk_overlap_tokens: int = 50
    retrieval_top_k: int = 6

    # Ask AI tools (app/ask_ai/tools/). list_meetings picks how much detail to
    # return per meeting from how many match: up to ask_ai_detailed_limit
    # get a summary each, up to ask_ai_compact_limit get their first key
    # points, up to ask_ai_titles_limit get a title and date, and beyond that
    # the most recent ask_ai_titles_limit are returned, marked truncated. So
    # "what was discussed in August?" covers every meeting in the month
    # rather than an arbitrary first 20.
    ask_ai_detailed_limit: int = 20
    ask_ai_compact_limit: int = 100
    ask_ai_titles_limit: int = 300
    # search_across_meetings with no date range searches only the user's most
    # recent this-many meetings.
    ask_ai_search_candidate_meetings: int = 200
    ask_ai_search_top_k: int = 10
    ask_ai_topic_top_k: int = 10
    # Ask AI agent (app/ask_ai/agent/). More tool iterations than the
    # per-meeting chat's 6: a date question is typically a list_meetings call
    # followed by a details call per meeting it picks out. The history limit
    # plays the same role as chat_service.HISTORY_TURN_LIMIT.
    ask_ai_max_tool_iterations: int = 8
    ask_ai_history_turn_limit: int = 20

    # Fallback STT provider - used if Gemini keeps failing (429/503)
    sarvam_api_key: str = ""
    sarvam_stt_model: str = "saaras:v3"
    sarvam_chat_model: str = "sarvam-105b"
    sarvam_language_code: str = "en-IN"
    sarvam_num_speakers: int | None = None  # None = let Sarvam auto-detect speaker count

    jina_api_key: str = ""
    jina_embedding_model: str = "jina-embeddings-v3"    #Fallback embedding model

    # Fallback chat provider - used by rag/chat_fallback_groq.py when Gemini
    # keeps failing (429/503/504) during "Ask about this meeting" after
    # chat_service's own retries are exhausted. Leave groq_api_key empty to
    # disable the fallback entirely (the canned "try again later" message
    # comes back). The model must support tool calling - the chat agent is a
    # tool-calling loop, not plain generation.
    groq_api_key: str = ""
    groq_chat_model: str = "openai/gpt-oss-120b"
    groq_max_output_tokens: int = 2000
    groq_timeout_seconds: float = 60.0

    # How long a single Gemini chat turn may stall, and how many times
    # chat_service retries it before handing the question to Groq.
    #
    # These bound how long a user waits on a bad Gemini day, because the
    # timeout is spent in full on every attempt: the previous 60s x 3 meant
    # roughly three minutes of an empty chat bubble before the fallback even
    # started. As a *read* timeout it only trips on that many seconds of
    # silence, and a healthy stream sends tokens continuously, so 30s is still
    # far more than a live turn ever needs - it just declares a dead stream
    # dead sooner. Two attempts then reach Groq (which is typically faster
    # than Gemini anyway) in about a minute instead of three.
    chat_timeout_seconds: float = 30.0
    chat_max_retries: int = 2

    # Google Calendar integration (optional - the app runs fine with
    # these unset, the /calendar/* routes just fail with a clear error
    # until they're configured). See docs/google-calendar-integration-plan.md.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:8000/calendar/oauth/callback"
    # Fernet key encrypting stored Calendar refresh tokens at rest -
    # generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    google_token_encryption_key: str = ""
    calendar_lookahead_days: int = 7

    # Scheduler: picks up meetings whose scheduled_at (set via the
    # calendar "record this event" flow) has arrived and triggers the
    # bot join, same as the immediate path in POST /meetings.
    calendar_scheduler_enabled: bool = True
    scheduler_sweep_interval_minutes: int = 1
    calendar_join_lead_minutes: int = 2

    # Watchdog: fails meetings stuck in a non-terminal status past these
    # TTLs (docs/reliability-audit.md, finding #1). "recording" has no
    # TTL of its own - it's max_recording_duration_minutes (the same
    # hard cap meeting-bot enforces, MAX_RECORDING_DURATION_MINUTES)
    # plus watchdog_recording_margin_minutes, so it never fires before
    # meeting-bot's own cap could have legitimately ended the meeting.
    watchdog_enabled: bool = True
    watchdog_sweep_interval_minutes: int = 5
    watchdog_joining_ttl_minutes: int = 10
    # Safety net only - meeting-bot's own waitForAdmission() times out at 5
    # minutes and reports failure itself. This exists in case the bot
    # process dies before it can report that.
    watchdog_admission_ttl_minutes: int = 10
    max_recording_duration_minutes: int = 90
    watchdog_recording_margin_minutes: int = 15
    watchdog_uploading_ttl_minutes: int = 20
    watchdog_transcribing_ttl_minutes: int = 30
    # "queued" (Phase C2) is the one non-terminal status where waiting is
    # legitimate rather than a symptom - the meeting is holding for a free
    # recorder. It still needs a bound: a meeting queued for hours is a
    # meeting nobody is joining.
    #
    # This MUST stay larger than the dispatcher's own cap
    # (bot_dispatch_max_attempts x bot_dispatch_retry_delay_seconds, 20
    # minutes by default). If the watchdog fired first, the user would get
    # "Timed out while 'queued' - swept by watchdog" instead of the
    # dispatcher's specific "no recorder ever became free", which is the
    # difference between a diagnosable failure and a generic one.
    watchdog_queued_ttl_minutes: int = 30

    # Transcription queue (Phase A3). Redis is the arq broker; the worker
    # runs `arq app.worker.WorkerSettings` from the same image as the API.
    redis_url: str = "redis://localhost:6379"
    # The cutover flag. False keeps submit_transcription on the in-process
    # ThreadPoolExecutor, which is what deploy 1 ships: Redis, the worker and
    # the task definition all running, with nothing behaviourally different.
    # Flipping this to true is deploy 2, and reverting it is the rollback.
    transcription_use_queue: bool = False
    # How many attempts arq gives a transcription job before the worker
    # writes a terminal state and stops retrying. The resume check at the top
    # of transcribe_recording makes attempts after the first cheap - they skip
    # straight to indexing rather than re-billing a full Gemini transcription.
    transcription_max_tries: int = 3
    # How long arq waits before the next attempt of a failed transcription
    # job. Mostly a transient AI provider or embedding outage, so a short wait
    # beats an immediate re-run that hits the same outage.
    transcription_retry_delay_seconds: int = 30
    # Generous: a long recording is a download, a Gemini File API upload, a
    # poll to ACTIVE (capped at 5 minutes on its own), transcription and a
    # full embed. Must exceed the worst realistic case, or arq kills a job
    # that was going to succeed.
    transcription_job_timeout_seconds: int = 1800
    # Concurrent jobs per worker container. Matches the max_workers=4 the
    # ThreadPoolExecutor used, so deploy 2 doesn't change AI provider load.
    worker_max_jobs: int = 4

    # Database connection pool, per process (app/db/database.py).
    #
    # The ceiling is not ours to set: DATABASE_URL goes through Supabase's
    # session-mode pooler (port 5432), which allows this project 15 client
    # connections in total, and rejects the 16th with EMAXCONNSESSION. Every
    # process that imports database.py builds its own engine, so 15 is split
    # across processes:
    #
    #   backend   8  request handlers, plus the watchdog and scheduler sweeps
    #   worker    4  worker_max_jobs = 4, and each job holds one session at a time
    #   headroom  3  `alembic upgrade head` at container boot, the Supabase SQL
    #                editor, any ad-hoc psql
    #   total    15
    #
    # The defaults here are the backend's. env_file is shared by both
    # services, so the worker gets its share from an `environment:` override
    # on the worker service in both compose files - the only per-process
    # lever there is. Overflow is 0 for both: an overflow connection still
    # takes a pooler slot, so it would only hide where the real cap is.
    #
    # Anything that adds a process breaks this arithmetic. `--scale worker=2`
    # is 8 + 4 + 4 = 16; a second backend replica (Phase A2) has to split the
    # backend's 8. With TRANSCRIPTION_USE_QUEUE=false, transcription runs in
    # the backend's own 4-thread executor and spends up to 4 of its 8.
    db_pool_size: int = 8
    db_max_overflow: int = 0
    # How long a request waits for a free pooled connection before giving up,
    # which main.py turns into a 503. SQLAlchemy's default is 30s, and that
    # default is the trap: once the pool fits under the pooler's cap, Postgres
    # stops rejecting excess demand and SQLAlchemy queues it instead, so a busy
    # moment becomes a 30-second spinner followed by an error. A healthy
    # checkout waits milliseconds - 3s of waiting is saturation, not jitter.
    # It does not include connect time, only the wait for a free slot.
    db_pool_timeout_seconds: float = 3.0

    # Bot dispatch queue (Phase C2). Reuses A3's Redis and the same worker
    # container - no new broker, no new process, no second engine.
    #
    # The cutover flag, same two-deploy pattern as TRANSCRIPTION_USE_QUEUE
    # and JWT_LOCAL_VERIFICATION_ENABLED. False is deploy 1: the task, the
    # "queued" status and the dispatcher all shipped and inert, with
    # trigger_bot_join still posting synchronously and POST /meetings still
    # returning 502 when the bot is unreachable. True is deploy 2, and
    # flipping it back is the rollback.
    bot_dispatch_use_queue: bool = False
    # How many times a dispatch job re-checks for a free recorder before it
    # gives up and fails the meeting. Deferring forever would turn "meeting
    # lost" into "meeting pending forever", which is worse: nothing surfaces
    # it. 40 x 30s = 20 minutes of waiting, then a terminal failure that
    # names the real cause.
    bot_dispatch_max_attempts: int = 40
    bot_dispatch_retry_delay_seconds: int = 30
    # Short: GET /capacity is a cheap in-memory read on the bot, and a slow
    # one is itself a reason to wait rather than dispatch. Separate from the
    # 10s join timeout, which covers a real browser launch.
    bot_capacity_timeout_seconds: float = 5.0

    # The bot pool (Phase C3). A comma-separated, operator-maintained list of
    # recorders, each entry either "id=url" or a bare "url" whose id is
    # derived from host:port:
    #
    #   BOT_HOSTS=bot-a=http://meeting-bot:3000,bot-b=http://meeting-bot-2:3000
    #
    # Deliberately not self-registration, which is what the plan's text
    # originally described. C4 provisions a Google/Zoom account and a machine
    # by hand for every host, so the list is already human-maintained - and
    # pulling it from config means meeting-bot needs no new code, no REDIS_URL
    # and no new outbound credential to join a pool. See bot_registry.py.
    #
    # Empty means a pool of one built from MEETING_BOT_URL, which is exactly
    # the pre-C3 deployment. Setting nothing here is the revert.
    bot_hosts: str = ""
    # How often the worker's heartbeat loop asks every host GET /capacity, and
    # how stale a host's last answer may be before the dispatcher treats it as
    # dead. The gap between them is the tolerance: at 5 and 20, a host has to
    # miss three consecutive polls before it stops receiving meetings, so a
    # single slow answer or a container restart does not take it out of the
    # pool. Raising the interval reduces polling traffic and widens the window
    # in which the cache disagrees with reality; the bot's 409 covers that
    # disagreement either way.
    bot_heartbeat_enabled: bool = True
    bot_heartbeat_interval_seconds: float = 5.0
    bot_heartbeat_ttl_seconds: float = 20.0
    # How long a dispatcher's slot reservation on a host survives. It exists
    # only to stop two simultaneous dispatches piling onto the same host while
    # another sits idle - a bot registers a session before it answers 202, so
    # one poll interval later the real /capacity already reflects the join and
    # the reservation is redundant. A few multiples of the interval, so a
    # reservation leaked by a crash between the claim and the post clears on
    # its own. Never a lock: see bot_registry.claim_host.
    bot_host_reservation_seconds: float = 20.0

    # Per-user rate limiting (Phase B1). Backed by A3's Redis, keyed on the
    # user id get_current_user resolves - never on IP, which for this app is
    # a shared NAT or a corporate egress as often as it is a person.
    #
    # The master switch. False makes the dependency a no-op that never opens a
    # connection, which is both the revert and what the tests that are not
    # about rate limiting rely on.
    rate_limit_enabled: bool = True

    # Chat: POST /meetings/{id}/chat and /chat/stream share this one budget.
    # They are the same operation with different transports, and two counters
    # would just mean a caller alternating between them gets double.
    #
    # 30 per 5 minutes is one question every 10 seconds, sustained. A chat
    # turn is a retrieval pass plus a streamed answer - chat_timeout_seconds
    # is 30 on its own - so a person cannot read 30 answers in 5 minutes, let
    # alone ask 30 considered questions. The cap is per user across every
    # meeting they own, because the bill is per user, not per meeting.
    #
    # The cost this bounds: a chat turn is roughly 10k input + 500 output
    # tokens against gemini_input/output_cost_per_mtok (0.30 / 2.50), about
    # $0.004. 30 per 5 minutes is a ceiling near $2/hour for one user running
    # flat out - a number you would notice on a bill but not one that empties
    # an account overnight. Unlimited is the only genuinely dangerous value.
    chat_rate_limit_requests: int = 30
    chat_rate_limit_window_seconds: int = 300

    # Ask AI: its own budget, and a tighter one than chat's - a question
    # across every meeting typically makes several tool calls (a list, then
    # details or a search) and replays a longer prompt each time, so it costs
    # a few chat turns. 20 per 10 minutes is still more than a person asks.
    ask_ai_rate_limit_requests: int = 20
    ask_ai_rate_limit_window_seconds: int = 600

    # Meeting creation: cheaper per call than chat, but each one dispatches a
    # recorder - a headful Chrome plus ffmpeg at roughly 2GB - so the resource
    # it protects is the bot pool, not an API bill.
    #
    # 10 per 5 minutes covers pasting several links in a row and every retry a
    # frustrated user makes. It is not the path calendar sync uses: scheduled
    # meetings are dispatched by the scheduler sweep, which never comes
    # through this route and so is never limited by it.
    meeting_create_rate_limit_requests: int = 10
    meeting_create_rate_limit_window_seconds: int = 300

    # How long the limiter waits on Redis before giving up on it and letting
    # the request through (see rate_limit.py on failing open). Short on
    # purpose: this runs on the hot path of every chat, and a limiter that
    # adds seconds of latency to protect against a cost that is already
    # bounded by the cap itself has made the wrong trade. Both halves are set
    # - a refused connection is instant, a black-holed one is not.
    rate_limit_redis_timeout_seconds: float = 0.5

    # Local JWT verification (Phase A5). This project signs access tokens
    # asymmetrically with ES256; the public keys live at the JWKS URL below
    # and nothing secret is stored here.
    #
    # The kill switch. False restores the pre-A5 behaviour exactly -
    # supabase.auth.get_user() on every request - with no rebuild and no code
    # change, which is the fastest possible revert for the riskiest phase in
    # the plan. Same role TRANSCRIPTION_USE_QUEUE plays for A3.
    jwt_local_verification_enabled: bool = True
    # The safety net under the kill switch: on ANY local verification failure,
    # ask Supabase before rejecting. Every fire is counted, logged and sent to
    # Sentry. Turning this off is the second, separate change - do it only
    # once that counter has sat at zero under real traffic.
    auth_fallback_enabled: bool = True
    # At most one Sentry event per reason per this many seconds. The running
    # total rides along in every event, so a fallback firing on 100% of
    # requests is still visible without sending 100% of requests to Sentry.
    auth_fallback_sentry_interval_seconds: float = 60.0

    # Blank means derive from supabase_url, which is correct for a normal
    # Supabase project. Both are overridable because a wrong value here fails
    # for every user at once, so it must be fixable by env var alone.
    supabase_jwks_url: str = ""
    jwt_issuer: str = ""
    # Read off a real access token, not assumed - see docs/scaling-plan.md A5.
    jwt_audience: str = "authenticated"
    # An allow-list, and the whole defence against `alg: none` and the
    # HS256-confusion trick. Never widen this to include an HMAC algorithm:
    # the JWKS public key would become a valid shared secret.
    jwt_algorithms: str = "ES256"
    # Clock skew tolerance on exp/iat. Small on purpose - it directly extends
    # how long an expired token keeps working.
    jwt_leeway_seconds: int = 10

    # How long a fetched JWKS is reused. Long, deliberately: an unknown `kid`
    # forces a refetch immediately, so a key rotation is picked up by the
    # first request that sees the new key rather than by this expiring.
    jwks_cache_seconds: int = 3600
    jwks_fetch_timeout_seconds: float = 5.0
    # Floor on how often an unknown `kid` may force a JWKS refetch. Without
    # it, PyJWKClient refetches on every miss - one outbound request to
    # Supabase per junk token.
    jwks_unknown_kid_cooldown_seconds: int = 60

    # "user id X has a row in users" - a TTL cache in front of the
    # SELECT-then-maybe-INSERT that get_current_user does on every request.
    # Once verification stops going over the network that query is the
    # dominant per-request cost, and skipping this makes A5's win much
    # smaller than expected. A stale positive is harmless: the worst case is
    # one redundant INSERT the primary key rejects.
    user_cache_ttl_seconds: int = 300
    user_cache_max_entries: int = 10000

    # Observability (Phase A4). Optional, like the fallback provider keys
    # above: an empty SENTRY_DSN means Sentry is never initialised and both
    # the API and the worker start exactly as they did before. Local dev and
    # the test suite need no DSN.
    sentry_dsn: str = ""
    # Errors only by default. Performance tracing is a separate (and billable)
    # thing to turn on deliberately; A4 exists to see error *rates*, not spans.
    sentry_traces_sample_rate: float = 0.0

    # Root log level for both entrypoints. app/observability.py configures the
    # root logger from this - without it neither uvicorn nor arq gives root a
    # handler and every logger.info() in app.* is silently dropped.
    log_level: str = "INFO"

    # Cost tracking (Phase B4): USD rates applied when an ai_usage_events row
    # is written - see app/services/cost_tracker.py. Update these via env vars
    # when a provider's pricing changes - never hardcode a rate at a call site.
    gemini_input_cost_per_mtok: float = 0.30
    gemini_output_cost_per_mtok: float = 2.50
    gemini_embedding_cost_per_mtok: float = 0.15
    # The fallback providers. 0 until real rates are set, which is safe
    # because every row also stores the raw units (tokens, audio seconds):
    # a spend query can be re-run against corrected rates at any time.
    groq_input_cost_per_mtok: float = 0.0
    groq_output_cost_per_mtok: float = 0.0
    jina_embedding_cost_per_mtok: float = 0.0
    sarvam_cost_per_audio_hour: float = 0.0

    # Razorpay (billing). Documented in .env.example, but until now there were
    # no fields here to receive them - Settings has extra = "ignore", so the
    # values in the environment were being silently dropped.
    #
    # All three are blank by default and billing_enabled is False when the
    # pair is missing: the app must start and run without them, exactly as it
    # does today, rather than failing at import because a demo feature is
    # unconfigured.
    #
    # The key id is public (it is handed to Checkout in the browser). The
    # secret and the webhook secret are not, and must never be returned by an
    # API or reach the frontend bundle.
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    @property
    def billing_enabled(self) -> bool:
        """
        True when a usable Razorpay key pair is configured. Both halves are
        required - an id without a secret can open Checkout but cannot verify
        what comes back, which is worse than being switched off.
        """
        return bool(self.razorpay_key_id and self.razorpay_key_secret)

    @property
    def razorpay_is_test_mode(self) -> bool:
        """
        Whether the configured key is a test key. Read off the id's own
        prefix, which Razorpay guarantees, rather than a separate flag
        somebody could set to disagree with the key actually in use. The UI
        uses this to label the demo as a demo.
        """
        return self.razorpay_key_id.startswith("rzp_test_")

    class Config:
        env_file = None if IGNORE_DOTENV else ".env"
        extra = "ignore"


settings = Settings()

