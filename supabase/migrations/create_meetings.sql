create table meetings (
  id uuid primary key default gen_random_uuid(),
  meeting_url text not null,
  platform text not null,              -- 'google' | 'zoom' | 'teams'
  status text not null default 'scheduled',
  -- scheduled | joining | recording | uploading | completed | failed
  recording_url text,                  -- signed URL, generated on read, not stored long-term
  duration_seconds int,
  error_message text,
  created_at timestamptz default now()
);

-- Run this once in the Supabase SQL editor. Also create a "recordings"
-- bucket under Storage (private, not public) before running the bot.
