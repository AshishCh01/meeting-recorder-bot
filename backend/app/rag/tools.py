from google.adk.tools import ToolContext
from sqlalchemy import func

from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting, MeetingChunk
from app.services.embedding_service import embed_query


def _get_context(tool_context: ToolContext) -> tuple[str, str]:
    meeting_id = tool_context.state.get("meeting_id")
    user_id = tool_context.state.get("user_id")
    if not meeting_id or not user_id:
        raise ValueError("Missing meeting_id or user_id in ADK context.")
    return meeting_id, user_id


def get_meeting_summary(tool_context: ToolContext) -> dict:
    """
    Returns the high-level summary of the meeting, key points discussed, 
    and the final conclusion/resolution.

    Use this FIRST for broad questions like "what was this meeting about",
    "what were the main topics", or "how did it conclude".
    """
    meeting_id, user_id = _get_context(tool_context)
    
    db = SessionLocal()
    try:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
        if not meeting or not meeting.transcript:
            return {}
            
        transcript = meeting.transcript
        return {
            "summary": transcript.get("summary"),
            "key_points": transcript.get("key_points"),
            "conclusion": transcript.get("conclusion"),
            "duration_seconds": meeting.duration_seconds,
            "platform": meeting.platform,
        }
    finally:
        db.close()


def get_action_items(tool_context: ToolContext) -> list[dict]:
    """
    Returns the concrete tasks, decisions, or follow-ups mentioned in the meeting,
    including the owner and timestamp if available.

    Use this specifically when asked about action items, tasks, or follow-ups.
    """
    meeting_id, user_id = _get_context(tool_context)
    
    db = SessionLocal()
    try:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
        if not meeting or not meeting.transcript:
            return []
            
        return meeting.transcript.get("action_items", [])
    finally:
        db.close()


def search_by_speaker(speaker_name: str, tool_context: ToolContext) -> dict:
    """
    Searches the transcript for everything said by a specific person.

    Use this when the user asks "what did Alice say", "find quotes by Bob",
    or "did John mention anything?".

    Args:
        speaker_name: The name of the speaker to search for.
    """
    meeting_id, user_id = _get_context(tool_context)
    
    db = SessionLocal()
    try:
        # Check if meeting belongs to user first to prevent unauthorized cross-joins implicitly
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
        if not meeting:
            return {"note": "Meeting not found or unauthorized."}

        # PostgreSQL array match: ANY(speakers) ILIKE %speaker_name%
        # Or simply search if speaker_name is contained in the string representation
        chunks = (
            db.query(MeetingChunk)
            .filter(
                MeetingChunk.meeting_id == meeting_id,
                func.array_to_string(MeetingChunk.speakers, ',').ilike(f"%{speaker_name}%")
            )
            .order_by(MeetingChunk.chunk_index)
            .all()
        )
        
        if not chunks:
            return {
                "matches": [],
                "note": f"No quotes found for speaker: {speaker_name}.",
            }

        return {
            "matches": [
                {
                    "content": chunk.content,
                    "speakers": chunk.speakers or [],
                    "timestamp_start": chunk.timestamp_start,
                    "timestamp_end": chunk.timestamp_end,
                }
                for chunk in chunks
            ]
        }
    finally:
        db.close()


def search_transcript(query: str, tool_context: ToolContext) -> dict:
    """
    Semantically searches this meeting's full transcript for passages
    relevant to `query`, and returns the matching passages with their
    speakers and MM:SS timestamps.

    Use this for specific questions the summary can't answer: exact wording,
    or anything tied to a specific moment or topic in the conversation.
    Call it more than once with reworded queries if the first results don't
    fully answer the question.

    Args:
        query: A focused natural-language description of what to find,
            e.g. "budget concerns raised about the Q3 launch".
    """
    meeting_id, user_id = _get_context(tool_context)
    if not query or not query.strip():
        return {"note": "No query provided."}
        
    query_embedding = embed_query(query)

    db = SessionLocal()
    try:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
        if not meeting:
            return {"note": "Meeting not found or unauthorized."}

        chunks = (
            db.query(MeetingChunk)
            .filter(MeetingChunk.meeting_id == meeting_id)
            .order_by(MeetingChunk.embedding.cosine_distance(query_embedding))
            .limit(settings.retrieval_top_k)
            .all()
        )
        
        if not chunks:
            return {
                "matches": [],
                "note": "No indexed transcript chunks found for this meeting.",
            }

        return {
            "matches": [
                {
                    "content": chunk.content,
                    "speakers": chunk.speakers or [],
                    "timestamp_start": chunk.timestamp_start,
                    "timestamp_end": chunk.timestamp_end,
                }
                for chunk in chunks
            ]
        }
    finally:
        db.close()

def search_transcript(query: str, tool_context: ToolContext) -> dict:
    """
    Semantically searches this meeting's full transcript for passages
    relevant to `query`, and returns the matching passages with their
    speakers and MM:SS timestamps.

    Use this for specific questions the summary can't answer: exact wording,
    or anything tied to a specific moment or topic in the conversation.
    Call it more than once with reworded queries if the first results don't
    fully answer the question.

    Args:
        query: A focused natural-language description of what to find,
            e.g. "budget concerns raised about the Q3 launch".
    """
    meeting_id, user_id = _get_context(tool_context)
    if not query or not query.strip():
        return {"note": "No query provided."}

    db = SessionLocal()
    try:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id, Meeting.user_id == user_id).first()
        if not meeting:
            return {"note": "Meeting not found or unauthorized."}

        # Must match whichever provider embedded this meeting's chunks
        # (older meetings indexed before this column existed default to
        # "gemini", since that's all that existed then).
        query_embedding = embed_query(query, provider=meeting.embedding_provider or "gemini")

        chunks = (
            db.query(MeetingChunk)
            .filter(MeetingChunk.meeting_id == meeting_id)
            .order_by(MeetingChunk.embedding.cosine_distance(query_embedding))
            .limit(settings.retrieval_top_k)
            .all()
        )
        
        if not chunks:
            return {
                "matches": [],
                "note": "No indexed transcript chunks found for this meeting.",
            }

        return {
            "matches": [
                {
                    "content": chunk.content,
                    "speakers": chunk.speakers or [],
                    "timestamp_start": chunk.timestamp_start,
                    "timestamp_end": chunk.timestamp_end,
                }
                for chunk in chunks
            ]
        }
    finally:
        db.close()