from fastapi import APIRouter
from app.db.supabase import supabase
from app.models.meeting import RecordingCompleteWebhook
from app.services.storage_service import get_signed_recording_url

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/recording-complete")
def recording_complete(payload: RecordingCompleteWebhook):
    """
    Called by meeting-bot once it finishes joining/recording/uploading a meeting.
    On success, payload.recording_path is the object path inside the Supabase
    Storage bucket that meeting-bot already uploaded the file to.
    """
    update = {"status": payload.status}

    if payload.status == "completed" and payload.recording_path:
        update["recording_url"] = get_signed_recording_url(payload.recording_path)
        update["duration_seconds"] = payload.duration_seconds

    if payload.status == "failed":
        update["error_message"] = payload.error_message

    supabase.table("meetings").update(update).eq("id", payload.meeting_id).execute()
    return {"status": "received"}
