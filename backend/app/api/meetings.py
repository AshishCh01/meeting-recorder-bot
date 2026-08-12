from fastapi import APIRouter, HTTPException
from app.db.supabase import supabase
from app.models.meeting import MeetingCreate
from app.services.platform_detector import detect_platform
from app.services.bot_service import trigger_bot_join

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
    return result.data


@router.get("/{meeting_id}")
def get_meeting(meeting_id: str):
    result = supabase.table("meetings").select("*").eq("id", meeting_id).single().execute()
    if not result.data:
        raise HTTPException(404, "Meeting not found")
    return result.data
