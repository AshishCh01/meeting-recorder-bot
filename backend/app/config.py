from pydantic_settings import BaseSettings


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

    # Fallback STT provider - used if Gemini keeps failing (429/503)
    sarvam_api_key: str = ""
    sarvam_stt_model: str = "saaras:v3"
    sarvam_chat_model: str = "sarvam-105b"
    sarvam_language_code: str = "en-IN"
    sarvam_num_speakers: int | None = None  # None = let Sarvam auto-detect speaker count

    jina_api_key: str = ""
    jina_embedding_model: str = "jina-embeddings-v3"    #Fallback embedding model

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

    # Cost tracking: per-million-token USD rates for the [cost] log lines
    # in transcription_service.py, chat_service.py, and embedding_service.py.
    # Update these via env vars when Gemini's pricing changes - never hardcode
    # a rate at a call site.
    gemini_input_cost_per_mtok: float = 0.30
    gemini_output_cost_per_mtok: float = 2.50
    gemini_embedding_cost_per_mtok: float = 0.15

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

