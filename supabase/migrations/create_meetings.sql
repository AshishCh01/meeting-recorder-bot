-- This creates only a minimal seed "meetings" table - not the full current
-- schema. Everything else (this table's later columns like user_id/title/
-- embedding_provider/scheduled_at/calendar_event_id/updated_at, plus the
-- users and meeting_chunks tables, indexes, Row Level Security policies,
-- and the pgvector extension) is added automatically via Alembic
-- migrations the first time the backend container starts - see
-- backend/alembic/versions/. Run this file first (see README.md's
-- "Set up Supabase" section), then start the backend once to let Alembic
-- finish the schema. Alembic's own initial migration only ALTERs this
-- table rather than creating it, so this manual step can't be skipped
-- when bootstrapping a genuinely fresh Supabase project.
create table meetings (
  id uuid primary key default gen_random_uuid(),
  meeting_url text not null,
  platform text not null,              -- 'google' | 'zoom' | 'teams'
  status text not null default 'scheduled',
  -- scheduled | joining | recording | uploading | transcribing | completed | failed
  recording_url text,                  -- signed URL, generated on read, not stored long-term
  duration_seconds int,
  error_message text,
  transcript jsonb,                    -- Gemini output: summary, key_points, action_items, conversation
  created_at timestamptz default now()
);

-- Run this once in the Supabase SQL editor. Also create a "recordings"
-- bucket under Storage (private, not public) before running the bot.
