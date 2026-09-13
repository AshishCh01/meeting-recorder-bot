-- AI usage and spend (docs/scaling-plan.md, Phase B4).
--
-- Paste any one of these into the Supabase SQL editor and run it; the editor
-- runs as the database owner, which is what ai_usage_events' RLS allows.
-- backend/tests/test_ai_usage_metrics.py runs every query in this file against
-- seeded rows, so what is documented here is what was tested.
--
-- usd is the configured rate at the time each row was written. The raw units
-- (input_tokens, output_tokens, audio_seconds) are stored too, so if a rate was
-- wrong or still 0, replace SUM(usd) with the units times the correct rate.
-- estimated = true marks token counts that are a ~4 chars/token heuristic.
--
-- Each query starts with a "-- name:" line; the test file finds them by name.


-- name: spend_per_user_per_day
-- Daily AI spend per user, newest day first. The last 30 days.
SELECT
    date_trunc('day', created_at)          AS day,
    user_id,
    COUNT(*)                                AS calls,
    SUM(usd)                                AS usd
FROM ai_usage_events
WHERE created_at >= now() - interval '30 days'
GROUP BY 1, 2
ORDER BY day DESC, usd DESC;


-- name: chat_turn_cost_percentiles
-- What one chat turn costs: median and 95th percentile, per day. A turn is
-- every row sharing a request_id - a Gemini row plus a Groq row when the
-- fallback ran. This is the number B1's chat rate limit should be sized from.
SELECT
    date_trunc('day', turn_started)                                     AS day,
    COUNT(*)                                                            AS turns,
    percentile_cont(0.5)  WITHIN GROUP (ORDER BY turn_usd)              AS p50_usd,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY turn_usd)              AS p95_usd,
    MAX(turn_usd)                                                       AS max_usd
FROM (
    SELECT request_id, MIN(created_at) AS turn_started, SUM(usd) AS turn_usd
    FROM ai_usage_events
    WHERE operation = 'chat' AND request_id IS NOT NULL
    GROUP BY request_id
) AS turns
GROUP BY 1
ORDER BY day DESC;


-- name: cost_per_meeting
-- Total AI cost of each meeting, split by what it was spent on. Replaces the
-- old "[cost] meeting_total" log line, and also counts chat and search.
SELECT
    meeting_id,
    SUM(usd)                                                   AS total_usd,
    SUM(usd) FILTER (WHERE operation = 'transcription')        AS transcription_usd,
    SUM(usd) FILTER (WHERE operation = 'embedding')            AS embedding_usd,
    SUM(usd) FILTER (WHERE operation = 'chat')                 AS chat_usd,
    SUM(usd) FILTER (WHERE operation = 'query_embedding')      AS query_embedding_usd,
    MIN(created_at)                                            AS first_call,
    MAX(created_at)                                            AS last_call
FROM ai_usage_events
WHERE meeting_id IS NOT NULL
GROUP BY meeting_id
ORDER BY total_usd DESC;


-- name: spend_and_fallbacks_by_provider
-- Spend and outcome counts per operation and provider, last 7 days. A rising
-- fallback count is a primary provider in trouble; failed rows are usage that
-- was paid for and produced nothing.
SELECT
    operation,
    provider,
    COUNT(*)                                          AS calls,
    COUNT(*) FILTER (WHERE outcome = 'ok')            AS ok,
    COUNT(*) FILTER (WHERE outcome = 'fallback')      AS fallback,
    COUNT(*) FILTER (WHERE outcome = 'failed')        AS failed,
    SUM(usd)                                          AS usd
FROM ai_usage_events
WHERE created_at >= now() - interval '7 days'
GROUP BY operation, provider
ORDER BY operation, usd DESC;
