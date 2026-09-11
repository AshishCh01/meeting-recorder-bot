from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import update
from app.db.database import get_db
from app.db.models import Meeting
from app.api.auth import verify_webhook_token
from app.models.meeting import RecordingCompleteWebhook
from app.services.storage_service import get_signed_recording_url
from app.services.transcription_service import submit_transcription

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

@router.post("/recording-complete", dependencies=[Depends(verify_webhook_token)])
def recording_complete(
    payload: RecordingCompleteWebhook, 
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

    # Defense-in-depth: meeting-bot already knew this pairing when it
    # started the job. A mismatch means a bug upstream, not a real
    # update - fail loudly instead of silently touching the wrong
    # user's meeting.
    if str(meeting.user_id) != payload.user_id:
        raise HTTPException(409, "meeting_id does not belong to payload.user_id")

    # Best-effort progress pings from mid-lifecycle (not the final
    # completed/failed report). Both are fire-and-forget and sent moments
    # apart, so an out-of-order response could otherwise race "recording"
    # backward to "waiting_for_admission" - exclude states already past
    # each ping's point in the lifecycle to keep status moving forward only.
    if payload.status == "waiting_for_admission":
        db.execute(
            update(Meeting)
            .where(Meeting.id == payload.meeting_id, Meeting.status.notin_(["recording", "transcribing", "completed", "failed"]))
            .values(status="waiting_for_admission")
        )
        db.commit()
        return {"status": "received"}

    if payload.status == "recording":
        db.execute(
            update(Meeting)
            .where(Meeting.id == payload.meeting_id, Meeting.status.notin_(["transcribing", "completed", "failed"]))
            .values(status="recording")
        )
        db.commit()
        return {"status": "received"}

    if payload.status == "completed" and payload.recording_path:
        try:
            signed_url = get_signed_recording_url(payload.recording_path)
        except Exception as e:
            print(f"[webhook] Could not generate signed URL: {e}")
            db.execute(
                update(Meeting)
                .where(Meeting.id == payload.meeting_id, Meeting.status.notin_(["transcribing", "completed"]))
                .values(status="failed", error_message=f"Upload reported but file not found in storage: {e}")
            )
            db.commit()
            return {"status": "received"}

        result = db.execute(
            update(Meeting)
            .where(Meeting.id == payload.meeting_id, Meeting.status.notin_(["transcribing", "completed"]))
            .values(
                status="transcribing",
                recording_url=signed_url,
                duration_seconds=payload.duration_seconds
            )
        )
        db.commit()

        if result.rowcount == 0:
            return {"status": "already_processed"}

        submit_transcription(payload.meeting_id, payload.recording_path)
        return {"status": "received"}

    result = db.execute(
        update(Meeting)
        .where(Meeting.id == payload.meeting_id, Meeting.status.notin_(["transcribing", "completed"]))
        .values(
            status="failed",
            error_message=payload.error_message or "Recording failed"
        )
    )
    db.commit()
    return {"status": "received"}