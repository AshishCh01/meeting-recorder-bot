"""
Quota enforcement - billing Phase 2.

What a plan *allows* lives in plans.py. What a user has actually *used*, and
whether one more is permitted, lives here. Every gate in the app calls one of
the two enforce_* functions below, so there is one definition of "you are out
of meetings" rather than one per endpoint.

Four things worth knowing before changing anything in here:

**Usage is counted, never accumulated.** There is no counter column and no
monthly reset job: meetings_used is a COUNT over the meetings table inside the
period window, and AI questions are a COUNT over the two message tables. A
counter can drift from reality - a failed increment, a double-count on a
retry, a reset that did not run - and then bills someone for meetings they did
not have. A COUNT cannot drift, needs no backfill for existing users, and at
this product's volume is a sub-millisecond index scan.

The one thing counting gives up: deleting a meeting frees its quota slot,
because the row it was counting is gone. A free user could in principle record
five, delete them and record five more. That is a known, accepted hole in a
demo billing system - closing it needs an append-only billable-events table
that survives the delete, which is not worth building before anyone is
actually being charged.

**A period is a billing period, not a calendar month.** A paying user's
window is their subscription's current_period_start/end, so someone who
subscribes on the 20th gets 30 meetings from the 20th, not 30 until the 1st.
Only free users, who have no subscription row, fall back to the calendar
month. All of it in UTC - see _month_window.

**users.plan is a cache, and this module does not trust it.** resolve() reads
the subscription row anyway (it needs the window from it), so it costs nothing
to check that a user marked "pro" actually has a live subscription behind it.
If the expiry sweep has not run yet, or a row was edited by hand, the user is
treated as free rather than given a plan they are not paying for.

**Per-meeting chat counts against the Ask AI budget.** The tier table names
only "Ask AI", but the per-meeting chat panel calls the same models at the
same cost, and leaving it ungated would make the free tier's 20-question cap
mean nothing - a user would simply ask inside a meeting instead. A cap that is
trivially bypassed is worse than no cap, because the pricing page implies a
limit that does not exist. If that is not wanted, count only AskAiMessage in
_count_ai_questions and the rest of this module is unchanged.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.billing.plans import FREE, Plan, get_plan
from app.db.models import AskAiMessage, ChatMessage, Meeting, Subscription, User

logger = logging.getLogger(__name__)

# 402 Payment Required, not 403 and not 429. It is none of "you are not
# allowed", "you are not signed in" or "slow down" - it is "this costs money
# and you have not paid for this much of it", and the frontend branches on the
# status to show an upgrade prompt instead of an error toast.
QUOTA_STATUS = 402


@dataclass(frozen=True)
class Window:
    """The period usage is counted inside. Half-open: start <= t < end."""
    start: datetime
    end: datetime
    # How to name it to a user: "this month" or "this billing period".
    label: str


@dataclass(frozen=True)
class Usage:
    """
    Everything the usage meters (Phase 6) and the gates below need, from one
    pass over the database. A limit of None means unlimited - render it as
    "Unlimited", never as 0.
    """
    plan: Plan
    window: Window
    meetings_used: int
    meetings_limit: Optional[int]
    ai_questions_used: int
    ai_questions_limit: Optional[int]
    max_duration_minutes: int

    @property
    def meetings_remaining(self) -> Optional[int]:
        if self.meetings_limit is None:
            return None
        return max(0, self.meetings_limit - self.meetings_used)

    @property
    def ai_questions_remaining(self) -> Optional[int]:
        if self.ai_questions_limit is None:
            return None
        return max(0, self.ai_questions_limit - self.ai_questions_used)


def _month_window(now: datetime) -> Window:
    """
    The calendar month containing `now`, in UTC - the free tier's period.

    UTC rather than the user's timezone on purpose: the window has to agree
    with itself between the request that counts usage and the one that refuses
    it, and a user who travels (or whose browser reports a different zone)
    must not get a second allowance out of it.
    """
    start = now.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return Window(start=start, end=end, label="this month")


def resolve(db: Session, user_id: str, now: Optional[datetime] = None) -> Tuple[Plan, Window]:
    """
    The user's effective plan and the window their usage is counted in.

    "Effective" is the point: a users.plan of "pro" with no live subscription
    behind it resolves to Free. See the module docstring on why this does not
    trust the cache.
    """
    now = now or datetime.now(timezone.utc)

    row = (
        db.query(User.plan, Subscription)
        .outerjoin(Subscription, Subscription.user_id == User.id)
        .filter(User.id == user_id)
        .first()
    )
    if row is None:
        # No user row at all. Something upstream is wrong, but a quota check is
        # not the place to discover it - fall back to the tightest plan rather
        # than raising from inside a gate.
        logger.warning("[quota] no user row for %s - treating as free", user_id)
        return get_plan(FREE), _month_window(now)

    cached_plan, subscription = row

    if subscription is None or subscription.status != "active":
        return get_plan(FREE), _month_window(now)

    period_end = subscription.current_period_end
    if period_end.tzinfo is None:
        period_end = period_end.replace(tzinfo=timezone.utc)
    if period_end <= now:
        # Paid up until some point in the past. The expiry sweep will move them
        # to free; until it does, they are free here.
        logger.info(
            "[quota] subscription for %s ended at %s - treating as free until the sweep runs",
            user_id, period_end.isoformat(),
        )
        return get_plan(FREE), _month_window(now)

    if cached_plan != subscription.plan:
        # Not fatal - the subscription is the truth and is used below - but it
        # means something wrote users.plan that should not have. The
        # subscription code is the only writer; see the User.plan comment.
        logger.warning(
            "[quota] users.plan (%s) disagrees with the subscription (%s) for %s",
            cached_plan, subscription.plan, user_id,
        )

    period_start = subscription.current_period_start
    if period_start.tzinfo is None:
        period_start = period_start.replace(tzinfo=timezone.utc)

    return get_plan(subscription.plan), Window(
        start=period_start, end=period_end, label="this billing period"
    )


def _count_meetings(db: Session, user_id: str, window: Window) -> int:
    """
    Meetings this user started inside the window.

    Counted on created_at, not on scheduled_at: the quota is spent when the
    meeting is booked, so scheduling six months of a weekly standup cannot slip
    past a cap that only looked at when each one runs. Failed meetings are
    included - a recorder was dispatched either way, which is the cost the cap
    exists to bound.
    """
    return (
        db.query(func.count(Meeting.id))
        .filter(
            Meeting.user_id == user_id,
            Meeting.created_at >= window.start,
            Meeting.created_at < window.end,
        )
        .scalar()
    ) or 0


def _count_ai_questions(db: Session, user_id: str, window: Window) -> int:
    """
    Questions asked of the AI inside the window - Ask AI and the per-meeting
    chat panel together. See the module docstring for why both.

    Only the user's own turns are counted. An assistant turn is the answer to a
    question already counted, and counting both would halve every cap.
    """
    ask = (
        db.query(func.count(AskAiMessage.id))
        .filter(
            AskAiMessage.user_id == user_id,
            AskAiMessage.role == "user",
            AskAiMessage.created_at >= window.start,
            AskAiMessage.created_at < window.end,
        )
        .scalar()
    ) or 0

    meeting_chat = (
        db.query(func.count(ChatMessage.id))
        .filter(
            ChatMessage.user_id == user_id,
            ChatMessage.role == "user",
            ChatMessage.created_at >= window.start,
            ChatMessage.created_at < window.end,
        )
        .scalar()
    ) or 0

    return ask + meeting_chat


def snapshot(db: Session, user_id: str, now: Optional[datetime] = None) -> Usage:
    """
    One user's full usage picture. What GET /billing/usage (Phase 6) returns,
    and what the gates below are built on.
    """
    from app.billing.plans import effective_max_duration_minutes

    plan, window = resolve(db, user_id, now)
    return Usage(
        plan=plan,
        window=window,
        meetings_used=_count_meetings(db, user_id, window),
        meetings_limit=plan.meetings_per_period,
        ai_questions_used=_count_ai_questions(db, user_id, window),
        ai_questions_limit=plan.ask_ai_questions_per_period,
        max_duration_minutes=effective_max_duration_minutes(plan),
    )


def resolve_max_duration_minutes(db: Session, user_id: str, now: Optional[datetime] = None) -> int:
    """
    Just the recording cap, without counting any usage.

    The dispatcher wants this and nothing else, and it runs inside a worker
    holding a claimed host - two COUNT queries it would throw away are not
    free there. Falls back to the recorder's own hard limit if anything about
    the lookup goes wrong: a billing question must never be the reason a
    meeting fails to join.
    """
    from app.billing.plans import effective_max_duration_minutes
    from app.config import settings

    try:
        plan, _ = resolve(db, user_id, now)
        return effective_max_duration_minutes(plan)
    except Exception as e:
        logger.warning(
            "[quota] could not resolve a duration cap for %s (%s) - using the recorder default",
            user_id, e,
        )
        return settings.max_recording_duration_minutes


def _refuse(message: str, used: int, limit: int, resource: str) -> HTTPException:
    """
    The 402 every gate raises.

    `detail` is a plain sentence because that is what the frontend's existing
    `!response.ok` branches render verbatim (see rate_limit.check on the same
    constraint). The numbers also go out as headers so a client that wants to
    draw a meter does not have to parse English out of the sentence.
    """
    return HTTPException(
        status_code=QUOTA_STATUS,
        detail=message,
        headers={
            "X-Quota-Resource": resource,
            "X-Quota-Used": str(used),
            "X-Quota-Limit": str(limit),
        },
    )


# The plan flags a route may gate on, and how to name each one to a user.
#
# A whitelist rather than getattr(plan, feature): a typo in a call site must
# fail loudly here, not silently read as False and lock everyone out of a
# feature they paid for - or, worse, be spelled as a field that does not exist
# and read as truthy through some future refactor.
FEATURES = {
    "pdf_export": "PDF export",
    "calendar_scheduling": "Calendar scheduling",
    "team_workspace": "The shared team workspace",
}


def require_feature(db: Session, user_id: str, feature: str, now: Optional[datetime] = None) -> Plan:
    """
    Raises 402 unless the user's plan includes `feature`. Returns the plan, so
    a caller that needs it for anything else does not resolve twice.

    A feature gate, unlike the quota gates above, counts nothing - it is a
    single boolean on the plan. It still resolves through resolve(), so an
    expired subscription loses the feature at the same moment it loses the
    quota, rather than keeping it until a sweep runs.
    """
    if feature not in FEATURES:
        raise ValueError(
            "{!r} is not a gateable feature. Known: {}".format(feature, ", ".join(sorted(FEATURES)))
        )

    plan, _ = resolve(db, user_id, now)

    if getattr(plan, feature):
        return plan

    logger.info("[quota] user %s asked for %s, which %s does not include", user_id, feature, plan.id)
    raise HTTPException(
        status_code=QUOTA_STATUS,
        detail="{} is not part of the {} plan. Upgrade to use it.".format(
            FEATURES[feature], plan.name
        ),
        headers={"X-Quota-Resource": feature},
    )


def enforce_meeting_quota(db: Session, user_id: str, now: Optional[datetime] = None) -> Usage:
    """
    Called before a meeting row is created, on every path that creates one -
    POST /meetings and the calendar's "record this event". Raises 402 when the
    plan's meeting allowance for the period is already spent.

    Returns the Usage it computed, so a caller that also needs the duration cap
    does not pay for a second pass.
    """
    usage = snapshot(db, user_id, now)
    plan = usage.plan

    if plan.allows(usage.meetings_limit, usage.meetings_used):
        return usage

    logger.info(
        "[quota] user %s is out of meetings on %s (%s/%s %s)",
        user_id, plan.id, usage.meetings_used, usage.meetings_limit, usage.window.label,
    )
    raise _refuse(
        "You have used all {} meetings on the {} plan {}. Upgrade to record more.".format(
            usage.meetings_limit, plan.name, usage.window.label
        ),
        used=usage.meetings_used,
        limit=usage.meetings_limit,
        resource="meetings",
    )


def enforce_ai_question_quota(db: Session, user_id: str, now: Optional[datetime] = None) -> Usage:
    """
    Called before an AI answer is generated, by both chat surfaces. Raises 402
    when the plan's question allowance for the period is spent.

    This runs *in addition to* the rate limits in app/services/rate_limit.py,
    which are a different thing: those bound how fast a user may ask, this
    bounds how many they bought. A user can be inside both, over one, or over
    both, and the two messages say different things on purpose.
    """
    usage = snapshot(db, user_id, now)
    plan = usage.plan

    if plan.allows(usage.ai_questions_limit, usage.ai_questions_used):
        return usage

    logger.info(
        "[quota] user %s is out of AI questions on %s (%s/%s %s)",
        user_id, plan.id, usage.ai_questions_used, usage.ai_questions_limit, usage.window.label,
    )
    raise _refuse(
        "You have used all {} AI questions on the {} plan {}. Upgrade for unlimited questions.".format(
            usage.ai_questions_limit, plan.name, usage.window.label
        ),
        used=usage.ai_questions_used,
        limit=usage.ai_questions_limit,
        resource="ai_questions",
    )
