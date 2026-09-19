"""
Rules every Ask AI tool shares (docs/ask-ai-implementation-plan.md, Phase 3):

- Scoped to the user: the user_id comes from the tool context the service
  binds, never from anything the model passes.
- Completed meetings only.
- Unknown and unowned meeting ids get the same NOT_FOUND note, so a meeting's
  existence is never revealed.
- Bad input comes back as {"note": ...}, never as an exception.
- Every meeting in a result carries id, title and date, so the model can cite it.
"""

import uuid

from app.db.models import Meeting

NOT_FOUND = {"note": "Meeting not found."}
UNTITLED = "Untitled meeting"


def owned_completed(query, user_id: str):
    """Narrows a Meeting query to the user's completed meetings."""
    return query.filter(Meeting.user_id == user_id, Meeting.status == "completed")


def parse_meeting_id(value) -> uuid.UUID | None:
    """The model's meeting_id as a UUID, or None if it isn't one."""
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, AttributeError):
        return None


def title_of(title: str | None) -> str:
    return (title or "").strip() or UNTITLED


def clip(text, limit: int) -> str | None:
    """`text` cut to `limit` characters, with an ellipsis if anything was cut."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"
