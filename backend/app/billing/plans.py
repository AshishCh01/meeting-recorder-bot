"""
The plan catalogue - the single place every tier limit, price and feature flag
is defined.

Nothing else in the codebase should hardcode "5 meetings", "45 minutes" or
"50000". Quota checks (Phase 2), feature gates (Phase 3-4), the pricing page
(Phase 5) and the usage meters (Phase 6) all read these objects, so changing a
tier is one edit here.

Four conventions the rest of the billing code relies on:

- **None means unlimited.** A limit of `None` is "no ceiling", never "zero".
  Use `Plan.allows(...)` rather than comparing against the raw field, so a
  None is not accidentally read as falsy-and-therefore-blocking.

- **Money is an integer count of paise, never a float.** 500 rupees is
  50_000 paise. Razorpay's API takes paise too, so the value passes straight
  through with no rounding step anywhere. Floats are banned here for the
  usual reason: 0.1 + 0.2 must never decide what a customer is charged.

- **A period is a billing period, not a calendar month.** For a paid plan it
  is the subscription's current_period_start/end; for free users, who have no
  subscription row, it is the calendar month. Phase 2's counters resolve
  that window - this module only says how many of a thing fit in one.

- **The duration cap here is a promise, not the enforcement point.** The
  recorder stops at MAX_RECORDING_DURATION_MINUTES
  (settings.max_recording_duration_minutes, 90 by default) whatever the plan
  says, so the cap a user actually gets is
  `effective_max_duration_minutes()` below - the smaller of the two. Raising
  Pro past 90 needs that env var raised on the bot hosts as well, or the UI
  promises three hours and the recording stops at ninety.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

# Plan ids. These strings are stored in users.plan and subscriptions.plan, so
# they are part of the schema - renaming one needs a migration.
FREE = "free"
PRO = "pro"
TEAM = "team"


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    tagline: str
    # Monthly price in paise. 0 for free. For a per-seat plan this is the
    # price of ONE seat - multiply by subscriptions.seats for the charge.
    price_paise: int
    per_seat: bool
    # Meetings that may be recorded per billing period. None = unlimited.
    meetings_per_period: Optional[int]
    # Longest single recording this plan may request, in minutes. Always
    # capped further by the recorder's own hard limit - see the module
    # docstring and effective_max_duration_minutes().
    max_duration_minutes: int
    # Ask AI questions (cross-meeting chat) per billing period. None =
    # unlimited, still bounded by ask_ai_rate_limit_requests in config.py.
    ask_ai_questions_per_period: Optional[int]
    # Feature gates. Every paid feature is a field here rather than a plan-id
    # comparison at the call site, so a gate reads `plan.pdf_export` and does
    # not have to know which tiers exist.
    pdf_export: bool
    calendar_scheduling: bool
    team_workspace: bool
    early_access: bool

    @property
    def price_rupees(self) -> int:
        """For display only. Every plan price is a whole number of rupees."""
        return self.price_paise // 100

    def allows(self, limit: Optional[int], used: int) -> bool:
        """
        True when one more unit fits under `limit`, treating None as
        unlimited. Pass one of the *_per_period fields as `limit`.
        """
        return limit is None or used < limit

    def to_public_dict(self) -> dict:
        """
        Safe to serialise to the browser - there is nothing secret in a plan.
        Used by the pricing page and the usage meters.

        Carries BOTH duration numbers, and the pricing page must render
        `effective_max_duration_minutes`. `max_duration_minutes` is what the
        tier was designed to offer; the effective one is what a recording
        actually gets once this deployment's MAX_RECORDING_DURATION_MINUTES is
        applied. Printing the first while the recorder enforces the second is
        how a pricing page comes to state something untrue.
        """
        data = asdict(self)
        data["price_rupees"] = self.price_rupees
        data["effective_max_duration_minutes"] = effective_max_duration_minutes(self)
        return data


FREE_PLAN = Plan(
    id=FREE,
    name="Free",
    tagline="Try MeetIQ on a handful of meetings.",
    price_paise=0,
    per_seat=False,
    meetings_per_period=5,
    max_duration_minutes=45,
    # Deliberately not zero. Ask AI is the thing that makes MeetIQ more than a
    # recorder, and a free user who never feels it has no reason to upgrade -
    # so the free tier is capped on volume, not on capability. Twenty
    # questions costs roughly seven rupees of tokens (see cost_tracker.py).
    ask_ai_questions_per_period=20,
    pdf_export=False,
    calendar_scheduling=False,
    team_workspace=False,
    early_access=False,
)

PRO_PLAN = Plan(
    id=PRO,
    name="Pro",
    tagline="For one person who lives in meetings.",
    price_paise=50_000,  # Rs 500/month
    per_seat=False,
    meetings_per_period=30,
    max_duration_minutes=180,
    ask_ai_questions_per_period=None,
    pdf_export=True,
    calendar_scheduling=True,
    team_workspace=False,
    early_access=False,
)

TEAM_PLAN = Plan(
    id=TEAM,
    name="Team",
    tagline="Ask AI across everyone's meetings, not just your own.",
    price_paise=120_000,  # Rs 1,200 per seat/month
    per_seat=True,
    # "Unlimited (fair use)" on the pricing page. Unlimited here means no
    # per-period counter; the recorder pool's own capacity and the rate
    # limits in config.py are what actually bound a runaway account.
    meetings_per_period=None,
    max_duration_minutes=180,
    ask_ai_questions_per_period=None,
    pdf_export=True,
    calendar_scheduling=True,
    team_workspace=True,
    early_access=True,
)

# Ordered cheapest-first: this is the order the pricing page renders in.
ALL_PLANS = (FREE_PLAN, PRO_PLAN, TEAM_PLAN)

_BY_ID = {plan.id: plan for plan in ALL_PLANS}

# The plans a user can actually buy. Free is not purchasable - it is what you
# have when you have no subscription.
PURCHASABLE_PLAN_IDS = frozenset({PRO, TEAM})


def get_plan(plan_id: Optional[str]) -> Plan:
    """
    The Plan for a users.plan value. Falls back to Free rather than raising:
    an unrecognised or NULL plan must degrade to the free tier, never to an
    unhandled exception on the path that records a meeting.
    """
    return _BY_ID.get(plan_id or FREE, FREE_PLAN)


def is_purchasable(plan_id: str) -> bool:
    return plan_id in PURCHASABLE_PLAN_IDS


def charge_paise(plan: Plan, seats: int = 1) -> int:
    """
    What one period of this plan costs, in paise - the amount handed to
    Razorpay. Seats are ignored for a plan that is not per-seat, so a caller
    cannot accidentally charge five times for Pro.
    """
    if seats < 1:
        raise ValueError("seats must be at least 1")
    return plan.price_paise * seats if plan.per_seat else plan.price_paise


def effective_max_duration_minutes(plan: Plan) -> int:
    """
    The recording length this plan really gets: the smaller of the plan's cap
    and the recorder's hard limit. See the module docstring - the bot enforces
    its own ceiling regardless of what was sold.
    """
    from app.config import settings

    return min(plan.max_duration_minutes, settings.max_recording_duration_minutes)
