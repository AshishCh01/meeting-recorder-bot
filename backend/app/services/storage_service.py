from app.db.supabase import supabase
from app.config import settings


def get_signed_recording_url(storage_path: str, expires_in: int = 86400) -> str:
    """
    Generates a signed URL for a recording in Supabase Storage.
    expires_in is set to 86400 seconds (24 hours) instead of the previous
    1 hour — long enough to survive even a slow transcription job and still
    be playable when the user checks back later.
    """
    result = supabase.storage.from_(
        settings.supabase_recordings_bucket
    ).create_signed_url(storage_path, expires_in)
    return result["signedURL"]