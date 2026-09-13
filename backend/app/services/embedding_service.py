import logging
import random
import time

from sqlalchemy.orm import Session
from google import genai
from google.genai import types

from app.config import settings
from app.db.models import Meeting, MeetingChunk
from app.services.embedding_fallback_jina import embed_documents_with_jina, embed_query_with_jina
from app.services.cost_tracker import estimate_tokens, gemini_embedding_cost, jina_embedding_cost, record_usage
from app.services.gemini_errors import TRANSIENT_EXCEPTIONS, error_code_str, is_transient

# Initialize the Gemini GenAI client
client = genai.Client(api_key=settings.gemini_api_key, http_options=types.HttpOptions(timeout=60_000))

GEMINI_PROVIDER = "gemini"
JINA_PROVIDER = "jina"

# Indexing runs inside a transcription and query embedding inside a chat turn,
# both of which bind meeting_id/user_id as log fields (Phase B5) - nothing to
# add here.
logger = logging.getLogger(__name__)


def _build_chunks(conversation: list[dict]) -> list[dict]:
    """
    Groups consecutive conversation segments into overlapping windows based on token count
    using a character-based heuristic (1 token ~= 4 chars). 
    Target: ~500 tokens per chunk with ~50 tokens overlap.
    We preserve entire conversation segments to keep timestamps/speakers cleanly mapped.
    """
    if not conversation:
        return []

    TARGET_TOKENS = settings.chunk_target_tokens
    OVERLAP_TOKENS = settings.chunk_overlap_tokens

    chunks = []
    chunk_index = 0
    start_idx = 0

    while start_idx < len(conversation):
        current_tokens = 0
        end_idx = start_idx
        
        # Grow the window until we hit TARGET_TOKENS or run out of segments
        while end_idx < len(conversation):
            seg_text = conversation[end_idx].get("text", "")
            # Heuristic: 1 token is roughly 4 characters
            seg_tokens = len(seg_text) // 4
            
            if current_tokens + seg_tokens > TARGET_TOKENS and end_idx > start_idx:
                break
                
            current_tokens += seg_tokens
            end_idx += 1

        window = conversation[start_idx:end_idx]
        
        content = "\n".join(seg.get("text", "") for seg in window)
        speakers = sorted({
            seg["text"].split(" - ", 1)[0].strip()
            for seg in window
            if " - " in seg.get("text", "")
        })

        chunks.append({
            "chunk_index": chunk_index,
            "content": content,
            "speakers": speakers,
            "timestamp_start": window[0].get("timestamp_start"),
            "timestamp_end": window[-1].get("timestamp_end"),
        })
        
        chunk_index += 1

        if end_idx >= len(conversation):
            break
            
        # Calculate overlap for the next chunk
        overlap_tokens = 0
        overlap_idx = end_idx - 1
        
        while overlap_idx > start_idx:
            seg_text = conversation[overlap_idx].get("text", "")
            seg_tokens = len(seg_text) // 4
            if overlap_tokens + seg_tokens > OVERLAP_TOKENS:
                break
            overlap_tokens += seg_tokens
            overlap_idx -= 1
            
        if overlap_idx <= start_idx:
            start_idx = start_idx + 1
        else:
            start_idx = overlap_idx

    return chunks


def _call_gemini_embed_with_retry(contents, max_retries: int = 4):
    """
    Calls Gemini's embed_content with exponential backoff retry for
    transient errors (429 rate limit, 503 overloaded) - mirrors
    _call_gemini_with_retry in transcription_service.py. Re-raises the
    last error if all retries are exhausted, or immediately for
    non-retriable errors (e.g. 400, 401) - retrying those is pointless.

    The backoff below uses a blocking time.sleep, which is correct here
    and must not be changed to asyncio.sleep: this function is only ever
    reached from a worker thread, never from the event loop. Both callers
    are synchronous - index_transcript runs inside transcription_service's
    ThreadPoolExecutor (no event loop in that thread at all, so
    asyncio.sleep would raise), and embed_query is invoked from the chat
    path via asyncio.to_thread in rag/chat_service.py. Blocking a worker
    thread is exactly what those threads are for; blocking the event loop
    is what we're avoiding.
    """
    for attempt in range(max_retries):
        try:
            return client.models.embed_content(
                model=settings.gemini_embedding_model,
                contents=contents,
                config=types.EmbedContentConfig(output_dimensionality=settings.embedding_dimensions),
            )
        except TRANSIENT_EXCEPTIONS as e:
            retriable = is_transient(e)
            code_str = error_code_str(e)
            if not retriable or attempt == max_retries - 1:
                raise
            delay = (2 ** attempt) + random.uniform(0, 1)
            logger.warning(
                "[embedding] Gemini %s, retrying in %.1fs (attempt %s/%s)",
                code_str, delay, attempt + 1, max_retries,
            )
            time.sleep(delay)


def _embed_documents(texts: list[str]) -> tuple[list[list[float]], str]:
    """
    Embeds document chunks for indexing. Tries Gemini first (with
    retry); if Gemini is still failing with 429/503 once retries are
    exhausted, falls back to Jina (if configured). Returns the vectors
    together with whichever provider actually produced them, so the
    caller can record it on the Meeting row for embed_query to match
    later - Gemini and Jina vectors are not comparable to each other.
    """
    if not texts:
        return [], GEMINI_PROVIDER

    try:
        response = _call_gemini_embed_with_retry(texts)
        return [e.values for e in response.embeddings], GEMINI_PROVIDER
    except TRANSIENT_EXCEPTIONS as e:
        is_retriable = is_transient(e)
        code_str = error_code_str(e)
        if is_retriable and settings.jina_api_key:
            logger.warning("[embedding] Gemini %s persisted after retries, falling back to Jina AI", code_str)
            return embed_documents_with_jina(texts), JINA_PROVIDER
        raise


def embed_query(query: str, provider: str = GEMINI_PROVIDER, *, meeting_id=None, user_id=None) -> list[float]:
    """
    Embeds a chat query. `provider` MUST match whichever model indexed
    the meeting being queried (Meeting.embedding_provider) - Gemini and
    Jina embeddings live in different, incompatible vector spaces, so
    mixing them wouldn't error, it would just silently return wrong
    nearest-neighbor results. Callers should read the meeting's stored
    provider and pass it through rather than assume Gemini.

    `meeting_id`/`user_id` only attribute the usage row (Phase B4). The row
    is written with a session of its own, so this must be called with no
    session open - which search_transcript already guarantees.
    """
    if provider == JINA_PROVIDER:
        vector = embed_query_with_jina(query)
        _record_query_usage(query, JINA_PROVIDER, "ok", meeting_id, user_id)
        return vector

    try:
        response = _call_gemini_embed_with_retry(query)
        vector = response.embeddings[0].values
        _record_query_usage(query, GEMINI_PROVIDER, "ok", meeting_id, user_id)
        return vector
    except TRANSIENT_EXCEPTIONS as e:
        is_retriable = is_transient(e)
        code_str = error_code_str(e)
        if is_retriable and settings.jina_api_key:
            logger.warning("[embedding] Gemini %s persisted after retries, falling back to Jina AI for query", code_str)
            vector = embed_query_with_jina(query)
            _record_query_usage(query, JINA_PROVIDER, "fallback", meeting_id, user_id)
            return vector
        raise


def _model_for(provider: str) -> str:
    return settings.jina_embedding_model if provider == JINA_PROVIDER else settings.gemini_embedding_model


def _embedding_cost(provider: str, tokens: int) -> float:
    return jina_embedding_cost(tokens) if provider == JINA_PROVIDER else gemini_embedding_cost(tokens)


def _record_query_usage(query: str, provider: str, outcome: str, meeting_id, user_id) -> None:
    # Neither provider's response is read for usage here, so the count is the
    # same ~4 chars/token heuristic indexing uses, and flagged as such.
    tokens = estimate_tokens(len(query))
    record_usage(
        operation="query_embedding",
        provider=provider,
        model=_model_for(provider),
        outcome=outcome,
        user_id=user_id,
        meeting_id=meeting_id,
        input_tokens=tokens,
        usd=_embedding_cost(provider, tokens),
        estimated=True,
    )


def index_transcript(db: Session, meeting_id: str, transcript: dict) -> float:
    """
    Chunks the `conversation` array of a completed transcript, embeds each
    chunk, and (re)writes them to `meeting_chunks` so the RAG agent can query
    this meeting. Safe to call again for the same meeting (old chunks are
    cleared first). Also records which provider embedded the chunks on
    the Meeting row, so embed_query can use a matching model later even
    if Gemini and Jina alternate between re-indexing runs.

    Returns the estimated USD cost of this indexing run (0.0 if there was
    nothing to embed), so callers can fold it into a per-meeting total.
    """
    conversation = transcript.get("conversation") or []
    if not conversation:
        return 0.0

    chunks = _build_chunks(conversation)
    if not chunks:
        return 0.0

    embeddings, provider = _embed_documents([c["content"] for c in chunks])

    # The Gemini embed API doesn't return usage_metadata, so we estimate
    # token count with the same ~4-chars-per-token heuristic used for
    # chunking above - and store it flagged as an estimate.
    token_estimate = estimate_tokens(sum(len(c["content"]) for c in chunks))
    cost = _embedding_cost(provider, token_estimate)
    owner_id = db.query(Meeting.user_id).filter(Meeting.id == meeting_id).scalar()
    # Staged in this session, not written on a connection of its own: this
    # runs inside a worker job that already holds one (cost_tracker.py). It is
    # committed with the chunks below, so it is recorded exactly when the
    # index it paid for is.
    record_usage(
        operation="embedding",
        provider=provider,
        model=_model_for(provider),
        outcome="ok" if provider == GEMINI_PROVIDER else "fallback",
        user_id=owner_id,
        meeting_id=meeting_id,
        input_tokens=token_estimate,
        usd=cost,
        estimated=True,
        db=db,
    )

    # Delete existing chunks for this meeting.
    #
    # Deliberately NOT committed here. The delete and the insert below have to
    # land in one transaction: a process that dies between them - an arq
    # shutdown, a job timeout, an OOM kill, a dropped DB connection - would
    # otherwise leave the meeting with zero chunks. That state is invisible,
    # because transcribe_recording sets status "completed" before calling
    # here: the UI shows a finished meeting with a transcript and summary
    # while RAG returns nothing and chat answers every question with "that
    # isn't in the transcript". Nothing errors, nobody is paged. Committing
    # only once, below, means an interrupted re-index rolls back and the
    # meeting keeps the chunks it already had.
    db.query(MeetingChunk).filter(MeetingChunk.meeting_id == meeting_id).delete()

    # Insert new chunks
    meeting_chunks = [
        MeetingChunk(
            meeting_id=meeting_id,
            chunk_index=chunk["chunk_index"],
            content=chunk["content"],
            speakers=chunk["speakers"],
            timestamp_start=chunk["timestamp_start"],
            timestamp_end=chunk["timestamp_end"],
            embedding=embedding,
        )
        for chunk, embedding in zip(chunks, embeddings)
    ]

    db.add_all(meeting_chunks)

    meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
    if meeting:
        meeting.embedding_provider = provider

    db.commit()
    return cost