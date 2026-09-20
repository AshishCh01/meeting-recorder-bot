"""
Feature gates - billing Phase 4.

Quotas count; these do not. A feature gate is a single boolean on the plan,
and the things worth pinning are the edges: that a typo'd feature name fails
loudly, that ownership is still checked first, and that the two calendar
routes which must stay open on every plan did stay open.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import auth
from app.billing import quota
from app.billing.plans import FREE, PRO, TEAM
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import CalendarConnection, Meeting, Subscription, User
from app.main import app

HEADERS = {"Authorization": "Bearer stub-token-identity-is-stubbed"}


@pytest.fixture(autouse=True)
def no_rate_limiting(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_enabled", False)


@pytest.fixture
def signed_in(monkeypatch):
    user_id = uuid.uuid4()
    db = SessionLocal()
    try:
        db.add(User(id=user_id, email="feat-{}@example.com".format(user_id)))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(
        auth, "_identify", lambda token: (str(user_id), "feat-{}@example.com".format(user_id))
    )
    auth.user_row_cache.clear()
    yield str(user_id)
    auth.user_row_cache.clear()


def _upgrade(user_id, plan=PRO):
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        db.add(Subscription(
            user_id=user_id, plan=plan, status="active",
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=29),
        ))
        db.query(User).filter(User.id == user_id).update({"plan": plan})
        db.commit()
    finally:
        db.close()


def _completed_meeting(user_id):
    db = SessionLocal()
    try:
        meeting = Meeting(
            user_id=user_id, meeting_url="https://meet.google.com/abc-defg-hij",
            platform="google", status="completed",
            transcript={"summary": "We agreed to ship on Friday.", "conversation": []},
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        return str(meeting.id)
    finally:
        db.close()


# ---------------------------------------------------------- the helper


def test_an_unknown_feature_name_raises_rather_than_denying(db):
    """
    A typo must fail loudly here. Silently reading as False would lock
    everyone out of a feature they paid for, and nothing would point at why.
    """
    user = User(email="typo@example.com")
    db.add(user)
    db.commit()

    with pytest.raises(ValueError):
        quota.require_feature(db, str(user.id), "pdf_exprot")


def test_the_gate_follows_the_plan(db):
    free_user = User(email="gate-free@example.com")
    db.add(free_user)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        quota.require_feature(db, str(free_user.id), "pdf_export")
    assert exc.value.status_code == 402
    assert "PDF export" in exc.value.detail
    assert exc.value.headers["X-Quota-Resource"] == "pdf_export"

    pro_user = User(email="gate-pro@example.com", plan=PRO)
    db.add(pro_user)
    db.commit()
    now = datetime.now(timezone.utc)
    db.add(Subscription(
        user_id=pro_user.id, plan=PRO, status="active",
        current_period_start=now, current_period_end=now + timedelta(days=30),
    ))
    db.commit()

    assert quota.require_feature(db, str(pro_user.id), "pdf_export").id == PRO


def test_team_workspace_is_team_only(db):
    pro_user = User(email="ws-pro@example.com", plan=PRO)
    db.add(pro_user)
    db.commit()
    now = datetime.now(timezone.utc)
    db.add(Subscription(
        user_id=pro_user.id, plan=PRO, status="active",
        current_period_start=now, current_period_end=now + timedelta(days=30),
    ))
    db.commit()

    # Pro buys PDF and scheduling, but not the workspace.
    quota.require_feature(db, str(pro_user.id), "calendar_scheduling")
    with pytest.raises(HTTPException):
        quota.require_feature(db, str(pro_user.id), "team_workspace")


def test_an_expired_subscription_loses_the_feature_immediately(db):
    """
    Not "until the expiry sweep runs" - the gate resolves through the same
    resolve() the quotas use, so the feature goes at the same moment.
    """
    user = User(email="gate-expired@example.com", plan=PRO)
    db.add(user)
    db.commit()
    now = datetime.now(timezone.utc)
    db.add(Subscription(
        user_id=user.id, plan=PRO, status="active",
        current_period_start=now - timedelta(days=31),
        current_period_end=now - timedelta(minutes=1),
    ))
    db.commit()

    with pytest.raises(HTTPException):
        quota.require_feature(db, str(user.id), "pdf_export")


# ------------------------------------------------------------ PDF export


def test_pdf_export_is_refused_on_free(signed_in):
    meeting_id = _completed_meeting(signed_in)
    response = TestClient(app).get("/meetings/{}/export-pdf".format(meeting_id), headers=HEADERS)

    assert response.status_code == 402
    assert response.headers["X-Quota-Resource"] == "pdf_export"


def test_pdf_export_works_on_pro(signed_in):
    meeting_id = _completed_meeting(signed_in)
    _upgrade(signed_in)

    response = TestClient(app).get("/meetings/{}/export-pdf".format(meeting_id), headers=HEADERS)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:4] == b"%PDF"


def test_someone_elses_meeting_is_a_404_not_a_402(signed_in):
    """
    Ownership before billing. A 402 on a meeting you do not own would confirm
    that it exists.
    """
    other_id = uuid.uuid4()
    db = SessionLocal()
    try:
        db.add(User(id=other_id, email="other-{}@example.com".format(other_id)))
        db.commit()
    finally:
        db.close()
    meeting_id = _completed_meeting(str(other_id))

    response = TestClient(app).get("/meetings/{}/export-pdf".format(meeting_id), headers=HEADERS)
    assert response.status_code == 404


def test_the_gate_does_not_depend_on_the_transcript_being_ready(signed_in):
    """
    "Can I export at all" is a plan question, and must not be answered
    differently because this particular meeting is still transcribing.
    """
    db = SessionLocal()
    try:
        meeting = Meeting(
            user_id=signed_in, meeting_url="https://zoom.us/j/1",
            platform="zoom", status="transcribing", transcript=None,
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        meeting_id = str(meeting.id)
    finally:
        db.close()

    # Free: a billing answer, not "not ready".
    assert TestClient(app).get(
        "/meetings/{}/export-pdf".format(meeting_id), headers=HEADERS
    ).status_code == 402

    # Pro: now the real 400.
    _upgrade(signed_in)
    assert TestClient(app).get(
        "/meetings/{}/export-pdf".format(meeting_id), headers=HEADERS
    ).status_code == 400


# --------------------------------------------------- calendar scheduling


def test_calendar_routes_are_refused_on_free(signed_in):
    client = TestClient(app)

    assert client.get("/calendar/connect", headers=HEADERS).status_code == 402
    assert client.get("/calendar/events", headers=HEADERS).status_code == 402
    assert client.post("/calendar/events/evt_123/schedule", headers=HEADERS).status_code == 402


def test_status_and_disconnect_stay_open_on_every_plan(signed_in):
    """
    The one deliberate hole. A user who downgrades still has a Google account
    connected to this service and must be able to see that and revoke it -
    gating these would leave them connected with no way out.
    """
    db = SessionLocal()
    try:
        db.add(CalendarConnection(
            user_id=signed_in, google_email="someone@gmail.com",
            refresh_token_encrypted="not-a-real-token",
        ))
        db.commit()
    finally:
        db.close()

    client = TestClient(app)

    status = client.get("/calendar/status", headers=HEADERS)
    assert status.status_code == 200
    assert status.json()["connected"] is True

    assert client.delete("/calendar/disconnect", headers=HEADERS).status_code == 200

    db = SessionLocal()
    try:
        assert db.query(CalendarConnection).filter(
            CalendarConnection.user_id == signed_in
        ).first() is None
    finally:
        db.close()


def test_scheduling_is_reachable_on_pro(signed_in):
    """
    Past the billing gate. It then fails on the calendar not being connected -
    a 409, which is the point: the plan is no longer what stops it.
    """
    _upgrade(signed_in)
    response = TestClient(app).post("/calendar/events/evt_123/schedule", headers=HEADERS)
    assert response.status_code != 402
    assert response.status_code in (409, 503)


def test_team_gets_the_paid_features_too(signed_in):
    meeting_id = _completed_meeting(signed_in)
    _upgrade(signed_in, plan=TEAM)

    client = TestClient(app)
    assert client.get("/meetings/{}/export-pdf".format(meeting_id), headers=HEADERS).status_code == 200
    assert client.get("/calendar/events", headers=HEADERS).status_code != 402
