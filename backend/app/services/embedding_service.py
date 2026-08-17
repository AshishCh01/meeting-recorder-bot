from sqlalchemy.orm import Session
from google import genai
from google.genai import types

from app.config import settings
from app.db.models import MeetingChunk

# Initialize the Gemini GenAI client
client = genai.Client(api_key=settings.gemini_api_key)


def _build_chunks(conversation: list[dict]) -> list[dict]:
    """
    Groups consecutive conversation segments into overlapping windows based on token count
    using a character-based heuristic (1 token ~= 4 chars). 
    Target: ~500 tokens per chunk with ~50 tokens overlap.
    We preserve entire conversation segments to keep timestamps/speakers cleanly mapped.
    """
    if not conversation:
        return []

    TARGET_TOKENS = 500
    OVERLAP_TOKENS = 50

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


def _embed_documents(texts: list[str]) -> list[list[float]]:
    """Embeds the documents (meeting chunks) using the Gemini embedding API."""
    if not texts:
        return []
    
    response = client.models.embed_content(
        model="gemini-embedding-2",
        contents=texts,
        config=types.EmbedContentConfig(output_dimensionality=768)
    )
    
    # Extract the vectors from response.embeddings
    return [e.values for e in response.embeddings]


def embed_query(query: str) -> list[float]:
    """Embeds a query using the Gemini embedding API."""
    response = client.models.embed_content(
        model="gemini-embedding-2",
        contents=query,
        config=types.EmbedContentConfig(output_dimensionality=768)
    )
    return response.embeddings[0].values


def index_transcript(db: Session, meeting_id: str, transcript: dict) -> None:
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

    # Delete existing chunks for this meeting
    db.query(MeetingChunk).filter(MeetingChunk.meeting_id == meeting_id).delete()
    db.commit()

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
    db.commit()
