from google import genai
from google.genai import types

from app.config import settings
from app.db.supabase import supabase

client = genai.Client(api_key=settings.gemini_api_key)


def _build_chunks(conversation: list[dict]) -> list[dict]:
    """
    Groups consecutive conversation segments into overlapping windows so each
    embedded chunk has enough context to be useful on its own, while still
    keeping timestamps and speakers attached.
    """
    size = settings.chunk_segments
    overlap = min(settings.chunk_overlap, size - 1)
    step = max(size - overlap, 1)

    chunks = []
    for start in range(0, len(conversation), step):
        window = conversation[start:start + size]
        if not window:
            continue

        content = "\n".join(seg.get("text", "") for seg in window)
        speakers = sorted({
            seg["text"].split(" - ", 1)[0].strip()
            for seg in window
            if " - " in seg.get("text", "")
        })

        chunks.append({
            "chunk_index": start // step,
            "content": content,
            "speakers": speakers,
            "timestamp_start": window[0].get("timestamp_start"),
            "timestamp_end": window[-1].get("timestamp_end"),
        })

        if start + size >= len(conversation):
            break

    return chunks


def _embed_documents(texts: list[str]) -> list[list[float]]:
    """gemini-embedding-001 only accepts one input per request."""
    embeddings = []
    for text in texts:
        result = client.models.embed_content(
            model=settings.gemini_embedding_model,
            contents=text,
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
                output_dimensionality=settings.embedding_dimensions,
            ),
        )
        embeddings.append(result.embeddings[0].values)
    return embeddings


def embed_query(query: str) -> list[float]:
    result = client.models.embed_content(
        model=settings.gemini_embedding_model,
        contents=query,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=settings.embedding_dimensions,
        ),
    )
    return result.embeddings[0].values


def index_transcript(meeting_id: str, transcript: dict) -> None:
    """
    Chunks the `conversation` array of a completed transcript, embeds each
    chunk, and (re)writes them to `meeting_chunks` so the RAG agent can query
    this meeting. Safe to call again for the same meeting (old chunks are
    cleared first).
    """
    conversation = transcript.get("conversation") or []
    if not conversation:
        return

    chunks = _build_chunks(conversation)
    if not chunks:
        return

    embeddings = _embed_documents([c["content"] for c in chunks])

    supabase.table("meeting_chunks").delete().eq("meeting_id", meeting_id).execute()

    rows = [
        {
            "meeting_id": meeting_id,
            "chunk_index": chunk["chunk_index"],
            "content": chunk["content"],
            "speakers": chunk["speakers"],
            "timestamp_start": chunk["timestamp_start"],
            "timestamp_end": chunk["timestamp_end"],
            "embedding": embedding,
        }
        for chunk, embedding in zip(chunks, embeddings)
    ]

    supabase.table("meeting_chunks").insert(rows).execute()
