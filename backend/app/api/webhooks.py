from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Meeting
from app.api.auth import verify_webhook_token
from app.models.meeting import RecordingCompleteWebhook
from app.services.storage_service import get_signed_recording_url
from app.services.transcription_service import transcribe_recording

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

@router.post("/recording-complete", dependencies=[Depends(verify_webhook_token)])
def recording_complete(
    payload: RecordingCompleteWebhook, 
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """
    Called by meeting-bot once recording finishes.
    On success: generates signed URL, sets status to transcribing,
    then kicks off transcription as a background task.
    On failure: just marks the meeting as failed.
    """
    meeting = db.query(Meeting).filter(Meeting.id == payload.meeting_id).first()
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    if payload.status == "completed" and payload.recording_path:
        try:
            signed_url = get_signed_recording_url(payload.recording_path)
        except Exception as e:
            print(f"[webhook] Could not generate signed URL: {e}")
            meeting.status = "failed"
            meeting.error_message = f"Upload reported but file not found in storage: {e}"
            db.commit()
            return {"status": "received"}

        meeting.status = "transcribing"
        meeting.recording_url = signed_url
        meeting.duration_seconds = payload.duration_seconds
        db.commit()

        background_tasks.add_task(
            transcribe_recording,
            payload.meeting_id,
            payload.recording_path,
        )
        return {"status": "received"}

    meeting.status = "failed"
    meeting.error_message = payload.error_message or "Recording failed"
    db.commit()
    return {"status": "received"}