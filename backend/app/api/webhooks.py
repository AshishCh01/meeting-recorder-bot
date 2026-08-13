from fastapi import APIRouter, BackgroundTasks
from app.db.supabase import supabase
from app.models.meeting import RecordingCompleteWebhook
from app.services.storage_service import get_signed_recording_url
from app.services.transcription_service import transcribe_recording

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/recording-complete")
def recording_complete(payload: RecordingCompleteWebhook, background_tasks: BackgroundTasks):
    """
    Called by meeting-bot once it finishes joining/recording/uploading a meeting.
    On success, payload.recording_path is the object path inside the Supabase
    Storage bucket that meeting-bot already uploaded the file to.
    """
    if payload.status == "completed" and payload.recording_path:
        update = {
            "status": "transcribing",
            "recording_url": get_signed_recording_url(payload.recording_path),
            "duration_seconds": payload.duration_seconds,
        }
        supabase.table("meetings").update(update).eq("id", payload.meeting_id).execute()
        background_tasks.add_task(transcribe_recording, payload.meeting_id, payload.recording_path)
        return {"status": "received"}

    update = {"status": payload.status}
    if payload.status == "failed":
        update["error_message"] = payload.error_message

    supabase.table("meetings").update(update).eq("id", payload.meeting_id).execute()
    return {"status": "received"}
