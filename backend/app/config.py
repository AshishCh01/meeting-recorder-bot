from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_key: str
    supabase_recordings_bucket: str = "recordings"

    meeting_bot_url: str = "http://localhost:3000"
    meeting_bot_bearer_token: str

    frontend_origin: str = "http://localhost:5173"

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

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

