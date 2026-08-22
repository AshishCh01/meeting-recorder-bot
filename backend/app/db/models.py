import uuid
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
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
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Bumped automatically (including on Core-style bulk updates - see
    # webhooks.py/meetings.py) on every write. The watchdog sweep uses
    # this to find meetings stuck in a non-terminal status past its TTL.
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="meetings")
    chunks = relationship("MeetingChunk", back_populates="meeting", cascade="all, delete-orphan")

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
