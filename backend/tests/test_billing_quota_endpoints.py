"""
The quota gates as the frontend meets them - billing Phase 2.

test_billing_quota.py proves the arithmetic. This file proves the wiring: that
the gates are actually mounted on the routes that create meetings and answer
questions, that they return 402 rather than 500 or 200, and - the part easiest
to get wrong - that a refused request leaves nothing behind in the database.

Rate limiting is switched off throughout. Both mechanisms return a refusal and
only one of them is under test here; leaving the limiter on would make a 429
from an unrelated budget look like a quota bug.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import auth
from app.billing.plans import PRO
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import AskAiConversation, ChatMessage, Meeting, Subscription, User
from app.main import app

HEADERS = {"Authorization": "Bearer stub-token-identity-is-stubbed"}
MEET_URL = "https://meet.google.com/abc-defg-hij"


@pytest.fixture(autouse=True)
def no_rate_limiting(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)


@pytest.fixture
def signed_in(monkeypatch):
    """One signed-in user, with the JWT check stubbed but the DB half real."""
    user_id = uuid.uuid4()
    db = SessionLocal()
    try:
        db.add(User(id=user_id, email="quota-{}@example.com".format(user_id)))
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        auth, "_identify", lambda token: (str(user_id), "quota-{}@example.com".format(user_id))
    )
    auth.user_row_cache.clear()
    yield str(user_id)
    auth.user_row_cache.clear()


@pytest.fixture
def no_bot(monkeypatch):
    """The recorder is not the subject here - never actually dispatch one."""
    monkeypatch.setattr("app.api.meetings.trigger_bot_join", lambda *a, **k: None)


def _fill_meetings(user_id, count):
    db = SessionLocal()
    try:
        for i in range(count):
            db.add(Meeting(
                user_id=user_id, meeting_url="https://zoom.us/j/{}".format(i),
                platform="zoom", status="completed",
            ))
        db.commit()
    finally:
        db.close()


def _count_meetings(user_id):
    db = SessionLocal()
    try:
        return db.query(Meeting).filter(Meeting.user_id == user_id).count()
    finally:
        db.close()


# ------------------------------------------------------- POST /meetings


def test_the_sixth_meeting_is_refused_with_402(signed_in, no_bot):
    client = TestClient(app)
    body = {"meeting_url": MEET_URL}

    for i in range(5):
        assert client.post("/meetings", json=body, headers=HEADERS).status_code == 200, "create {}".format(i + 1)

    refused = client.post("/meetings", json=body, headers=HEADERS)

    assert refused.status_code == 402
    assert "5 meetings" in refused.json()["detail"]
    assert "Free" in refused.json()["detail"]
    assert refused.headers["X-Quota-Resource"] == "meetings"


def test_a_refused_meeting_leaves_no_row_behind(signed_in, no_bot):
    """
    The gate runs before the insert. If it ever moved below it, a user at
    their cap would accumulate rows that count against the next attempt -
    each refusal making the next one more certain.
    """
    _fill_meetings(signed_in, 5)
    client = TestClient(app)

    assert client.post("/meetings", json={"meeting_url": MEET_URL}, headers=HEADERS).status_code == 402
    assert _count_meetings(signed_in) == 5


def test_a_bad_url_is_still_a_400_when_out_of_quota(signed_in, no_bot):
    """
    Ordering: the URL is validated first. A malformed link is a 400 whatever
    plan you are on, and telling someone to upgrade in order to submit a
    broken URL would be nonsense.
    """
    _fill_meetings(signed_in, 5)
    client = TestClient(app)

    response = client.post("/meetings", json={"meeting_url": "https://example.com/not-a-meeting"}, headers=HEADERS)
    assert response.status_code in (400, 422)


def test_upgrading_lifts_the_cap(signed_in, no_bot):
    from datetime import datetime, timedelta, timezone

    _fill_meetings(signed_in, 5)
    client = TestClient(app)
    assert client.post("/meetings", json={"meeting_url": MEET_URL}, headers=HEADERS).status_code == 402

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(Subscription(
            user_id=signed_in, plan=PRO, status="active",
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=29),
        ))
        db.query(User).filter(User.id == signed_in).update({"plan": PRO})
        db.commit()
    finally:
        db.close()

    assert client.post("/meetings", json={"meeting_url": MEET_URL}, headers=HEADERS).status_code == 200


def test_the_duration_cap_travels_with_the_join(signed_in, monkeypatch):
    """
    The plan's recording length has to reach the recorder, or it is a number
    on a pricing page and nothing else.
    """
    seen = {}
    monkeypatch.setattr(
        "app.api.meetings.trigger_bot_join",
        lambda *a, **k: seen.update(k),
    )
    monkeypatch.setattr(settings, "max_recording_duration_minutes", 90)

    client = TestClient(app)
    assert client.post("/meetings", json={"meeting_url": MEET_URL}, headers=HEADERS).status_code == 200
    assert seen["max_duration_minutes"] == 45  # the free tier's cap


# --------------------------------------------------------- chat surfaces


def _fill_ai_questions(user_id, count):
    """Spend the AI allowance through the per-meeting chat table."""
    db = SessionLocal()
    try:
        meeting = Meeting(
            user_id=user_id, meeting_url=MEET_URL, platform="google", status="completed",
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        for _ in range(count):
            db.add(ChatMessage(meeting_id=meeting.id, user_id=user_id, role="user", content="q"))
        db.commit()
        return str(meeting.id)
    finally:
        db.close()


def test_the_twenty_first_meeting_question_is_refused(signed_in):
    meeting_id = _fill_ai_questions(signed_in, 20)
    client = TestClient(app)

    refused = client.post(
        "/meetings/{}/chat".format(meeting_id),
        json={"question": "what did we decide?"},
        headers=HEADERS,
    )

    assert refused.status_code == 402
    assert refused.headers["X-Quota-Resource"] == "ai_questions"
    assert "20 AI questions" in refused.json()["detail"]


def test_the_stream_transport_shares_the_same_budget(signed_in):
    """
    Two transports must not buy two allowances - the refusal has to be a real
    402, not a 200 stream with an error event the upgrade prompt cannot see.
    """
    meeting_id = _fill_ai_questions(signed_in, 20)
    client = TestClient(app)

    refused = client.post(
        "/meetings/{}/chat/stream".format(meeting_id),
        json={"question": "what did we decide?"},
        headers=HEADERS,
    )
    assert refused.status_code == 402


def test_ask_ai_is_refused_on_the_same_budget(signed_in):
    _fill_ai_questions(signed_in, 20)

    db = SessionLocal()
    try:
        convo = AskAiConversation(user_id=signed_in, title="New chat")
        db.add(convo)
        db.commit()
        db.refresh(convo)
        conversation_id = str(convo.id)
    finally:
        db.close()

    client = TestClient(app)
    refused = client.post(
        "/ask/conversations/{}/stream".format(conversation_id),
        json={"question": "what did we decide?"},
        headers=HEADERS,
    )
    assert refused.status_code == 402


def test_someone_elses_chat_is_still_a_404_not_a_402(signed_in):
    """
    Ordering again: ownership is checked before billing. A 402 on a
    conversation you do not own would confirm that it exists.
    """
    _fill_ai_questions(signed_in, 20)

    other_id = uuid.uuid4()
    db = SessionLocal()
    try:
        db.add(User(id=other_id, email="other-{}@example.com".format(other_id)))
        db.commit()
        convo = AskAiConversation(user_id=other_id, title="Not yours")
        db.add(convo)
        db.commit()
        db.refresh(convo)
        conversation_id = str(convo.id)
    finally:
        db.close()

    client = TestClient(app)
    response = client.post(
        "/ask/conversations/{}/stream".format(conversation_id),
        json={"question": "what did we decide?"},
        headers=HEADERS,
    )
    assert response.status_code == 404
