-- Estimated AI cost of meetings recorded BEFORE Phase B4 (docs/scaling-plan.md).
--
-- Those meetings have no ai_usage_events rows, so cost_per_meeting in
-- ai_usage_queries.sql does not list them at all. This reconstructs an estimate
-- from what is still on disk: duration_seconds, the stored transcript, the
-- chunk text and the chat history.
--
-- Deliberately kept out of ai_usage_queries.sql: a test there asserts that file
-- holds exactly the four documented queries, and everything in it is measured
-- rather than estimated. These numbers are neither, and should not be mixed in.
--
-- Accuracy, worst first:
--   chat      - the prompt (retrieved chunks + history) was never stored, so
--               input tokens are unknowable. Turns are counted and priced at a
--               flat rate you set below. Treat as an order of magnitude.
--   output    - length/4 heuristic over the transcript JSON, which includes the
--               summary and action items generated in the same call.
--   embedding - length/4 over the chunk text. Close.
--   audio in  - duration x tokens-per-second. Exact if the rate is right, and
--               that rate is the one worth confirming against a real
--               ai_usage_events row before trusting any of this.

WITH rates AS (
    SELECT
        0.30::numeric AS input_per_mtok,      -- GEMINI_INPUT_COST_PER_MTOK
        2.50::numeric AS output_per_mtok,     -- GEMINI_OUTPUT_COST_PER_MTOK
        0.15::numeric AS embed_per_mtok,      -- GEMINI_EMBEDDING_COST_PER_MTOK
        32::numeric   AS audio_tokens_per_sec,
        0.005::numeric AS usd_per_chat_turn   -- replace from chat_turn_cost_percentiles
),
transcription AS (
    SELECT
        m.id AS meeting_id,
        m.user_id,
        m.created_at,
        m.duration_seconds,
        m.duration_seconds * r.audio_tokens_per_sec AS input_tokens,
        length(m.transcript::text) / 4.0            AS output_tokens
    FROM meetings m
    CROSS JOIN rates r
    WHERE m.transcript IS NOT NULL
      AND m.duration_seconds IS NOT NULL
),
embedding AS (
    SELECT meeting_id, SUM(length(content)) / 4.0 AS embed_tokens
    FROM meeting_chunks
    GROUP BY meeting_id
),
chat AS (
    SELECT meeting_id, COUNT(*) FILTER (WHERE role = 'assistant') AS turns
    FROM chat_messages
    GROUP BY meeting_id
)
SELECT
    t.meeting_id,
    t.user_id,
    t.created_at::date                                                   AS recorded_on,
    round(t.duration_seconds / 60.0, 1)                                  AS minutes,
    round(((t.input_tokens  / 1e6) * r.input_per_mtok)::numeric, 4)      AS transcribe_in_usd,
    round(((t.output_tokens / 1e6) * r.output_per_mtok)::numeric, 4)     AS transcribe_out_usd,
    round(((COALESCE(e.embed_tokens, 0) / 1e6) * r.embed_per_mtok)::numeric, 4) AS embed_usd,
    COALESCE(c.turns, 0)                                                 AS chat_turns,
    round((COALESCE(c.turns, 0) * r.usd_per_chat_turn)::numeric, 4)      AS chat_usd,
    round((
          (t.input_tokens  / 1e6) * r.input_per_mtok
        + (t.output_tokens / 1e6) * r.output_per_mtok
        + (COALESCE(e.embed_tokens, 0) / 1e6) * r.embed_per_mtok
        + COALESCE(c.turns, 0) * r.usd_per_chat_turn
    )::numeric, 4)                                                       AS total_usd_est,
    round((
        (
              (t.input_tokens  / 1e6) * r.input_per_mtok
            + (t.output_tokens / 1e6) * r.output_per_mtok
            + (COALESCE(e.embed_tokens, 0) / 1e6) * r.embed_per_mtok
            + COALESCE(c.turns, 0) * r.usd_per_chat_turn
        ) / NULLIF(t.duration_seconds / 3600.0, 0)
    )::numeric, 4)                                                       AS usd_per_meeting_hour
FROM transcription t
CROSS JOIN rates r
LEFT JOIN embedding e ON e.meeting_id = t.meeting_id
LEFT JOIN chat      c ON c.meeting_id = t.meeting_id
ORDER BY total_usd_est DESC;
