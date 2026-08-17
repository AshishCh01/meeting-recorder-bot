from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Meeting
from app.api.auth import get_current_user
from app.models.meeting import MeetingCreate
from app.services.platform_detector import detect_platform
from app.services.bot_service import trigger_bot_join
from app.services.storage_service import get_signed_recording_url

router = APIRouter(prefix="/meetings", tags=["meetings"])

def meeting_to_dict(m: Meeting):
    return {
        "id": str(m.id),
        "user_id": str(m.user_id) if m.user_id else None,
        "meeting_url": m.meeting_url,
        "platform": m.platform,
        "status": m.status,
        "recording_url": m.recording_url,
        "duration_seconds": m.duration_seconds,
        "error_message": m.error_message,
        "transcript": m.transcript,
        "created_at": m.created_at.isoformat() if m.created_at else None
    }


@router.post("")
def create_meeting(
    payload: MeetingCreate,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    try:
        platform = detect_platform(payload.meeting_url)
    except ValueError as e:
        raise HTTPException(400, str(e))

    meeting = Meeting(
        user_id=user_id,
        meeting_url=payload.meeting_url,
        platform=platform,
        status="scheduled"
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)

    try:
        meeting.status = "joining"
        db.commit()
        trigger_bot_join(platform, payload.meeting_url, str(meeting.id), user_id)
    except Exception as e:
        meeting.status = "failed"
        meeting.error_message = f"Failed to start bot: {e}"
        db.commit()
        raise HTTPException(502, f"Could not start recording bot: {e}")

    return meeting_to_dict(meeting)


@router.get("")
def list_meetings(
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    meetings = db.query(Meeting).filter(Meeting.user_id == user_id).order_by(Meeting.created_at.desc()).all()
    results = []

    for meeting in meetings:
        m_dict = meeting_to_dict(meeting)
        if m_dict.get("status") == "completed":
            storage_path = f"{m_dict['user_id']}/{m_dict['id']}/recording.m4a"
            try:
                m_dict["audio_playback_url"] = get_signed_recording_url(storage_path, expires_in=3600)
            except Exception:
                pass
        results.append(m_dict)

    return results


@router.get("/{meeting_id}")
def get_meeting(
    meeting_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    m_dict = meeting_to_dict(meeting)
    if m_dict.get("status") == "completed":
        storage_path = f"{m_dict['user_id']}/{m_dict['id']}/recording.m4a"
        try:
            m_dict["audio_playback_url"] = get_signed_recording_url(storage_path, expires_in=3600)
        except Exception:
            pass

    return m_dict