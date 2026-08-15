from fastapi import APIRouter, HTTPException
from app.db.supabase import supabase
from app.models.meeting import MeetingCreate
from app.services.platform_detector import detect_platform
from app.services.bot_service import trigger_bot_join
from app.services.storage_service import get_signed_recording_url

router = APIRouter(prefix="/meetings", tags=["meetings"])


@router.post("")
def create_meeting(payload: MeetingCreate):
    try:
        platform = detect_platform(payload.meeting_url)
    except ValueError as e:
        raise HTTPException(400, str(e))

    result = supabase.table("meetings").insert({
        "meeting_url": payload.meeting_url,
        "platform": platform,
        "status": "scheduled",
    }).execute()
    meeting = result.data[0]

    try:
        supabase.table("meetings").update({"status": "joining"}).eq("id", meeting["id"]).execute()
        trigger_bot_join(platform, payload.meeting_url, meeting["id"])
    except Exception as e:
        supabase.table("meetings").update({
            "status": "failed",
            "error_message": f"Failed to start bot: {e}",
        }).eq("id", meeting["id"]).execute()
        raise HTTPException(502, f"Could not start recording bot: {e}")

    return supabase.table("meetings").select("*").eq("id", meeting["id"]).single().execute().data


@router.get("")
def list_meetings():
    result = supabase.table("meetings").select("*").order("created_at", desc=True).execute()
    meetings = result.data

    # Regenerate signed URLs on every read so they never appear expired
    # in the dashboard — the stored URL was generated at webhook time and
    # would go stale after 24h. This keeps "View recording" always fresh.
    for meeting in meetings:
        if meeting.get("status") == "completed" and meeting.get("recording_url"):
            # Extract the storage path from the stored signed URL, or
            # reconstruct it from the meeting ID directly — simpler and
            # more reliable than parsing the signed URL itself.
            storage_path = f"{meeting['id']}/recording.m4a"
            try:
                meeting["recording_url"] = get_signed_recording_url(storage_path)
            except Exception:
                pass  # keep the existing URL if regeneration fails

    return meetings


@router.get("/{meeting_id}")
def get_meeting(meeting_id: str):
    result = supabase.table("meetings").select("*").eq("id", meeting_id).single().execute()
    if not result.data:
        raise HTTPException(404, "Meeting not found")

    meeting = result.data
    if meeting.get("status") == "completed" and meeting.get("recording_url"):
        storage_path = f"{meeting['id']}/recording.m4a"
        try:
            meeting["recording_url"] = get_signed_recording_url(storage_path)
        except Exception:
            pass

    return meeting