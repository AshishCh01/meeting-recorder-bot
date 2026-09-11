"""
The dispatcher behind the bot-join queue (docs/scaling-plan.md, Phase C2).

Why this exists: trigger_bot_join used to post to meeting-bot synchronously
from the request thread. A bot already recording answers 409 "Bot is currently
busy with another meeting", the exception marked the meeting failed, and
POST /meetings returned 502 - so two meetings starting at 10:00 with one
recorder meant one user simply lost their recording. Here, that meeting sits
in "queued" and joins when a recorder frees up.

Everything below runs inside the existing arq worker container (app/worker.py),
against the existing SessionLocal. No new engine - the Supabase connection-pool
limit noted at the end of the A5 section is still open, and adding a second
create_engine here would make it worse.
"""
import logging
from dataclasses import dataclass

import httpx
from sqlalchemy import update

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting, User
from app.services import bot_service

logger = logging.getLogger(__name__)

# The status a meeting waits in. Every guard below is written against it
# rather than against a list of statuses, because "is this meeting still
# waiting for a recorder?" is the only question the dispatcher may act on.
QUEUED = "queued"


@dataclass(frozen=True)
class DispatchResult:
    """
    What one dispatch attempt achieved. `reason` is carried through to the
    exhaustion message, so a meeting that gives up says whether the bot was
    full for 20 minutes or unreachable for 20 minutes - the two have
    completely different fixes.
    """
    outcome: str          # "dispatched" | "dropped" | "waiting"
    reason: str = ""

    @property
    def should_retry(self) -> bool:
        return self.outcome == "waiting"


DROPPED = DispatchResult("dropped")


def dispatch_queued_meeting(meeting_id: str) -> DispatchResult:
    """
    One attempt at placing one queued meeting on the bot.

    Synchronous and blocking (httpx + SQLAlchemy), so the arq task runs it via
    asyncio.to_thread and it opens and closes its own Session inside that
    thread - the same rule transcribe_recording follows, and for the same
    reason: a Session must not be opened on the loop thread and closed there
    around an await.
    """
    db = SessionLocal()
    try:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()

        # Re-check before acting, exactly as _resume_if_work_already_done does
        # for A3. Between enqueue and now the user may have deleted or stopped
        # the meeting, or the watchdog may have swept it. Joining a meeting
        # the user cancelled is worse than not joining at all: the bot appears
        # in a call nobody expects it in.
        if meeting is None:
            logger.info("[dispatch] meeting %s no longer exists - dropping job", meeting_id)
            return DROPPED
        if meeting.status != QUEUED:
            logger.info(
                "[dispatch] meeting %s is '%s', not '%s' - dropping job",
                meeting_id, meeting.status, QUEUED,
            )
            return DROPPED

        platform = meeting.platform
        meeting_url = meeting.meeting_url
        user_id = str(meeting.user_id)
        db_user = db.query(User).filter(User.id == meeting.user_id).first()
        bot_display_name = db_user.bot_display_name if db_user else None

        # An unreachable bot is a reason to wait, not a reason to fail the
        # meeting: the container may be restarting, and a recording that
        # starts two minutes late is still a recording. If it never comes
        # back, the attempt cap turns that into a failure naming this error.
        try:
            capacity = bot_service.get_bot_capacity()
        except Exception as e:
            logger.warning("[dispatch] could not read bot capacity for meeting %s: %s", meeting_id, e)
            return DispatchResult("waiting", f"the recorder could not be reached ({e})")

        available = capacity.get("available", 0)
        if not available or available <= 0:
            logger.info(
                "[dispatch] meeting %s still waiting - recorder at %s/%s",
                meeting_id, capacity.get("active"), capacity.get("max"),
            )
            return DispatchResult(
                "waiting",
                f"the recorder was busy ({capacity.get('active')}/{capacity.get('max')} in use)",
            )

        # Claim before posting, not after. Two dispatch jobs can reach this
        # line for the same meeting (a duplicate enqueue, or two workers), and
        # whoever wins this conditional UPDATE owns the join - the same
        # advisory-select/atomic-claim pattern as scheduler._claim. Claiming
        # afterwards would leave a window where both had already posted.
        #
        # The cost of claiming first is that a crash between here and the post
        # leaves the meeting in "joining" with no bot. That is bounded and
        # familiar: watchdog_joining_ttl_minutes sweeps it in 10 minutes,
        # which is exactly what a failed synchronous join did before C2.
        claimed = db.execute(
            update(Meeting)
            .where(Meeting.id == meeting_id, Meeting.status == QUEUED)
            .values(status="joining", error_message=None)
        )
        db.commit()
        if claimed.rowcount != 1:
            logger.info("[dispatch] meeting %s was claimed by someone else - dropping job", meeting_id)
            return DROPPED

        try:
            bot_service.post_join(platform, meeting_url, meeting_id, user_id, bot_display_name)
        except Exception as e:
            # The 409 case is the one that matters: /capacity said there was
            # room and the bot disagreed. It is not a lock, and the bot's
            # admission rule is the authority - so this is a re-queue, not a
            # failure. Anything else (a timeout, a 500) is treated the same
            # way, because the meeting has not started and waiting is still
            # strictly better than failing.
            _return_to_queue(db, meeting_id)
            reason = _describe(e)
            logger.warning("[dispatch] join for meeting %s did not take (%s) - re-queued", meeting_id, reason)
            return DispatchResult("waiting", reason)

        logger.info("[dispatch] meeting %s dispatched to the recorder", meeting_id)
        return DispatchResult("dispatched")
    finally:
        db.close()


def record_dispatch_exhausted(meeting_id: str, reason: str) -> None:
    """
    The bound on waiting. Deferring forever would turn "meeting lost" into
    "meeting silently pending forever", which is worse - nothing surfaces it,
    and the user is left watching a spinner that will never resolve.

    Guarded on status == "queued" so a meeting that moved on between the last
    attempt and this write (a late dispatch, a webhook) is never stomped with
    a failure it did not have.
    """
    db = SessionLocal()
    try:
        result = db.execute(
            update(Meeting)
            .where(Meeting.id == meeting_id, Meeting.status == QUEUED)
            .values(
                status="failed",
                # Names the real cause, not a generic timeout: "waited for a
                # free recorder" is actionable (raise MAX_CONCURRENT_MEETINGS,
                # or add a host), "timed out" is not.
                error_message=(
                    f"Waited {_wait_window_description()} for a free recorder and never got one - "
                    f"{reason}. No recording was made."
                ),
            )
        )
        db.commit()
        if result.rowcount:
            logger.error("[dispatch] meeting %s gave up waiting for a recorder: %s", meeting_id, reason)
        else:
            logger.info("[dispatch] meeting %s left 'queued' before the wait ran out - nothing to fail", meeting_id)
    finally:
        db.close()


def cancel_queued_meeting(db, meeting_id) -> bool:
    """
    Stops a meeting that is still waiting for a recorder. Returns True if it
    was cancelled, False if it had already left "queued".

    There is no bot session to stop - no join was ever posted - so this never
    calls the bot. It is one conditional UPDATE, and it is safe against the
    dispatcher for the same reason two dispatch jobs are safe against each
    other: the dispatcher's claim is `queued -> joining` and this is
    `queued -> failed`, both guarded on status == "queued". Postgres lets
    exactly one of them match the row. If this wins, the dispatcher's next
    attempt re-reads the row, sees "failed" and drops the job without
    contacting the bot. If the dispatcher won, this returns False and the
    caller stops the bot the ordinary way.

    Written as "failed", the same terminal status a bot-side stop ends in, so
    a meeting the user stopped looks the same whether it had a bot yet or not.
    """
    result = db.execute(
        update(Meeting)
        .where(Meeting.id == meeting_id, Meeting.status == QUEUED)
        .values(
            status="failed",
            error_message="Stopped before a recorder became free. No recording was made.",
        )
    )
    db.commit()
    if result.rowcount:
        logger.info("[dispatch] meeting %s stopped while queued - its dispatch job will drop", meeting_id)
    return result.rowcount == 1


def _return_to_queue(db, meeting_id: str) -> None:
    """
    Undoes the claim after a join that did not take, so the next attempt can
    try again. Guarded on "joining" so a webhook that has already moved the
    meeting on (the bot did accept it, and said so before we saw the error)
    is not dragged backwards into the queue.
    """
    db.execute(
        update(Meeting)
        .where(Meeting.id == meeting_id, Meeting.status == "joining")
        .values(status=QUEUED)
    )
    db.commit()


def _describe(exc: Exception) -> str:
    """A reason string worth putting in front of a user."""
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 409:
            return "the recorder was already full when the join was posted"
        return f"the recorder rejected the join (HTTP {exc.response.status_code})"
    return f"the recorder could not be reached ({exc})"


def _wait_window_description() -> str:
    total_seconds = settings.bot_dispatch_max_attempts * settings.bot_dispatch_retry_delay_seconds
    minutes = total_seconds // 60
    if not minutes:
        return f"{total_seconds} seconds"
    return "1 minute" if minutes == 1 else f"{minutes} minutes"
