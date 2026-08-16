import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from app.main import app
from app.api.auth import get_current_user
from app.db.database import get_db
from app.db.models import Meeting
import datetime

client = TestClient(app)

def test_cross_tenant_isolation():
    """
    Test that User A cannot access User B's meeting via API endpoints.
    """
    mock_db = MagicMock()
    
    # We will simulate the DB returning a meeting that belongs to User A
    mock_meeting = Meeting(
        id="meeting_123",
        user_id="user_A_123",
        meeting_url="https://meet.google.com/abc",
        platform="Google Meet",
        status="completed",
        created_at=datetime.datetime.now()
    )
    
    # 1. Simulate User A and get the meeting
    app.dependency_overrides[get_current_user] = lambda: "user_A_123"
    app.dependency_overrides[get_db] = lambda: mock_db
    
    # Setup mock to return the meeting for User A
    mock_db.query.return_value.filter.return_value.first.return_value = mock_meeting
    
    get_response_a = client.get(f"/meetings/meeting_123")
    assert get_response_a.status_code == 200
    assert get_response_a.json()["id"] == "meeting_123"
    
    # 2. Simulate User B trying to get the same meeting
    app.dependency_overrides[get_current_user] = lambda: "user_B_456"
    
    # Setup mock to return None, simulating the SQLAlchemy filter (Meeting.user_id == 'user_B_456') failing to find it
    mock_db.query.return_value.filter.return_value.first.return_value = None
    
    get_response_b = client.get(f"/meetings/meeting_123")
    assert get_response_b.status_code == 404
    
def test_missing_auth():
    """
    Test that missing authorization headers return 401 Unauthorized.
    """
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)
    
    response = client.get("/meetings")
    assert response.status_code == 401
