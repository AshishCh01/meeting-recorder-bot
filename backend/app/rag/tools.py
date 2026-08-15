from google.adk.tools import ToolContext

from app.config import settings
from app.db.supabase import supabase
from app.services.embedding_service import embed_query


def _meeting_id(tool_context: ToolContext) -> str:
    meeting_id = tool_context.state.get("meeting_id")
    if not meeting_id:
        raise ValueError("No meeting_id set on this session - cannot scope the search.")
    return meeting_id


def get_meeting_overview(tool_context: ToolContext) -> dict:
    """
    Returns the high-level summary already generated for this meeting:
    overall summary, key points, action items, and conclusion.

    Use this FIRST for broad questions like "what was this meeting about",
    "what are the action items", or "how did it conclude" - it's cheap and
    usually enough on its own. Fall back to search_transcript only when the
    user asks about specific details, quotes, or moments that this overview
    doesn't cover.
    """
    meeting_id = _meeting_id(tool_context)
    result = (
        supabase.table("meetings")
        .select("transcript, duration_seconds, platform, status")
        .eq("id", meeting_id)
        .single()
        .execute()
    )
    row = result.data or {}
    transcript = row.get("transcript") or {}
    return {
        "summary": transcript.get("summary"),
        "key_points": transcript.get("key_points"),
        "action_items": transcript.get("action_items"),
        "conclusion": transcript.get("conclusion"),
        "duration_seconds": row.get("duration_seconds"),
        "platform": row.get("platform"),
    }


def search_transcript(query: str, tool_context: ToolContext) -> dict:
    """
    Semantically searches this meeting's full transcript for passages
    relevant to `query`, and returns the matching passages with their
    speakers and MM:SS timestamps.

    Use this for specific questions the overview can't answer: who said
    something, what was discussed about a particular topic, exact wording,
    or anything tied to a moment in the conversation. Call it more than once
    with reworded queries if the first results don't fully answer the
    question.

    Args:
        query: A focused natural-language description of what to find,
            e.g. "budget concerns raised about the Q3 launch".
    """
    meeting_id = _meeting_id(tool_context)
    query_embedding = embed_query(query)

    result = supabase.rpc(
        "match_meeting_chunks",
        {
            "query_embedding": query_embedding,
            "match_meeting_id": meeting_id,
            "match_count": settings.retrieval_top_k,
        },
    ).execute()

    hits = result.data or []
    if not hits:
        return {
            "matches": [],
            "note": "No indexed transcript chunks found for this meeting.",
        }

    return {
        "matches": [
            {
                "content": hit["content"],
                "speakers": hit.get("speakers") or [],
                "timestamp_start": hit.get("timestamp_start"),
                "timestamp_end": hit.get("timestamp_end"),
                "similarity": round(hit["similarity"], 3),
            }
            for hit in hits
        ]
    }
