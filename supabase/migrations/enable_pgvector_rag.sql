-- Enables pgvector and adds storage for the RAG pipeline.
-- Run this once in the Supabase SQL editor, after create_meetings.sql.

create extension if not exists vector;

-- One row per transcript chunk (a few conversation segments grouped
-- together) so we can do semantic search scoped to a single meeting.
create table meeting_chunks (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references meetings(id) on delete cascade,
  chunk_index int not null,          -- order of the chunk within the meeting
  content text not null,             -- "Speaker - text" lines, concatenated
  speakers text[],                   -- distinct speakers in this chunk
  timestamp_start text,              -- MM:SS of the first segment in the chunk
  timestamp_end text,                -- MM:SS of the last segment in the chunk
  embedding vector(1536) not null,   -- gemini-embedding-001, truncated to 1536 dims
  created_at timestamptz default now()
);

create index on meeting_chunks (meeting_id);

-- ivfflat needs rows to train lists on; fine to create up front, Postgres
-- will just use a flat scan until there's enough data.
create index on meeting_chunks using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- Similarity search scoped to one meeting. Called from the ADK tool via
-- supabase.rpc("match_meeting_chunks", {...}).
create or replace function match_meeting_chunks (
  query_embedding vector(1536),
  match_meeting_id uuid,
  match_count int default 6
)
returns table (
  id uuid,
  content text,
  speakers text[],
  timestamp_start text,
  timestamp_end text,
  similarity float
)
language sql stable
as $$
  select
    id,
    content,
    speakers,
    timestamp_start,
    timestamp_end,
    1 - (embedding <=> query_embedding) as similarity
  from meeting_chunks
  where meeting_id = match_meeting_id
  order by embedding <=> query_embedding
  limit match_count;
$$;
