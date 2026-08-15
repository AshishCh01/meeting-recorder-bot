from fastapi import APIRouter, BackgroundTasks
from app.db.supabase import supabase
from app.models.meeting import RecordingCompleteWebhook
from app.services.storage_service import get_signed_recording_url
from app.services.transcription_service import transcribe_recording

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/recording-complete")
def recording_complete(payload: RecordingCompleteWebhook, background_tasks: BackgroundTasks):
    """
    Called by meeting-bot once recording finishes.
    On success: generates signed URL, sets status to transcribing,
    then kicks off transcription as a background task.
    On failure: just marks the meeting as failed.
    """
    if payload.status == "completed" and payload.recording_path:
        try:
            signed_url = get_signed_recording_url(payload.recording_path)
        except Exception as e:
            print(f"[webhook] Could not generate signed URL: {e}")
            supabase.table("meetings").update({
                "status": "failed",
                "error_message": f"Upload reported but file not found in storage: {e}",
            }).eq("id", payload.meeting_id).execute()
            return {"status": "received"}

        supabase.table("meetings").update({
            "status": "transcribing",
            "recording_url": signed_url,
            "duration_seconds": payload.duration_seconds,
        }).eq("id", payload.meeting_id).execute()

        background_tasks.add_task(
            transcribe_recording,
            payload.meeting_id,
            payload.recording_path,
        )
        return {"status": "received"}

    supabase.table("meetings").update({
        "status": "failed",
        "error_message": payload.error_message or "Recording failed",
    }).eq("id", payload.meeting_id).execute()
    return {"status": "received"}