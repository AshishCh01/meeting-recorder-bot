"""
AI spend, recorded as data (docs/scaling-plan.md, Phase B4).

Every call to an AI provider goes through record_usage, which does two things:
emits one greppable `[cost] <operation> k=v ...` log line, and stores one
ai_usage_events row. The row is what the saved SQL queries in the plan read -
spend per user per day, cost per chat turn, cost per meeting - and what the
rate-limit caps can eventually be sized from, instead of from arithmetic.

Two rules every caller relies on:

- **Recording never breaks the work it records.** A failed insert is logged
  and swallowed. A meeting must not fail to transcribe, and a chat answer must
  not fail to arrive, over bookkeeping.
- **Recording never takes a second pooled connection from inside a job that
  already holds one.** The pool has no overflow and a 3s checkout timeout
  (config.py), and the worker runs worker_max_jobs sessions against it at
  once, so a cost write that opened its own connection mid-transcription could
  block and then fail exactly under load. Callers holding a session pass it
  as `db`: the row goes into that session inside a savepoint and is committed
  with the caller's own commit, and a failed insert rolls back only the
  savepoint. Callers holding no session (chat, query embeddings) omit `db` and
  get a short session of their own - chat calls it through asyncio.to_thread,
  so nothing blocks the event loop.
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


def gemini_generation_cost(prompt_tokens: int, output_tokens: int) -> float:
    """
    USD cost of a generate_content call, from config rates (settings.
    gemini_input_cost_per_mtok / gemini_output_cost_per_mtok), not hardcoded
    here, so pricing changes only require an env var update.
    """
    return (
        (prompt_tokens / 1_000_000) * settings.gemini_input_cost_per_mtok
        + (output_tokens / 1_000_000) * settings.gemini_output_cost_per_mtok
    )


def gemini_embedding_cost(token_count: int) -> float:
    return (token_count / 1_000_000) * settings.gemini_embedding_cost_per_mtok


def groq_generation_cost(prompt_tokens: int, output_tokens: int) -> float:
    return (
        (prompt_tokens / 1_000_000) * settings.groq_input_cost_per_mtok
        + (output_tokens / 1_000_000) * settings.groq_output_cost_per_mtok
    )


def jina_embedding_cost(token_count: int) -> float:
    return (token_count / 1_000_000) * settings.jina_embedding_cost_per_mtok


def sarvam_transcription_cost(audio_seconds: Optional[float]) -> float:
    if not audio_seconds:
        return 0.0
    return (audio_seconds / 3600) * settings.sarvam_cost_per_audio_hour


def estimate_tokens(text_length: int) -> int:
    """The ~4 characters per token heuristic, for providers that report no usage."""
    return text_length // 4


def record_usage(
    *,
    operation: str,
    provider: str,
    outcome: str,
    model: Optional[str] = None,
    request_id=None,
    user_id=None,
    meeting_id=None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    audio_seconds: Optional[float] = None,
    usd: float = 0.0,
    estimated: bool = False,
    db: Optional[Session] = None,
) -> None:
    """
    Logs and stores one AI usage event. Never raises.

    `db`: the caller's session, if it holds one. The row is added inside a
    savepoint and persists with the caller's next commit - so it is recorded
    exactly when the caller's own work is committed. Omit it only where no
    session is open, and never call this on the event loop thread without
    asyncio.to_thread.

    Token counts are None when the provider reported nothing; do not pass a
    guessed 0. Pass estimated=True when the count is a heuristic.
    """
    fields = {
        "provider": provider,
        "model": model,
        "outcome": outcome,
        "meeting": meeting_id,
        "user": user_id,
        "request": request_id,
        "in_tok": input_tokens,
        "out_tok": output_tokens,
        "audio_sec": f"{audio_seconds:.1f}" if audio_seconds is not None else None,
        "usd": f"{usd:.6f}",
        "estimated": "true" if estimated else None,
    }
    rendered = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    logger.info("[cost] %s %s", operation, rendered)

    # Imported here, not at module level: models imports database, which
    # builds the engine at import time, and this module is imported by
    # services that are otherwise usable without it.
    from app.db.models import AiUsageEvent

    row = AiUsageEvent(
        request_id=request_id,
        operation=operation,
        provider=provider,
        model=model,
        user_id=user_id,
        meeting_id=meeting_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        audio_seconds=audio_seconds,
        usd=round(usd, 6),
        estimated=estimated,
        outcome=outcome,
    )

    if db is not None:
        try:
            with db.begin_nested():
                db.add(row)
        except Exception as e:
            logger.warning("[cost] could not stage %s usage for meeting %s: %s", operation, meeting_id, e)
        return

    from app.db.database import SessionLocal

    session = SessionLocal()
    try:
        session.add(row)
        session.commit()
    except Exception as e:
        logger.warning("[cost] could not store %s usage for meeting %s: %s", operation, meeting_id, e)
        try:
            session.rollback()
        except Exception:
            pass  # a dead connection cannot roll back either; close() below still releases it
    finally:
        try:
            session.close()
        except Exception:
            pass
