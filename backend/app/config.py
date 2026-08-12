from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_key: str
    supabase_recordings_bucket: str = "recordings"

    meeting_bot_url: str = "http://localhost:3000"
    meeting_bot_bearer_token: str

    frontend_origin: str = "http://localhost:5173"

    class Config:
        env_file = ".env"


settings = Settings()
