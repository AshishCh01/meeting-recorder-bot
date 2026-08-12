from app.db.supabase import supabase
from app.config import settings


def get_signed_recording_url(storage_path: str, expires_in: int = 3600) -> str:
    """
    storage_path is the path inside the bucket, e.g. 'meeting-abc123/recording.mp4'
    (this is what the bot writes when it uploads via the S3-compatible endpoint).
    """
    result = supabase.storage.from_(settings.supabase_recordings_bucket).create_signed_url(
        storage_path, expires_in
    )
    return result["signedURL"]
