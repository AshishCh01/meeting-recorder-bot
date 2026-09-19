"""
One-off: summary embeddings for meetings indexed before Ask AI existed.

New meetings get meetings.summary_embedding from index_transcript, in the same
embedding call as their chunks. Meetings indexed before that change have
none, and find_meetings_by_topic can't rank them until they do. Run once
after the Ask AI migration (d5b8e1f3a7c2) has deployed:

    python -m app.ask_ai.backfill              # embed everything still missing
    python -m app.ask_ai.backfill --dry-run    # count only; no API calls, no writes

In Docker: docker exec <backend container> python -m app.ask_ai.backfill

- Picks completed meetings with a transcript and summary_embedding IS NULL,
  in batches of 50.
- Embeds each meeting with its own embedding_provider (Gemini when unset), and
  never falls back to the other provider: the summary vector has to live in
  the same space as the meeting's chunks and query embeddings.
- Idempotent and resumable. Every batch commits on its own, and a meeting is
  only written while its summary_embedding is still NULL and its provider
  unchanged - so a meeting re-indexed while this runs keeps the vector
  index_transcript gave it. If a provider fails, the script stops; run it
  again later and it carries on from what is left.
- Records usage with operation="embedding", one row per meeting, like normal
  indexing.
- Holds no database connection while an embedding call is in flight.
"""

import argparse
import logging
from collections import defaultdict

from sqlalchemy import func

from app.db.database import SessionLocal
from app.db.models import Meeting
from app.observability import configure_logging, log_context
from app.services import embedding_service
from app.services.cost_tracker import estimate_tokens, record_usage

logger = logging.getLogger(__name__)

BATCH_SIZE = 50


def _load_batch(after_id, batch_size: int) -> list:
    """
    The next batch of meetings still missing a summary vector, in id order.
    Keyset pagination (id > the last one seen) rather than re-reading the
    first page: meetings with nothing to embed stay NULL forever, and would
    otherwise fill every page and stop the run from ever reaching the rest.
    """
    db = SessionLocal()
    try:
        query = (
            db.query(Meeting.id, Meeting.user_id, Meeting.embedding_provider, Meeting.transcript)
            .filter(
                Meeting.status == "completed",
                Meeting.transcript.isnot(None),
                Meeting.summary_embedding.is_(None),
            )
        )
        if after_id is not None:
            query = query.filter(Meeting.id > after_id)
        return query.order_by(Meeting.id).limit(batch_size).all()
    finally:
        db.close()


def _store(provider: str, items: list, vectors: list) -> tuple[int, int]:
    """
    Writes one provider's vectors and their usage rows in one commit. Returns
    (written, changed_meanwhile).
    """
    written = 0
    changed = 0
    db = SessionLocal()
    try:
        for (row, text), vector in zip(items, vectors):
            updated = (
                db.query(Meeting)
                .filter(
                    Meeting.id == row.id,
                    Meeting.summary_embedding.is_(None),
                    func.coalesce(Meeting.embedding_provider, embedding_service.GEMINI_PROVIDER) == provider,
                )
                .update({Meeting.summary_embedding: vector}, synchronize_session=False)
            )
            if not updated:
                # Re-indexed (or deleted) since the batch was read.
                changed += 1
                continue

            tokens = estimate_tokens(len(text))
            with log_context(meeting_id=row.id, user_id=row.user_id):
                record_usage(
                    operation="embedding",
                    provider=provider,
                    model=embedding_service._model_for(provider),
                    outcome="ok" if provider == embedding_service.GEMINI_PROVIDER else "fallback",
                    user_id=row.user_id,
                    meeting_id=row.id,
                    input_tokens=tokens,
                    usd=embedding_service._embedding_cost(provider, tokens),
                    estimated=True,
                    db=db,
                )
            written += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return written, changed


def backfill(batch_size: int = BATCH_SIZE, dry_run: bool = False) -> dict:
    """
    Runs the backfill and returns counts: embedded, skipped (nothing to embed
    or an unknown provider), changed (re-indexed while this ran), and batches.
    With dry_run, `embedded` is what would have been embedded.
    """
    stats = {"embedded": 0, "skipped": 0, "changed": 0, "batches": 0}
    after_id = None

    while True:
        rows = _load_batch(after_id, batch_size)
        if not rows:
            break
        after_id = rows[-1].id
        stats["batches"] += 1

        by_provider: dict[str, list] = defaultdict(list)
        for row in rows:
            transcript = row.transcript if isinstance(row.transcript, dict) else {}
            text = embedding_service.summary_document(transcript)
            provider = row.embedding_provider or embedding_service.GEMINI_PROVIDER
            if not text:
                stats["skipped"] += 1
                continue
            if provider not in (embedding_service.GEMINI_PROVIDER, embedding_service.JINA_PROVIDER):
                logger.warning("[ask_ai_backfill] meeting %s has unknown embedding provider %r, skipping", row.id, provider)
                stats["skipped"] += 1
                continue
            by_provider[provider].append((row, text))

        if dry_run:
            stats["embedded"] += sum(len(items) for items in by_provider.values())
            continue

        for provider, items in by_provider.items():
            vectors = embedding_service.embed_documents_with_provider([text for _, text in items], provider)
            written, changed = _store(provider, items, vectors)
            stats["embedded"] += written
            stats["changed"] += changed

        logger.info(
            "[ask_ai_backfill] batch %s done: %s embedded so far, %s skipped",
            stats["batches"], stats["embedded"], stats["skipped"],
        )

    return stats


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Backfill meetings.summary_embedding for Ask AI.")
    parser.add_argument("--dry-run", action="store_true", help="count what would be embedded; no API calls, no writes")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args(argv)

    configure_logging()
    try:
        stats = backfill(batch_size=args.batch_size, dry_run=args.dry_run)
    except Exception:
        logger.exception(
            "[ask_ai_backfill] stopped; everything embedded so far is saved - run it again to continue"
        )
        raise SystemExit(1)

    logger.info(
        "[ask_ai_backfill] %s: %s %s, %s skipped (nothing to embed), %s changed while running, %s batches",
        "dry run" if args.dry_run else "done",
        stats["embedded"], "would be embedded" if args.dry_run else "embedded",
        stats["skipped"], stats["changed"], stats["batches"],
    )


if __name__ == "__main__":
    main()
