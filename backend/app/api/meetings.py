import logging
from uuid import UUID
from fastapi import APIRouter, HTTPException, Depends, Response
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Meeting, MeetingChunk, User
from app.api.auth import get_current_user
from app.models.meeting import MeetingCreate
from app.services.platform_detector import detect_platform
from app.services.bot_service import initial_status, trigger_bot_join, stop_bot
from app.services.bot_dispatch import cancel_queued_meeting
from app.services import bot_registry, rate_limit
import httpx
from app.services.storage_service import get_signed_recording_url
from app.services.transcription_service import submit_transcription
from app.db.supabase import supabase
from app.config import settings
from app.services.pdf_service import generate_meeting_pdf
from app.billing import quota

router = APIRouter(prefix="/meetings", tags=["meetings"])

logger = logging.getLogger(__name__)


def _recorder_for(meeting: Meeting) -> bot_registry.BotHost:
    """
    Which recorder in the pool holds this meeting's session (Phase C3).

    Every route that talks to a bot about an existing meeting goes through
    here, because with more than one host "the bot" is no longer a thing:
    asking the wrong one 404s on a session it never had while the real
    recording carries on, and re-upload would look for a file that is on
    another machine's disk.

    A meeting with no recorded host is not automatically an error -
    require_host resolves NULL to the sole configured host, which covers every
    meeting from before C3 and every single-host install. It raises only where
    the answer is genuinely unknowable, and that becomes a 409 naming the
    reason rather than a 500 with a traceback. "queued" never reaches here at
    all: it has no session, and stop_meeting cancels it before this point.
    """
    try:
        return bot_registry.require_host(meeting.bot_host_id)
    except bot_registry.UnknownBotHost as e:
        raise HTTPException(409, f"Cannot reach the recorder for this meeting: {e}")

def meeting_to_dict(m: Meeting):
    return {
        "id": str(m.id),
        "user_id": str(m.user_id) if m.user_id else None,
        "meeting_url": m.meeting_url,
        "title": m.title or m.meeting_url,
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
    # Rate limited (Phase B1). Cheaper per call than chat, but each one
    # dispatches a recorder - a headful Chrome plus ffmpeg at roughly 2GB -
    # so what it protects is the bot pool rather than a model bill. The
    # limiter is an async dependency and this route is a plain `def` that
    # runs in the threadpool; FastAPI solves dependencies on the event loop
    # either way, so there is one code path, not two. See rate_limit.
    user_id: str = Depends(rate_limit.limited(rate_limit.MEETING_CREATE))
):
    meeting_url = str(payload.meeting_url)

    try:
        platform = detect_platform(meeting_url)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Billing Phase 2. After the URL is validated (a malformed link is a 400
    # whatever plan you are on, and should not read as "you are out of
    # meetings") and before the row is inserted, so a refused meeting leaves
    # nothing behind. 402 with a sentence the frontend renders as-is.
    usage = quota.enforce_meeting_quota(db, user_id)

    # "joining" pre-C2, "queued" once BOT_DISPATCH_USE_QUEUE is on - the bot has
    # not been contacted yet on the queued path, and parking the meeting in
    # "joining" while it waits would have the watchdog sweep it after 10
    # minutes. bot_service owns that choice; see initial_status.
    #
    # Written in the insert itself, and committed before the trigger, so the
    # dispatch job can never read this row before it says "queued". It used to
    # be inserted as "scheduled" and moved on in a second commit; a crash
    # between the two left a "scheduled" row with no scheduled_at, which the
    # scheduler never reads (docs/scaling-plan.md, B3 finding 3).
    meeting = Meeting(
        user_id=user_id,
        meeting_url=meeting_url,
        platform=platform,
        status=initial_status(),
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)

    try:
        db_user = db.query(User).filter(User.id == user_id).first()
        # The plan's recording cap travels with the join, so the recorder
        # stops at the length this user actually paid for. Already clamped to
        # the recorder's own hard limit by effective_max_duration_minutes.
        trigger_bot_join(
            platform, meeting_url, str(meeting.id), user_id, db_user.bot_display_name,
            max_duration_minutes=usage.max_duration_minutes,
        )
    except Exception as e:
        # Still reached on the queued path if the *enqueue* itself fails
        # (Redis down), which is the one case where the caller genuinely
        # cannot be told "it will happen later". A busy bot no longer lands
        # here at all - that is the phase.
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
    return [meeting_to_dict(m) for m in meetings]


@router.get("/{meeting_id}")
def get_meeting(
    meeting_id: UUID,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    m_dict = meeting_to_dict(meeting)
    # Release the pooled connection before the signed-URL request below - a
    # round-trip to Supabase Storage. Everything past this point reads the
    # plain dict, never the ORM object, so keep it that way.
    db.close()
    # The recording is in Storage from the moment the webhook claims the
    # meeting for transcription (that claim is what sets recording_url), so
    # it is playable while transcribing and after a failed transcription too.
    # A failed meeting without recording_url never uploaded anything.
    status = m_dict.get("status")
    if status in ("transcribing", "completed") or (status == "failed" and m_dict.get("recording_url")):
        storage_path = f"{m_dict['user_id']}/{m_dict['id']}/recording.m4a"
        try:
            m_dict["audio_playback_url"] = get_signed_recording_url(storage_path, expires_in=3600)
        except Exception:
            pass

    return m_dict


from sqlalchemy import update

@router.post("/{meeting_id}/retry")
def retry_meeting(
    meeting_id: UUID,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    result = db.execute(
        update(Meeting)
        .where(Meeting.id == meeting_id, Meeting.status == "failed")
        .values(status="transcribing", error_message=None)
    )
    db.commit()

    if result.rowcount == 0:
        raise HTTPException(409, "Only failed meetings can be retried, or retry is already in progress.")

    storage_path = f"{meeting.user_id}/{meeting.id}/recording.m4a"
    folder_path = f"{meeting.user_id}/{meeting.id}"

    try:
        files = supabase.storage.from_(settings.supabase_recordings_bucket).list(folder_path)
        in_storage = bool(files) and isinstance(files, list) and any(f.get('name') == 'recording.m4a' for f in files)
    except Exception as e:
        db.execute(update(Meeting).where(Meeting.id == meeting_id).values(status="failed", error_message=f"Error communicating with storage: {e}"))
        db.commit()
        raise HTTPException(500, f"Error communicating with storage: {e}")

    if not in_storage:
        # The upload itself may have failed, in which case meeting-bot kept
        # the local file - ask it to upload again before calling it lost.
        return _reupload_from_bot(db, meeting)

    submit_transcription(str(meeting.id), storage_path)

    return {"status": "retrying"}


RECORDING_NOT_FOUND = "Recording file not found in storage. Cannot retry."


def _reupload_from_bot(db: Session, meeting: Meeting):
    """
    Asks meeting-bot to re-upload a recording it kept after a failed upload.
    The bot answers 202 and reports the outcome later through the normal
    recording-complete webhook, exactly as a fresh recording would.

    The meeting must be "uploading" - not the "transcribing" retry_meeting
    claimed it as - before the bot is called. The webhook's completed branch
    only accepts meetings notin_(["transcribing", "completed"]), so a meeting
    left in "transcribing" would have the bot's completion rejected as
    already processed and the recovered recording would never be transcribed.
    "uploading" also carries the watchdog's 20-minute TTL, which bounds a
    re-upload whose webhook never arrives.

    Lives here rather than in bot_service.py only to keep this fix to the
    files it had to touch.

    Phase C3: a kept recording is on the disk of the host that recorded it and
    nowhere else, so this asks that host - meeting.bot_host_id - rather than a
    single configured URL. On a pool of one that resolves to the same bot it
    always did.
    """
    # Before the claim: if the host cannot be resolved there is nothing to ask
    # and no reason to move the meeting out of "transcribing" first. It must
    # still go back to "failed", though - retry_meeting already claimed it as
    # "transcribing", and nothing else will ever move it on. The common case
    # is a meeting cancelled while queued on a pool of two or more: it never
    # reached a recorder, so its bot_host_id is NULL and there is no file.
    try:
        host = _recorder_for(meeting)
    except HTTPException as e:
        db.execute(
            update(Meeting)
            .where(Meeting.id == meeting.id, Meeting.status == "transcribing")
            .values(status="failed", error_message=e.detail)
        )
        db.commit()
        raise

    result = db.execute(
        update(Meeting)
        .where(Meeting.id == meeting.id, Meeting.status == "transcribing")
        .values(status="uploading")
    )
    db.commit()
    if result.rowcount == 0:
        # Something moved the meeting between the claim and here (the
        # watchdog, a delete). Its status is no longer ours to drive.
        raise HTTPException(409, "Meeting changed state during retry.")

    def fail(message: str):
        # Conditional on "uploading": if the bot did accept the job despite
        # the error seen here (a timeout on a slow 202), its webhook may have
        # already moved the meeting on, and that must not be overwritten.
        db.execute(
            update(Meeting)
            .where(Meeting.id == meeting.id, Meeting.status == "uploading")
            .values(status="failed", error_message=message)
        )
        db.commit()

    try:
        response = httpx.post(
            f"{host.url}/reupload",
            json={"meetingId": str(meeting.id), "userId": str(meeting.user_id)},
            headers={"Authorization": f"Bearer {settings.meeting_bot_bearer_token}"},
            timeout=10,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            # Not in storage and not on the bot: genuinely gone.
            fail(RECORDING_NOT_FOUND)
            raise HTTPException(404, RECORDING_NOT_FOUND)
        fail(f"Could not re-upload recording: bot returned {e.response.status_code}. Try again.")
        raise HTTPException(502, f"Could not re-upload recording: {e.response.text}")
    except Exception as e:
        fail(f"Could not reach recording bot to re-upload recording: {e}. Try again.")
        raise HTTPException(502, f"Could not reach recording bot to re-upload recording: {e}")

    return {"status": "reuploading"}


@router.post("/{meeting_id}/stop")
def stop_meeting(
    meeting_id: UUID,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    if meeting.status == "queued":
        # Phase C2: waiting for a recorder, so there is no bot to ask. The
        # cancel is a conditional UPDATE that races the dispatcher's claim
        # fairly - see cancel_queued_meeting. Unlike the bot path below, the
        # terminal status is known right now, so it is returned for the page
        # to show immediately instead of waiting on a webhook that will never
        # come.
        if cancel_queued_meeting(db, meeting.id):
            db.refresh(meeting)
            return {"status": "stopped", "meeting": meeting_to_dict(meeting)}
        # Lost the race: a recorder freed up and the dispatcher claimed this
        # meeting between the read above and the cancel. It is joining now,
        # so stop it the ordinary way. (If the join POST itself has not landed
        # on the bot yet, stop_bot 404s into the 409 below and the user can
        # press Stop again a moment later - a millisecond window.)
        db.refresh(meeting)

    if meeting.status not in ("joining", "waiting_for_admission", "recording"):
        raise HTTPException(409, "Meeting is not currently active - nothing to stop.")

    # Resolved before the call, and from the meeting's own column - the old
    # single MEETING_BOT_URL would now be a guess between hosts.
    host = _recorder_for(meeting)

    try:
        stop_bot(host, str(meeting_id))
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(409, "Bot has no active session for this meeting - it may have just finished on its own.")
        raise HTTPException(502, f"Could not stop recording bot: {e.response.text}")
    except Exception as e:
        raise HTTPException(502, f"Could not stop recording bot: {e}")

    # meeting-bot reports the resulting "failed" status via its usual
    # webhook once it finishes unwinding (closing the browser, stopping
    # ffmpeg) - not set here, to avoid racing that webhook.
    return {"status": "stopping"}


@router.delete("/{meeting_id}")
def delete_meeting(
    meeting_id: UUID,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    if meeting.status in ("joining", "waiting_for_admission", "recording"):
        try:
            # Phase C3: the meeting's own recorder, not "the" recorder. An
            # unresolvable host raises HTTPException from _recorder_for, which
            # is caught here with everything else - a delete must not be
            # blocked by not knowing which bot to tell, it just cannot stop
            # what it cannot find.
            stop_bot(_recorder_for(meeting), str(meeting_id))
        except Exception as e:
            # Not fatal - the bot may have already finished on its own between
            # the status check above and this call. Deletion proceeds either
            # way, since leaving the bot running would otherwise record a
            # meeting the user just deleted, with no meeting row left for its
            # completion webhook to report back to.
            logger.warning(
                "[meetings] failed to stop bot before delete: %s", e,
                extra={"meeting_id": str(meeting_id), "user_id": user_id},
            )

    storage_path = f"{meeting.user_id}/{meeting.id}/recording.m4a"
    try:
        supabase.storage.from_(settings.supabase_recordings_bucket).remove([storage_path])
    except Exception as e:
        # Not fatal - meetings that never finished recording (e.g. "scheduled"
        # or "failed" status) have no storage object to begin with.
        logger.warning(
            "[meetings] failed to delete storage object %s: %s", storage_path, e,
            extra={"meeting_id": str(meeting_id), "user_id": user_id},
        )

    db.query(MeetingChunk).filter(MeetingChunk.meeting_id == meeting.id).delete()
    db.delete(meeting)
    db.commit()

    return {"status": "deleted"}


@router.get("/{meeting_id}/export-pdf")
def export_meeting_pdf(
    meeting_id: UUID,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user)
):
    meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    # Billing Phase 4. After ownership - a meeting you do not own is a 404 on
    # every plan, and a 402 would confirm it exists - and before the
    # not-ready check, so the answer to "can I export this at all" does not
    # depend on whether this particular meeting happens to be transcribed yet.
    quota.require_feature(db, user_id, "pdf_export")

    if not meeting.transcript:
        raise HTTPException(400, "Meeting transcript is not ready yet.")

    try:
        pdf_bytes = generate_meeting_pdf(meeting)
        date_str = meeting.created_at.strftime("%Y-%m-%d") if meeting.created_at else "Unknown"
        
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="Meeting_Summary_{date_str}.pdf"'
            }
        )
    except Exception as e:
        logger.exception(
            "[meetings] PDF generation failed: %s", e,
            extra={"meeting_id": str(meeting_id), "user_id": user_id},
        )
        raise HTTPException(status_code=500, detail="Failed to compile PDF document.")