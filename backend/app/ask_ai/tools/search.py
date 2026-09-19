"""
Ask AI's semantic tools: find_meetings_by_topic ranks whole meetings by their
summary_embedding, search_across_meetings ranks transcript chunks.

Both follow search_transcript's three steps (rag/tools.py): a short session to
find the candidates, the query embedding with no session open, then a short
session to rank. embed_query is a network call that retries with backoff and
can fall back to Jina, so holding a pooled connection across it would tie one
up for as long as the provider is slow.

Gemini and Jina vectors live in different spaces, so candidates are grouped by
the provider that embedded them (NULL means Gemini: meetings indexed before
the column existed), the query is embedded once per group, each group is
ranked against its own query vector, and the groups are merged by distance.
"""

import logging

from sqlalchemy import func, select

from app.ask_ai.tools.common import clip, owned_completed, title_of
from app.ask_ai.tools.dates import (
    InvalidDate, describe_range, format_local, local_range_to_utc, meeting_date_expr, resolve_tz,
)
from app.config import settings
from app.db.database import SessionLocal
from app.db.models import Meeting, MeetingChunk
from app.services.embedding_service import GEMINI_PROVIDER, embed_query

logger = logging.getLogger(__name__)

SUMMARY_CHARS = 300
QUERY_CHARS = 500

SEARCH_UNAVAILABLE = {
    "note": "Search is temporarily unavailable. Use list_meetings with a date range instead, or try again shortly.",
}


def _provider_expr():
    return func.coalesce(Meeting.embedding_provider, GEMINI_PROVIDER)


def _range_suffix(start_date, end_date) -> str:
    return f" in {describe_range(start_date, end_date)}" if (start_date or end_date) else ""


def _embed_per_provider(query: str, providers, user_id: str) -> dict[str, list[float]] | None:
    """One query vector per provider, or None if a provider failed. No session may be open here."""
    vectors = {}
    for provider in providers:
        try:
            vectors[provider] = embed_query(query, provider=provider, user_id=user_id)
        except Exception as e:
            logger.warning("[ask_ai] query embedding failed (provider=%s): %s", provider, e, exc_info=True)
            return None
    return vectors


def find_meetings_by_topic(
    *,
    user_id: str,
    tz_name: str | None,
    query: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """
    The user's meetings whose title, summary and key points best match
    `query`, best first (ask_ai_topic_top_k of them). Meetings without a
    summary_embedding yet are not ranked; search_across_meetings still
    reaches their transcripts.
    """
    query = (query or "").strip()[:QUERY_CHARS]
    if not query:
        return {"note": "No topic was given. Pass what the meeting should be about as `query`."}
    tz = resolve_tz(tz_name)
    try:
        date_range = local_range_to_utc(start_date, end_date, tz)
    except InvalidDate as e:
        return {"note": str(e)}

    meeting_date = meeting_date_expr()
    provider = _provider_expr()

    def candidates(db):
        q = owned_completed(db.query(Meeting), user_id).filter(Meeting.summary_embedding.isnot(None))
        if date_range:
            q = q.filter(meeting_date >= date_range[0], meeting_date < date_range[1])
        return q

    # 1. Which providers the candidates were embedded with.
    db = SessionLocal()
    try:
        providers = [p for (p,) in candidates(db).with_entities(provider).distinct().all()]
    finally:
        db.close()
    if not providers:
        return {
            "meetings": [],
            "note": (
                f"No meetings found{_range_suffix(start_date, end_date)} to rank by topic. "
                "Try search_across_meetings, or list_meetings for the date range."
            ),
        }

    # 2. The query, embedded once per provider - no session open.
    vectors = _embed_per_provider(query, providers, user_id)
    if vectors is None:
        return SEARCH_UNAVAILABLE

    # 3. Rank each provider's meetings against its own query vector.
    ranked = []
    db = SessionLocal()
    try:
        for name, vector in vectors.items():
            distance = Meeting.summary_embedding.cosine_distance(vector)
            ranked.extend(
                candidates(db)
                .filter(provider == name)
                .with_entities(
                    Meeting.id, Meeting.title, meeting_date.label("meeting_date"),
                    Meeting.transcript["summary"].astext.label("summary"),
                    distance.label("distance"),
                )
                .order_by(distance)
                .limit(settings.ask_ai_topic_top_k)
                .all()
            )
    finally:
        db.close()

    ranked.sort(key=lambda row: row.distance)
    return {
        "meetings": [
            {
                "id": str(row.id),
                "title": title_of(row.title),
                "date": format_local(row.meeting_date, tz),
                "summary": clip(row.summary, SUMMARY_CHARS),
                "relevance": round(1 - float(row.distance), 3),
            }
            for row in ranked[: settings.ask_ai_topic_top_k]
        ],
    }


def search_across_meetings(
    *,
    user_id: str,
    tz_name: str | None,
    query: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """
    The transcript passages that best match `query` across the user's
    meetings (ask_ai_search_top_k of them), each with its meeting's id, title
    and date. Without a date range, only the most recent
    ask_ai_search_candidate_meetings meetings are searched.
    """
    query = (query or "").strip()[:QUERY_CHARS]
    if not query:
        return {"note": "No query was given. Pass what to look for in the transcripts as `query`."}
    tz = resolve_tz(tz_name)
    try:
        date_range = local_range_to_utc(start_date, end_date, tz)
    except InvalidDate as e:
        return {"note": str(e)}

    meeting_date = meeting_date_expr()

    # 1. Candidate meetings, newest first.
    db = SessionLocal()
    try:
        q = owned_completed(db.query(Meeting), user_id)
        if date_range:
            q = q.filter(meeting_date >= date_range[0], meeting_date < date_range[1])
        q = (
            q.with_entities(Meeting.id, Meeting.title, meeting_date.label("meeting_date"), _provider_expr().label("provider"))
            .order_by(meeting_date.desc(), Meeting.id.desc())
        )
        if not date_range:
            q = q.limit(settings.ask_ai_search_candidate_meetings)
        meetings = q.all()
    finally:
        db.close()
    if not meetings:
        return {"matches": [], "note": f"No completed meetings found{_range_suffix(start_date, end_date)}."}

    by_provider: dict[str, list] = {}
    for m in meetings:
        by_provider.setdefault(m.provider, []).append(m.id)
    info = {m.id: m for m in meetings}

    # 2. The query, embedded once per provider - no session open.
    vectors = _embed_per_provider(query, by_provider, user_id)
    if vectors is None:
        return SEARCH_UNAVAILABLE

    # 3. Rank each provider's chunks against its own query vector.
    #
    # The candidates' chunks are selected in a MATERIALIZED CTE first, and
    # only then ordered by distance. Written directly - ORDER BY embedding <=>
    # :q with a meeting_id filter - Postgres can pick the ivfflat index on
    # meeting_chunks.embedding, which searches one of its 100 lists across
    # every user's chunks and filters to these meetings only afterwards: it
    # silently returns fewer than top_k passages and misses the best ones.
    # The CTE's rows have no index, so the ranking is an exact scan over one
    # user's chunks, which is cheap at this scale.
    top_k = settings.ask_ai_search_top_k
    ranked = []
    db = SessionLocal()
    try:
        for name, vector in vectors.items():
            chunks = (
                select(
                    MeetingChunk.meeting_id, MeetingChunk.content, MeetingChunk.speakers,
                    MeetingChunk.timestamp_start, MeetingChunk.embedding,
                )
                .where(MeetingChunk.meeting_id.in_(by_provider[name]))
                .cte("candidate_chunks")
                .prefix_with("MATERIALIZED")
            )
            distance = chunks.c.embedding.cosine_distance(vector).label("distance")
            ranked.extend(db.execute(
                select(chunks.c.meeting_id, chunks.c.content, chunks.c.speakers, chunks.c.timestamp_start, distance)
                .order_by(distance)
                .limit(top_k)
            ).all())
    finally:
        db.close()

    if not ranked:
        return {"matches": [], "note": "None of these meetings have searchable transcript passages yet."}

    ranked.sort(key=lambda row: row.distance)
    return {
        "matches": [
            {
                "meeting_id": str(row.meeting_id),
                "title": title_of(info[row.meeting_id].title),
                "date": format_local(info[row.meeting_id].meeting_date, tz),
                "content": row.content,
                "speakers": row.speakers or [],
                "timestamp_start": row.timestamp_start,
            }
            for row in ranked[:top_k]
        ],
    }
