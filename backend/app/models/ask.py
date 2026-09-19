"""Request and response bodies for the Ask AI routes (app/api/ask.py)."""

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class AskRequest(BaseModel):
    # The same cap as ChatRequest.question: it bounds what one request can
    # forward to the model.
    question: str = Field(min_length=1, max_length=4000)
    # The browser's IANA timezone (Intl.DateTimeFormat().resolvedOptions()
    # .timeZone). Dates in questions are resolved against it; an unknown or
    # missing one falls back to UTC rather than failing the question.
    timezone: Optional[str] = Field(default=None, max_length=64)


class AskRenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)

    @field_validator("title")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("title must not be blank")
        return value


class AskConversationOut(BaseModel):
    id: str
    title: str
    last_message_at: Optional[str] = None


class AskMessageOut(BaseModel):
    id: str
    role: str
    content: str
    tools_used: list[str] = []
    created_at: Optional[str] = None


class AskConversationDetail(BaseModel):
    id: str
    title: str
    messages: list[AskMessageOut]


class AskDeletedResponse(BaseModel):
    status: str = "deleted"


class AskDeletedAllResponse(BaseModel):
    status: str = "deleted"
    count: int
