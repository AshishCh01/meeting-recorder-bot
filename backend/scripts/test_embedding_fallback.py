"""
Forces a Gemini embedding failure (simulated 503, "server overloaded")
to verify that embedding_service falls back to Jina AI, correctly
writes embedding_provider="jina" on the Meeting row, and that a
follow-up query for that same meeting picks the matching (Jina) model
via embed_query().

Usage:
    python scripts/test_embedding_fallback.py <meeting_id>

<meeting_id> must belong to a meeting that already has a completed
transcript (meetings.transcript populated with a "conversation" array)
- the test re-indexes it, so any chunks currently stored for it will
be replaced. Requires JINA_API_KEY to be set in .env.
"""
import sys

from google.genai import errors

from app.db.database import SessionLocal
from app.db.models import Meeting, MeetingChunk
from app.services import embedding_service


def _fake_503():
    """
    Builds a real google.genai.errors.APIError instance without going
    through its constructor (whose exact signature varies by SDK
    version) - bypasses __init__ via __new__ and sets only the
    attribute embedding_service actually reads (`.code`), so
    `except errors.APIError as e: ... if e.code in (429, 503)` in
    embedding_service.py sees a genuine instance and behaves exactly
    as it would against a real outage.
    """
    err = errors.APIError.__new__(errors.APIError)
    err.code = 503
    err.message = "Simulated: Gemini servers overloaded (test_embedding_fallback.py)"
    return err


def main(meeting_id: str) -> None:
    db = SessionLocal()
    try:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not meeting:
            print(f"No meeting found with id {meeting_id}")
            sys.exit(1)
        if not meeting.transcript or not meeting.transcript.get("conversation"):
            print("This meeting has no transcript/conversation to index. Pick a completed one.")
            sys.exit(1)

        print(f"[test] Meeting {meeting_id} - transcript has "
              f"{len(meeting.transcript['conversation'])} conversation segments.")

        # --- Force every Gemini embedding call to fail with a 503 ---
        # This exercises the real retry loop in _call_gemini_embed_with_retry
        # (all 4 attempts fail, so expect a few seconds of backoff) before
        # falling through to Jina, the same way a real outage would.
        original_embed_content = embedding_service.client.models.embed_content

        def _always_fail_503(*args, **kwargs):
            raise _fake_503()

        embedding_service.client.models.embed_content = _always_fail_503

        print("[test] Gemini embedding calls patched to always raise 503. "
              "Re-indexing meeting (expect ~4 retries then a Jina fallback)...")

        try:
            embedding_service.index_transcript(db, meeting_id, meeting.transcript)
        finally:
            # Always restore, even if the test itself fails partway through
            embedding_service.client.models.embed_content = original_embed_content

        # --- Verify the fallback actually happened ---
        db.refresh(meeting)
        chunks = (
            db.query(MeetingChunk)
            .filter(MeetingChunk.meeting_id == meeting_id)
            .order_by(MeetingChunk.chunk_index)
            .all()
        )

        print(f"\n[test] embedding_provider on meeting row: {meeting.embedding_provider!r}")
        print(f"[test] chunks indexed: {len(chunks)}")
        if chunks:
            print(f"[test] first chunk embedding length: {len(chunks[0].embedding)}")

        assert meeting.embedding_provider == "jina", (
            f"Expected embedding_provider='jina' after forced Gemini failure, "
            f"got {meeting.embedding_provider!r}"
        )
        assert len(chunks) > 0, "Expected chunks to be written, found none"
        assert len(chunks[0].embedding) == 768, (
            f"Expected 768-dim embedding, got {len(chunks[0].embedding)}"
        )
        print("[test] PASS: index_transcript correctly fell back to Jina.")

        # --- Verify a query for this meeting uses the matching (Jina) model ---
        print("\n[test] Testing embed_query() picks up the stored provider...")
        provider = meeting.embedding_provider or "gemini"
        query_embedding = embedding_service.embed_query("what was discussed", provider=provider)
        assert len(query_embedding) == 768, (
            f"Expected 768-dim query embedding, got {len(query_embedding)}"
        )
        print(f"[test] PASS: embed_query(provider={provider!r}) returned a "
              f"{len(query_embedding)}-dim vector, matching the indexed chunks.")

        # Sanity check: nearest-chunk lookup against the query vector shouldn't error
        nearest = (
            db.query(MeetingChunk)
            .filter(MeetingChunk.meeting_id == meeting_id)
            .order_by(MeetingChunk.embedding.cosine_distance(query_embedding))
            .first()
        )
        print(f"[test] Nearest chunk (index {nearest.chunk_index}): "
              f"{nearest.content[:80]!r}...")

        print("\n[test] ALL CHECKS PASSED.")

    finally:
        db.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/test_embedding_fallback.py <meeting_id>")
        sys.exit(1)
    main(sys.argv[1])