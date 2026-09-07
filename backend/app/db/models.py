import uuid
from sqlalchemy import BigInteger, Column, String, Integer, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
from .database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, index=True, nullable=False)
    bot_display_name = Column(String, nullable=False, server_default="MeetIQ Notetaker")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    meetings = relationship("Meeting", back_populates="user")

class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    meeting_url = Column(String, nullable=False)
    title = Column(String, nullable=True)
    platform = Column(String, nullable=False)
    status = Column(String, nullable=False, default="scheduled")
    recording_url = Column(String, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    error_message = Column(String, nullable=True)
    transcript = Column(JSONB, nullable=True)
    # Which embedding provider ("gemini" or "jina") indexed this meeting's
    # chunks. embed_query() must use the same provider when searching this
    # meeting - Gemini and Jina vectors live in different, incompatible
    # spaces, so mixing them silently returns wrong results, not an error.
    embedding_provider = Column(String, nullable=True)
    # When set, this meeting was scheduled for a future join (via the
    # calendar "record this event" flow, or in principle any future
    # date) rather than joined immediately - app/services/scheduler.py
    # picks it up once scheduled_at arrives. NULL for meetings created
    # the normal way through POST /meetings, which still join right away.
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    # The originating Google Calendar event, for meetings created via
    # POST /calendar/events/{event_id}/schedule. NULL for manually
    # created meetings. (user_id, calendar_event_id) is unique where
    # not null - see e9c2b6a4f1d8_add_calendar_fields_to_meetings.py.
    calendar_event_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Bumped automatically (including on Core-style bulk updates - see
    # webhooks.py/meetings.py) on every write. The watchdog sweep uses
    # this to find meetings stuck in a non-terminal status past its TTL.
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="meetings")
    chunks = relationship("MeetingChunk", back_populates="meeting", cascade="all, delete-orphan")

class CalendarConnection(Base):
    __tablename__ = "calendar_connections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    google_email = Column(String, nullable=False)
    # Fernet-encrypted - never stored or logged in plaintext. Decrypted
    # on demand in app/services/google_oauth_service.py.
    refresh_token_encrypted = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User")

class MeetingChunk(Base):
    __tablename__ = "meeting_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(String, nullable=False)
    speakers = Column(ARRAY(String), nullable=True)
    timestamp_start = Column(String, nullable=True)
    timestamp_end = Column(String, nullable=True)
    embedding = Column(Vector(768), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    meeting = relationship("Meeting", back_populates="chunks")

class ChatMessage(Base):
    """
    One row per turn of a meeting's "Ask about this meeting" conversation.

    There is exactly one durable thread per (meeting, user): the chat panel
    reads it back on mount, and rag/chat_service.py replays it into a cold
    session, so the conversation survives a refresh, a logout, an LRU
    eviction and a backend restart. Only the text turns are stored - tool
    calls and their results are deliberately left out, for the same reason
    chat_fallback_groq.gemini_history_to_openai drops them: they are
    deterministic reads over the meeting's own rows, so re-calling a tool is
    cheaper and more reliable than replaying a provider-specific round-trip.

    Deliberately has no relationship() back to Meeting. delete_meeting()
    ends in db.delete(meeting), and a relationship would make SQLAlchemy
    load every message just to clear its FK; the ON DELETE CASCADE below
    removes them in the database instead.
    """
    __tablename__ = "chat_messages"

    # BIGSERIAL rather than the UUID the other tables use, because this is
    # an append-only log whose order is the point and created_at cannot
    # provide it: Postgres now() is transaction time, so a question and the
    # answer written alongside it in one commit carry the identical
    # timestamp. An increasing id is the only stable tiebreaker.
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    meeting_id = Column(UUID(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # "user" or "assistant".
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    # Which RAG tools produced this answer. NULL on user turns.
    tools_used = Column(ARRAY(String), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # The only access path there is: one meeting's thread, for its
        # owner, in insertion order.
        Index("ix_chat_messages_meeting_user", "meeting_id", "user_id", "id"),
    )
