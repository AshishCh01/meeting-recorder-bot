from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_key: str
    supabase_recordings_bucket: str = "recordings"

    meeting_bot_url: str = "http://localhost:3000"
    meeting_bot_bearer_token: str

    frontend_origin: str = "http://localhost:5173"

    gemini_api_key: str
    gemini_model: str = "gemini-3.7-flash"

    # RAG: embeddings + retrieval agent
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 1536
    rag_agent_model: str = "gemini-3.7-flash"
    chunk_segments: int = 6          # conversation segments grouped per chunk
    chunk_overlap: int = 1           # segments of overlap between consecutive chunks
    retrieval_top_k: int = 6

    class Config:
        env_file = ".env"


settings = Settings()