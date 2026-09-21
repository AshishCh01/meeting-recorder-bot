"""
The plan catalogue (app/billing/plans.py) and the Phase 1 billing schema.

These are the invariants the later phases assume. The interesting ones are
the two that a plausible refactor breaks silently: `None` meaning unlimited
rather than zero, and money staying an integer count of paise.
"""
import pytest

from app.billing import plans
from app.billing.plans import (
    ALL_PLANS,
    FREE,
    FREE_PLAN,
    PRO,
    PRO_PLAN,
    TEAM,
    TEAM_PLAN,
    charge_paise,
    effective_max_duration_minutes,
    get_plan,
    is_purchasable,
)
from app.config import settings
from app.db.models import Payment, Subscription, User


def test_prices_are_the_agreed_ones_in_paise():
    assert FREE_PLAN.price_paise == 0
    assert PRO_PLAN.price_paise == 50_000
    assert TEAM_PLAN.price_paise == 120_000
    assert PRO_PLAN.price_rupees == 500
    assert TEAM_PLAN.price_rupees == 1_200


def test_every_price_is_an_int_not_a_float():
    # A float price would eventually round a real charge. Guard the type, not
    # just the value.
    for plan in ALL_PLANS:
        assert isinstance(plan.price_paise, int)
        assert not isinstance(plan.price_paise, bool)


def test_agreed_tier_limits():
    assert FREE_PLAN.meetings_per_period == 5
    assert FREE_PLAN.max_duration_minutes == 45
    assert FREE_PLAN.ask_ai_questions_per_period == 20

    assert PRO_PLAN.meetings_per_period == 30
    assert PRO_PLAN.max_duration_minutes == 180
    assert PRO_PLAN.ask_ai_questions_per_period is None

    assert TEAM_PLAN.meetings_per_period is None
    assert TEAM_PLAN.ask_ai_questions_per_period is None


def test_none_means_unlimited_not_zero():
    # The whole point of allows(): a None limit must never read as "0 left".
    assert PRO_PLAN.allows(PRO_PLAN.ask_ai_questions_per_period, used=10_000)
    assert TEAM_PLAN.allows(TEAM_PLAN.meetings_per_period, used=10_000)


def test_allows_blocks_at_the_limit_not_after_it():
    # 5 meetings means the 6th is refused, so used=5 is already full.
    assert FREE_PLAN.allows(FREE_PLAN.meetings_per_period, used=4)
    assert not FREE_PLAN.allows(FREE_PLAN.meetings_per_period, used=5)
    assert not FREE_PLAN.allows(FREE_PLAN.meetings_per_period, used=6)


def test_feature_gates_match_the_agreed_table():
    assert (FREE_PLAN.pdf_export, FREE_PLAN.calendar_scheduling, FREE_PLAN.team_workspace) == (False, False, False)
    assert (PRO_PLAN.pdf_export, PRO_PLAN.calendar_scheduling) == (True, True)
    assert PRO_PLAN.team_workspace is False
    assert (TEAM_PLAN.pdf_export, TEAM_PLAN.calendar_scheduling, TEAM_PLAN.team_workspace) == (True, True, True)


def test_get_plan_degrades_to_free_rather_than_raising():
    # Recording a meeting must not 500 because users.plan holds something
    # unexpected.
    assert get_plan(PRO) is PRO_PLAN
    assert get_plan(None) is FREE_PLAN
    assert get_plan("") is FREE_PLAN
    assert get_plan("enterprise-that-never-shipped") is FREE_PLAN


def test_free_is_not_purchasable():
    assert not is_purchasable(FREE)
    assert is_purchasable(PRO)
    assert is_purchasable(TEAM)


def test_charge_ignores_seats_for_a_flat_plan():
    # Guards against charging Pro five times because a seats field leaked in.
    assert charge_paise(PRO_PLAN, seats=5) == 50_000
    assert charge_paise(TEAM_PLAN, seats=5) == 600_000
    assert charge_paise(TEAM_PLAN) == 120_000


def test_charge_rejects_nonsense_seat_counts():
    with pytest.raises(ValueError):
        charge_paise(TEAM_PLAN, seats=0)


def test_effective_duration_never_exceeds_the_recorder_hard_cap(monkeypatch):
    # Pro is sold as 3 hours, but the bot stops at
    # max_recording_duration_minutes whatever the plan says.
    monkeypatch.setattr(settings, "max_recording_duration_minutes", 90)
    assert effective_max_duration_minutes(PRO_PLAN) == 90
    assert effective_max_duration_minutes(FREE_PLAN) == 45  # plan cap is lower

    monkeypatch.setattr(settings, "max_recording_duration_minutes", 240)
    assert effective_max_duration_minutes(PRO_PLAN) == 180  # plan cap now binds


def test_public_dict_is_serialisable_and_carries_rupees():
    data = PRO_PLAN.to_public_dict()
    assert data["id"] == PRO
    assert data["price_rupees"] == 500
    assert data["ask_ai_questions_per_period"] is None
    # Nothing secret ever belongs in a plan.
    assert not any("secret" in key or "key" in key for key in data)


def test_plan_ids_are_unique_and_ordered_cheapest_first():
    ids = [plan.id for plan in ALL_PLANS]
    assert ids == [FREE, PRO, TEAM]
    assert len(set(ids)) == len(ids)
    prices = [plan.price_paise for plan in ALL_PLANS]
    assert prices == sorted(prices)


def test_users_default_to_free(db):
    user = User(email="plan-default@example.com")
    db.add(user)
    db.commit()
    db.refresh(user)
    # NOT NULL with a server default: a user who never bought anything is
    # free, not unknown.
    assert user.plan == FREE


def test_one_subscription_per_user(db):
    from datetime import datetime, timedelta, timezone

    user = User(email="one-sub@example.com")
    db.add(user)
    db.commit()

    now = datetime.now(timezone.utc)
    db.add(Subscription(
        user_id=user.id, plan=PRO, current_period_end=now + timedelta(days=30),
    ))
    db.commit()

    db.add(Subscription(
        user_id=user.id, plan=TEAM, current_period_end=now + timedelta(days=30),
    ))
    with pytest.raises(Exception):
        db.commit()
    db.rollback()


def test_duplicate_razorpay_order_is_refused_by_the_database(db):
    """
    The unique order id is what makes Phase 3's webhook idempotent - a
    replayed callback must hit a constraint, not create a second charge.
    """
    import uuid

    user_id = uuid.uuid4()
    db.add(Payment(
        user_id=user_id, plan=PRO, amount_paise=50_000,
        razorpay_order_id="order_DEMO123",
    ))
    db.commit()

    db.add(Payment(
        user_id=user_id, plan=PRO, amount_paise=50_000,
        razorpay_order_id="order_DEMO123",
    ))
    with pytest.raises(Exception):
        db.commit()
    db.rollback()


def test_billing_is_disabled_without_a_full_key_pair(monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_id", "")
    monkeypatch.setattr(settings, "razorpay_key_secret", "")
    assert not settings.billing_enabled

    # An id with no secret can open Checkout but cannot verify the result.
    # That is worse than off, so it must not count as enabled.
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_abc123")
    assert not settings.billing_enabled

    monkeypatch.setattr(settings, "razorpay_key_secret", "shhh")
    assert settings.billing_enabled
    assert settings.razorpay_is_test_mode


def test_test_mode_is_read_off_the_key_itself(monkeypatch):
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_live_abc123")
    assert not settings.razorpay_is_test_mode
