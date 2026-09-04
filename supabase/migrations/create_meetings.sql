-- HISTORICAL / NO LONGER REQUIRED.
--
-- This is the original hand-run seed table, kept only as a record of the
-- pre-Alembic schema. You do NOT need to run it when setting up a fresh
-- project any more: backend/alembic/versions/a0f1e2d3c4b5_bootstrap_
-- preexisting_tables.py now creates this table (and meeting_chunks) in this
-- exact shape as the first migration, so `alembic upgrade head` - which the
-- backend container runs on startup - builds the whole schema from an empty
-- database on its own.
--
-- Running it by hand is harmless (the bootstrap migration uses
-- CREATE TABLE IF NOT EXISTS), just redundant.
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
