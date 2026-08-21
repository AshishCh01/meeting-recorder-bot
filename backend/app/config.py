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
    chunk_segments: int = 6          # conversation segments grouped per chunk
    chunk_overlap: int = 1           # segments of overlap between consecutive chunks
    retrieval_top_k: int = 6

    # Fallback STT provider - used if Gemini keeps failing (429/503)
    sarvam_api_key: str = ""
    sarvam_stt_model: str = "saaras:v3"
    sarvam_chat_model: str = "sarvam-105b"
    sarvam_language_code: str = "en-IN"
    sarvam_num_speakers: int | None = None  # None = let Sarvam auto-detect speaker count

    jina_api_key: str = ""
    jina_embedding_model: str = "jina-embeddings-v3"    #Fallback embedding model

    # Watchdog: fails meetings stuck in a non-terminal status past these
    # TTLs (docs/reliability-audit.md, finding #1). "recording" has no
    # TTL of its own - it's max_recording_duration_minutes (the same
    # hard cap meeting-bot enforces, MAX_RECORDING_DURATION_MINUTES)
    # plus watchdog_recording_margin_minutes, so it never fires before
    # meeting-bot's own cap could have legitimately ended the meeting.
    watchdog_enabled: bool = True
    watchdog_sweep_interval_minutes: int = 5
    watchdog_joining_ttl_minutes: int = 10
    max_recording_duration_minutes: int = 90
    watchdog_recording_margin_minutes: int = 15
    watchdog_uploading_ttl_minutes: int = 20
    watchdog_transcribing_ttl_minutes: int = 30

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

