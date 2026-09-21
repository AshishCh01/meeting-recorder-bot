"""
Quota enforcement (app/billing/quota.py) - billing Phase 2.

The cases that matter are the boundaries (the Nth call is allowed, the N+1th
is not), the window arithmetic (usage outside the period must not count), and
the two places this deliberately does *not* trust its inputs: a stale
users.plan, and an expired subscription.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.billing import quota
from app.billing.plans import FREE, FREE_PLAN, PRO, PRO_PLAN, TEAM
from app.config import settings
from app.db.models import AskAiMessage, ChatMessage, Meeting, Subscription, User


def _user(db, email, plan=FREE):
    row = User(email=email, plan=plan)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _subscribe(db, user, plan=PRO, days_left=20, status="active", started_days_ago=10):
    now = datetime.now(timezone.utc)
    row = Subscription(
        user_id=user.id,
        plan=plan,
        status=status,
        current_period_start=now - timedelta(days=started_days_ago),
        current_period_end=now + timedelta(days=days_left),
    )
    db.add(row)
    db.commit()
    return row


def _meetings(db, user, count, created_at=None):
    for i in range(count):
        row = Meeting(
            user_id=user.id,
            meeting_url="https://meet.google.com/abc-defg-h{}".format(i),
            platform="google",
            status="completed",
        )
        if created_at is not None:
            row.created_at = created_at
        db.add(row)
    db.commit()


def _ask_questions(db, user, count, created_at=None, role="user"):
    import uuid

    from app.db.models import AskAiConversation

    convo = AskAiConversation(user_id=user.id, last_message_at=datetime.now(timezone.utc))
    db.add(convo)
    db.commit()
    for i in range(count):
        row = AskAiMessage(
            conversation_id=convo.id, user_id=user.id, role=role, content="q{}".format(i)
        )
        if created_at is not None:
            row.created_at = created_at
        db.add(row)
    db.commit()


# ---------------------------------------------------------------- windows


def test_free_users_get_the_calendar_month_in_utc(db):
    user = _user(db, "window-free@example.com")
    now = datetime(2026, 9, 21, 13, 30, tzinfo=timezone.utc)
    plan, window = quota.resolve(db, str(user.id), now=now)

    assert plan is FREE_PLAN
    assert window.start == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert window.end == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert window.label == "this month"


def test_december_rolls_into_the_next_year(db):
    user = _user(db, "window-dec@example.com")
    now = datetime(2026, 12, 15, tzinfo=timezone.utc)
    _, window = quota.resolve(db, str(user.id), now=now)
    assert window.end == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_paid_users_get_their_billing_period_not_the_month(db):
    user = _user(db, "window-pro@example.com", plan=PRO)
    _subscribe(db, user, plan=PRO, started_days_ago=10, days_left=20)

    plan, window = quota.resolve(db, str(user.id))
    assert plan is PRO_PLAN
    assert window.label == "this billing period"
    # A period that started 10 days ago is not the 1st of the month.
    assert (datetime.now(timezone.utc) - window.start).days == 10


# ------------------------------------------------- not trusting users.plan


def test_a_pro_flag_with_no_subscription_resolves_to_free(db):
    # The cache is wrong - nobody is paying. The tighter plan wins.
    user = _user(db, "liar@example.com", plan=PRO)
    plan, window = quota.resolve(db, str(user.id))
    assert plan is FREE_PLAN
    assert window.label == "this month"


def test_an_expired_subscription_resolves_to_free(db):
    user = _user(db, "expired@example.com", plan=PRO)
    _subscribe(db, user, plan=PRO, days_left=-1, started_days_ago=31)
    plan, _ = quota.resolve(db, str(user.id))
    assert plan is FREE_PLAN


def test_a_cancelled_subscription_resolves_to_free(db):
    user = _user(db, "cancelled@example.com", plan=PRO)
    _subscribe(db, user, plan=PRO, status="expired")
    plan, _ = quota.resolve(db, str(user.id))
    assert plan is FREE_PLAN


def test_the_subscription_wins_when_the_cache_disagrees(db):
    # users.plan says free, the live subscription says team. The subscription
    # is the truth; the mismatch is logged, not enforced.
    user = _user(db, "stale-cache@example.com", plan=FREE)
    _subscribe(db, user, plan=TEAM)
    plan, _ = quota.resolve(db, str(user.id))
    assert plan.id == TEAM


def test_a_missing_user_is_treated_as_free_not_an_error(db):
    import uuid

    plan, window = quota.resolve(db, str(uuid.uuid4()))
    assert plan is FREE_PLAN
    assert window.label == "this month"


# ------------------------------------------------------- meeting counting


def test_meetings_outside_the_window_do_not_count(db):
    user = _user(db, "old-meetings@example.com")
    last_month = datetime.now(timezone.utc) - timedelta(days=45)
    _meetings(db, user, 5, created_at=last_month)

    usage = quota.snapshot(db, str(user.id))
    assert usage.meetings_used == 0
    assert usage.meetings_remaining == 5


def test_another_users_meetings_do_not_count(db):
    mine = _user(db, "mine@example.com")
    theirs = _user(db, "theirs@example.com")
    _meetings(db, theirs, 4)

    usage = quota.snapshot(db, str(mine.id))
    assert usage.meetings_used == 0


def test_failed_meetings_still_count(db):
    # A recorder was dispatched either way - that is the cost being bounded.
    user = _user(db, "failed@example.com")
    db.add(Meeting(
        user_id=user.id, meeting_url="https://meet.google.com/x", platform="google",
        status="failed", error_message="bot died",
    ))
    db.commit()
    assert quota.snapshot(db, str(user.id)).meetings_used == 1


# ---------------------------------------------------- meeting enforcement


def test_free_allows_exactly_five_meetings(db):
    user = _user(db, "five@example.com")
    _meetings(db, user, 4)

    # The fifth is allowed.
    usage = quota.enforce_meeting_quota(db, str(user.id))
    assert usage.meetings_used == 4
    assert usage.meetings_remaining == 1

    _meetings(db, user, 1)

    # The sixth is not.
    with pytest.raises(HTTPException) as exc:
        quota.enforce_meeting_quota(db, str(user.id))
    assert exc.value.status_code == 402


def test_the_refusal_says_what_to_do_and_carries_the_numbers(db):
    user = _user(db, "refused@example.com")
    _meetings(db, user, 5)

    with pytest.raises(HTTPException) as exc:
        quota.enforce_meeting_quota(db, str(user.id))

    # A plain sentence, because the frontend renders `detail` verbatim.
    assert isinstance(exc.value.detail, str)
    assert "5 meetings" in exc.value.detail
    assert "Free" in exc.value.detail
    assert "Upgrade" in exc.value.detail
    # And the machine-readable version, so a meter need not parse English.
    assert exc.value.headers["X-Quota-Resource"] == "meetings"
    assert exc.value.headers["X-Quota-Used"] == "5"
    assert exc.value.headers["X-Quota-Limit"] == "5"


def test_pro_allows_thirty(db):
    user = _user(db, "pro-30@example.com", plan=PRO)
    _subscribe(db, user, plan=PRO)
    _meetings(db, user, 29)
    quota.enforce_meeting_quota(db, str(user.id))  # the 30th is fine

    _meetings(db, user, 1)
    with pytest.raises(HTTPException):
        quota.enforce_meeting_quota(db, str(user.id))


def test_team_is_never_refused_a_meeting(db):
    user = _user(db, "team@example.com", plan=TEAM)
    _subscribe(db, user, plan=TEAM)
    _meetings(db, user, 200)

    usage = quota.enforce_meeting_quota(db, str(user.id))
    # Unlimited must read as None, never as 0 remaining.
    assert usage.meetings_limit is None
    assert usage.meetings_remaining is None


# --------------------------------------------------- AI question counting


def test_both_chat_surfaces_share_one_budget(db):
    """
    The free tier's 20 questions must cover Ask AI *and* the per-meeting chat
    panel - otherwise the cap is bypassed by asking in the other place.
    """
    user = _user(db, "both-surfaces@example.com")
    _ask_questions(db, user, 12)

    meeting = Meeting(user_id=user.id, meeting_url="https://meet.google.com/y", platform="google", status="completed")
    db.add(meeting)
    db.commit()
    for i in range(8):
        db.add(ChatMessage(meeting_id=meeting.id, user_id=user.id, role="user", content="q"))
    db.commit()

    usage = quota.snapshot(db, str(user.id))
    assert usage.ai_questions_used == 20
    assert usage.ai_questions_remaining == 0

    with pytest.raises(HTTPException) as exc:
        quota.enforce_ai_question_quota(db, str(user.id))
    assert exc.value.status_code == 402
    assert exc.value.headers["X-Quota-Resource"] == "ai_questions"


def test_assistant_turns_are_not_counted(db):
    # Counting the answer as well would halve every cap.
    user = _user(db, "assistant-turns@example.com")
    _ask_questions(db, user, 5, role="user")
    _ask_questions(db, user, 5, role="assistant")
    assert quota.snapshot(db, str(user.id)).ai_questions_used == 5


def test_questions_outside_the_window_do_not_count(db):
    user = _user(db, "old-questions@example.com")
    _ask_questions(db, user, 20, created_at=datetime.now(timezone.utc) - timedelta(days=45))
    assert quota.snapshot(db, str(user.id)).ai_questions_used == 0


def test_paid_plans_have_no_question_cap(db):
    user = _user(db, "pro-questions@example.com", plan=PRO)
    _subscribe(db, user, plan=PRO)
    _ask_questions(db, user, 500)

    usage = quota.enforce_ai_question_quota(db, str(user.id))
    assert usage.ai_questions_limit is None
    assert usage.ai_questions_remaining is None


# ------------------------------------------------------------- duration


def test_duration_cap_follows_the_plan_and_the_host_ceiling(db, monkeypatch):
    monkeypatch.setattr(settings, "max_recording_duration_minutes", 90)

    free_user = _user(db, "dur-free@example.com")
    assert quota.resolve_max_duration_minutes(db, str(free_user.id)) == 45

    pro_user = _user(db, "dur-pro@example.com", plan=PRO)
    _subscribe(db, pro_user, plan=PRO)
    # Pro is sold as 180, but this host stops at 90 - the smaller wins.
    assert quota.resolve_max_duration_minutes(db, str(pro_user.id)) == 90

    monkeypatch.setattr(settings, "max_recording_duration_minutes", 240)
    assert quota.resolve_max_duration_minutes(db, str(pro_user.id)) == 180
    assert quota.resolve_max_duration_minutes(db, str(free_user.id)) == 45


def test_a_broken_lookup_never_blocks_a_join(db, monkeypatch):
    """
    The dispatcher calls this while holding a claimed recorder slot. A billing
    question must never be the reason a meeting fails to join.
    """
    monkeypatch.setattr(settings, "max_recording_duration_minutes", 90)

    def boom(*args, **kwargs):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(quota, "resolve", boom)
    assert quota.resolve_max_duration_minutes(db, "whoever") == 90
