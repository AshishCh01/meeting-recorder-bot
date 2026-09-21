import uuid
from sqlalchemy import BigInteger, Boolean, Column, Float, String, Integer, DateTime, ForeignKey, Index, Numeric, Text, text
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
    # The user's own instructions for Ask AI, added to its prompt. NULL when
    # none are set. At most 1,000 characters, enforced by the API.
    ask_ai_instructions = Column(Text, nullable=True)
    # Which billing tier this user is on - "free", "pro" or "team", the ids in
    # app/billing/plans.py. Denormalised from the subscriptions row on purpose:
    # every quota check and feature gate reads it, on paths (recording a
    # meeting, answering a chat turn) where a join would be pure cost.
    #
    # That makes it a cache, so it has exactly one writer: the subscription
    # activation/expiry code in app/billing. Nothing else may set it, or the
    # cache and the subscription drift apart. Never NULL - a user with no
    # subscription is "free", not unknown.
    plan = Column(String, nullable=False, server_default="free")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    meetings = relationship("Meeting", back_populates="user")
    subscription = relationship("Subscription", back_populates="user", uselist=False)

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
    # One vector for the whole meeting (title, summary and key points), so
    # Ask AI can rank meetings by topic before searching their chunks.
    # Written in the same embedding call as the chunks, so
    # embedding_provider above describes it too. NULL until the meeting is
    # indexed, or until the Ask AI backfill reaches a meeting indexed
    # before this column existed.
    summary_embedding = Column(Vector(768), nullable=True)
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
    # Which recorder in the pool this meeting was dispatched to (Phase C3) -
    # the id of a BOT_HOSTS entry, not a URL, so moving a host to a new
    # address does not orphan its in-flight meetings. Written by
    # bot_dispatch's queued -> joining claim, in the same UPDATE, and read by
    # stop/delete/re-upload to reach the right bot.
    #
    # Nullable, and three different things make it null: every meeting from
    # before C3, every meeting still "queued" (no recorder has been chosen
    # yet), and every meeting joined on the synchronous
    # BOT_DISPATCH_USE_QUEUE=false path. bot_registry.require_host resolves
    # null to the sole configured host when there is exactly one, and refuses
    # when there is a choice to get wrong.
    bot_host_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Bumped automatically (including on Core-style bulk updates - see
    # webhooks.py/meetings.py) on every write. The watchdog sweep uses
    # this to find meetings stuck in a non-terminal status past its TTL.
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="meetings")
    chunks = relationship("MeetingChunk", back_populates="meeting", cascade="all, delete-orphan")

    __table_args__ = (
        # Ask AI's date filters. A meeting's date is
        # coalesce(scheduled_at, created_at), and Postgres only uses this
        # index for a query that repeats that expression exactly - so build
        # it with ask_ai/tools/dates.py, never by hand.
        Index("ix_meetings_user_meeting_date", "user_id", text("coalesce(scheduled_at, created_at)")),
    )

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


class AiUsageEvent(Base):
    """
    One paid (or potentially paid) call to an AI provider - Phase B4.

    What app/services/cost_tracker.record_usage writes, and what the saved SQL
    queries in docs/scaling-plan.md B4 read. One row per provider call group
    within an operation, so a chat turn that failed on Gemini and was answered
    by Groq is two rows sharing a request_id.

    user_id and meeting_id are deliberately plain UUIDs with no foreign keys:
    deleting a meeting or a user must not erase what it cost. The raw units
    (tokens, audio seconds) are the source of truth; usd is what the configured
    rate made of them at the time, and can be recomputed from the units if a
    rate was wrong or still 0.
    """
    __tablename__ = "ai_usage_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Groups the rows of one operation - one chat turn, one transcription.
    request_id = Column(UUID(as_uuid=True), nullable=True)
    # "chat", "transcription", "embedding" (indexing) or "query_embedding".
    operation = Column(String, nullable=False)
    # "gemini", "groq", "sarvam" or "jina".
    provider = Column(String, nullable=False)
    model = Column(String, nullable=True)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    meeting_id = Column(UUID(as_uuid=True), nullable=True)
    # NULL means the provider reported nothing - never a guessed zero.
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    audio_seconds = Column(Float, nullable=True)
    usd = Column(Numeric(12, 6), nullable=False, server_default="0")
    # True when the token count is a heuristic (embeddings: ~4 chars/token)
    # rather than a number the provider returned.
    estimated = Column(Boolean, nullable=False, server_default="false")
    # "ok" (the primary provider produced the result), "fallback" (a fallback
    # provider did) or "failed" (usage was spent but produced no result).
    outcome = Column(String, nullable=False)

    __table_args__ = (
        Index("ix_ai_usage_events_user_created", "user_id", "created_at"),
        Index("ix_ai_usage_events_meeting", "meeting_id"),
    )


class AskAiConversation(Base):
    """
    One Ask AI chat - a conversation across all of a user's meetings, as
    opposed to ChatMessage's one thread per meeting.

    Created by the backend as soon as the user clicks "New chat", so the chat
    has its URL before anything is asked. last_message_at is NULL until the
    first exchange is saved, and a user has at most one such empty chat (the
    partial unique index below): "New chat" reuses it rather than making
    another, and the sidebar list leaves empty chats out.

    No relationship() to User or to AskAiMessage, for the same reason
    ChatMessage has none: deletes rely on ON DELETE CASCADE in the database
    rather than on SQLAlchemy loading every row to clear it.
    """
    __tablename__ = "ask_ai_conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # "New chat" until the first question is titled.
    title = Column(String, nullable=False, server_default="New chat")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # NULL means the chat is empty. Set when an exchange is saved.
    last_message_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # The sidebar list: one user's chats, most recently used first.
        Index("ix_ask_ai_conversations_user_last_message", "user_id", text("last_message_at DESC")),
        # At most one empty chat per user, even with two tabs clicking "New
        # chat" at the same moment: the second insert fails instead of
        # making a duplicate.
        Index(
            "ux_ask_ai_conversations_one_empty_per_user",
            "user_id",
            unique=True,
            postgresql_where=text("last_message_at IS NULL"),
        ),
    )


class AskAiMessage(Base):
    """
    One turn of an Ask AI chat. Like ChatMessage, only the text turns are
    stored - tool calls and their results are re-run rather than replayed.
    """
    __tablename__ = "ask_ai_messages"

    # BIGSERIAL for the same reason as ChatMessage.id: the thread is read back
    # in insertion order, and created_at cannot provide it.
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("ask_ai_conversations.id", ondelete="CASCADE"), nullable=False)
    # Redundant with the conversation's owner, so ownership checks need no
    # join.
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # "user" or "assistant".
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    # Which Ask AI tools produced this answer. NULL on user turns.
    tools_used = Column(ARRAY(String), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        # The only access path: one conversation's thread, in order.
        Index("ix_ask_ai_messages_conversation", "conversation_id", "id"),
    )


class Subscription(Base):
    """
    A user's current paid plan. One row per user, or none at all - a user with
    no row is on Free.

    Deliberately not a history table. Upgrading, downgrading or renewing
    rewrites this row in place (hence the unique user_id), because the only
    question anything asks it is "what is this user on right now, and until
    when". The audit trail lives in `payments`, which is append-only: every
    charge that ever succeeded is a row there whatever happens here.

    **This is a demo.** Razorpay runs on test keys, so `status` is set by our
    own code after a test-mode payment rather than by a real settlement, and
    any user may take any plan. Nothing here is safe to point at live keys
    without adding the things a real system needs - idempotent webhook
    replay, dunning, proration and refunds - none of which exist yet.
    """
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Unique: one current subscription per user, see the class docstring.
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    # A plan id from app/billing/plans.py - "pro" or "team". Never "free":
    # free is the absence of a subscription, not a subscription to nothing.
    plan = Column(String, nullable=False)
    # "active", "cancelled" (still paid up to current_period_end) or
    # "expired" (period elapsed, user has been moved back to free).
    status = Column(String, nullable=False, server_default="active")
    # Seats, for per-seat plans. Always 1 for Pro - charge_paise() ignores it
    # there rather than trusting the column.
    seats = Column(Integer, nullable=False, server_default="1")
    # The window every per-period quota is counted inside. Free users have no
    # row, so their window is the calendar month instead (Phase 2).
    current_period_start = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    current_period_end = Column(DateTime(timezone=True), nullable=False)
    # Set when the user cancels: they keep the plan until current_period_end,
    # then expiry moves them to free rather than renewing.
    cancel_at_period_end = Column(Boolean, nullable=False, server_default="false")
    # The Razorpay objects that produced this subscription, for tracing a row
    # back to a dashboard entry. Nullable because a plan granted by hand (the
    # demo path) has no payment behind it.
    razorpay_order_id = Column(String, nullable=True)
    razorpay_payment_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="subscription")

    __table_args__ = (
        # The expiry sweep: every active subscription whose period has run
        # out. Ordered so the scan is over status first, which is selective
        # once most rows are expired.
        Index("ix_subscriptions_status_period_end", "status", "current_period_end"),
    )


class Payment(Base):
    """
    One attempted charge - append-only, and the only durable record that money
    (in the demo, test-mode play money) changed hands.

    user_id and subscription_id are plain UUIDs with no foreign keys, for the
    same reason AiUsageEvent's are: deleting a user or rewriting a
    subscription must not erase what was charged. A financial record outlives
    the thing it was for.

    The amount is an integer count of paise, never a float and never rupees -
    it is exactly what was sent to Razorpay, so the row can be reconciled
    against a dashboard entry without a unit conversion in between.
    """
    __tablename__ = "payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    subscription_id = Column(UUID(as_uuid=True), nullable=True)
    # The plan this charge was for, copied rather than joined: if the
    # catalogue's prices change later, this row still says what was bought.
    plan = Column(String, nullable=False)
    seats = Column(Integer, nullable=False, server_default="1")
    # Paise. See the class docstring.
    amount_paise = Column(Integer, nullable=False)
    currency = Column(String, nullable=False, server_default="INR")
    # "created" (order opened, nothing paid yet), "paid" or "failed".
    status = Column(String, nullable=False, server_default="created")
    # Unique: Razorpay's order id is the idempotency key for the whole flow.
    # A duplicated webhook or a double-clicked Checkout button must not
    # produce a second row, and the database is what guarantees that rather
    # than a check-then-insert in application code.
    razorpay_order_id = Column(String, nullable=False, unique=True)
    razorpay_payment_id = Column(String, nullable=True)
    # The HMAC Checkout hands back, kept for after-the-fact verification.
    # Not a secret on its own - it is only meaningful next to the order id.
    razorpay_signature = Column(String, nullable=True)
    # Razorpay's own error_code/error_description on a failure, and anything
    # else worth keeping from the callback. Free-form on purpose: it is
    # diagnostic, and nothing branches on it.
    notes = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        # Billing history for one user, newest first.
        Index("ix_payments_user_created", "user_id", text("created_at DESC")),
    )
