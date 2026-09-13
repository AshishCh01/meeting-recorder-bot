from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_, update
from app.db.database import get_db
from app.db.models import Meeting
from app.api.auth import verify_webhook_token
from app.models.meeting import RecordingCompleteWebhook
from app.services.storage_service import get_signed_recording_url
from app.services.transcription_service import submit_transcription

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# **Nothing in this file is rate limited, and nothing in it should be**
# (docs/scaling-plan.md, Phase B1).
#
# These are not user-facing endpoints. The caller is meeting-bot, holding the
# shared bearer token verify_webhook_token checks, and it calls in on a
# schedule this backend does not control: a "waiting_for_admission" ping, a
# "recording" ping, then the final completed/failed report, per meeting, for
# every meeting in flight across every recorder in the pool. The rate is a
# function of how many recordings are running - which is exactly the thing
# scaling up is supposed to increase.
#
# Throttling that does not save money, because no paid model is called from
# here on the request path. What it does is break recordings that are already
# in progress: a dropped "completed" webhook means a finished recording whose
# file is never fetched, and the meeting sits until the watchdog fails it -
# the user loses a meeting that was successfully recorded. There is also
# nowhere sensible to key it, since the only identity here is one token shared
# by the whole pool.
#
# The real bound on this endpoint is the bearer token and the bot pool's own
# size, not a counter.

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
    # "uploading" is deliberately not excluded: a ping can only meet it from a
    # session that is still live, where the ping is the true state - see
    # tests/test_webhook_uploading_pings.py.
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
            .where(
                Meeting.id == payload.meeting_id,
                Meeting.status.notin_(["transcribing", "completed"]),
                # A "failed" meeting is claimable only if no completed report
                # was ever accepted for it. recording_url is set by this
                # claim and by nothing else (the hand-off undo below clears
                # it), so a failed row that has one failed *after* its
                # recording was handed to transcription - and this is a
                # redelivery of that same report, not a late recording.
                # Claiming it would pay to transcribe the same audio again;
                # its recovery is POST /meetings/{id}/retry. A failed row
                # without one (the watchdog swept it mid-recording, storage
                # did not have the file yet) is a real late completion, and
                # recovering it is what this branch is for.
                ~and_(Meeting.status == "failed", Meeting.recording_url.isnot(None)),
            )
            .values(
                status="transcribing",
                recording_url=signed_url,
                duration_seconds=payload.duration_seconds,
                error_message=None,
            )
        )
        db.commit()

        if result.rowcount == 0:
            return {"status": "already_processed"}

        try:
            submit_transcription(payload.meeting_id, payload.recording_path)
        except Exception as e:
            # The claim above is only worth keeping if the hand-off happened.
            # Left in place, every retry meeting-bot sends lands on the
            # rowcount == 0 branch as "already_processed" and nothing is ever
            # enqueued - the meeting sits in "transcribing" with no job until
            # the watchdog times it out. So undo it, in this request, and
            # answer with an error the bot retries on: the retry finds a
            # meeting no completed report has been accepted for (recording_url
            # cleared) and claims it again. Only the request holding the claim
            # gets here, and only when nothing was handed off, so at most one
            # transcription still holds.
            #
            # "failed", not the status before the claim, so a Redis outage that
            # outlasts the bot's retries ends terminal and Retry-able right
            # away. Guarded on "transcribing" so this never overwrites a
            # meeting something else has already moved on.
            #
            # A process that dies between the commit above and this line runs
            # no undo; that window is still bounded by the watchdog.
            print(f"[webhook] Could not queue transcription for meeting {payload.meeting_id}: {e}")
            db.execute(
                update(Meeting)
                .where(Meeting.id == payload.meeting_id, Meeting.status == "transcribing")
                .values(
                    status="failed",
                    recording_url=None,
                    error_message=f"Recording saved, but transcription could not be queued: {e}",
                )
            )
            db.commit()
            raise HTTPException(503, "Could not queue transcription - retry this webhook")

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