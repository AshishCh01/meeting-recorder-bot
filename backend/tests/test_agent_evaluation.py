import pytest
from unittest.mock import patch, MagicMock
from app.rag.chat_service import ask_question
from app.db.models import Meeting, MeetingChunk

@pytest.mark.asyncio
async def test_agent_hallucination_prevention():
    """
    Test that the agent declines to answer when DB returns no data,
    rather than fabricating (hallucinating) an answer.
    """
    mock_db = MagicMock()
    # Meeting found, but no transcript
    mock_meeting = Meeting(id="test_meeting", user_id="test_user", transcript={})
    mock_db.query.return_value.filter.return_value.first.return_value = mock_meeting
    mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    with patch('app.rag.tools.SessionLocal', return_value=mock_db):
        response = await ask_question(
            meeting_id="test_meeting", 
            question="What did the CEO say about the new budget?", 
            user_id="test_user"
        )
        
        answer = response["answer"].lower()
        
        assert any(phrase in answer for phrase in [
            "not available", "not found", "no information", "cannot", "do not have", "no quotes", "not mentioned"
        ]), f"Agent hallucinated or failed to decline gracefully. Response: {response['answer']}"

@pytest.mark.asyncio
async def test_agent_tool_selection():
    """
    Test that the agent accurately selects the 'get_action_items' tool
    when specifically asked about action items.
    """
    mock_db = MagicMock()
    mock_meeting = Meeting(
        id="test_meeting", 
        user_id="test_user", 
        transcript={"action_items": [{"task": "Fix the deployment pipeline"}]}
    )
    mock_db.query.return_value.filter.return_value.first.return_value = mock_meeting

    with patch('app.rag.tools.SessionLocal', return_value=mock_db):
        response = await ask_question(
            meeting_id="test_meeting", 
            question="Can you list all the action items from the meeting?", 
            user_id="test_user"
        )
        
        # Verify the tool was called
        assert "get_action_items" in response["tools_used"], f"Tool not selected. Tools used: {response['tools_used']}"
        
        # Verify the final answer is faithful to the DB's return value
        assert "pipeline" in response["answer"].lower()
