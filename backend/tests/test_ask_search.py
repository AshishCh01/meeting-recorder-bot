"""
Ask AI Phase 3 - find_meetings_by_topic and search_across_meetings.

embed_query is faked with one fixed query vector per provider, and the stored
vectors are built so their cosine similarity to it is known exactly
(tests/ask_ai_fixtures.toward), which makes the rankings exact.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text

from app.ask_ai.tools import find_meetings_by_topic, search_across_meetings
from app.ask_ai.tools import search as search_module
from app.db import database

from tests.ask_ai_fixtures import KOLKATA, add_chunk, add_meeting, add_meetings, add_user, meeting_row, toward, unit

AUG_1 = datetime(2026, 8, 1, 4, 30, tzinfo=timezone.utc)
GEMINI_AXIS = 1
JINA_AXIS = 2
QUERY_VECTORS = {"gemini": unit(GEMINI_AXIS), "jina": unit(JINA_AXIS)}


# Pooled connections already checked out when a tool is called - the test's
# own session usually holds one. Set by topic() and search() below.
_baseline = [0]


@pytest.fixture
def embeds(monkeypatch):
    """
    Records each embed_query call as (query, provider, connections the tool
    itself had checked out at that moment) - the last must be 0: no tool may
    hold a pooled connection across the network call.
    """
    calls = []

    def fake_embed_query(query, provider="gemini", *, meeting_id=None, user_id=None):
        calls.append((query, provider, database.engine.pool.checkedout() - _baseline[0]))
        return QUERY_VECTORS[provider]

    monkeypatch.setattr(search_module, "embed_query", fake_embed_query)
    return calls


def topic(user_id, query="pricing", **kwargs):
    user_id = str(user_id)
    _baseline[0] = database.engine.pool.checkedout()
    return find_meetings_by_topic(user_id=str(user_id), tz_name=KOLKATA, query=query, **kwargs)


def search(user_id, query="budget", **kwargs):
    user_id = str(user_id)
    _baseline[0] = database.engine.pool.checkedout()
    return search_across_meetings(user_id=str(user_id), tz_name=KOLKATA, query=query, **kwargs)


# ---------------------------------------------------------------------------
# find_meetings_by_topic
# ---------------------------------------------------------------------------

def test_meetings_are_ranked_by_their_summary_vector(db, user, embeds):
    weak = add_meeting(db, user.id, when=AUG_1, title="Weak", summary_embedding=toward(GEMINI_AXIS, 0.2))
    strong = add_meeting(db, user.id, when=AUG_1, title="Strong", summary_embedding=toward(GEMINI_AXIS, 0.9))
    middle = add_meeting(db, user.id, when=AUG_1, title="Middle", summary_embedding=toward(GEMINI_AXIS, 0.5))

    result = topic(user.id)

    assert [m["id"] for m in result["meetings"]] == [str(strong), str(middle), str(weak)]
    assert [m["relevance"] for m in result["meetings"]] == [0.9, 0.5, 0.2]
    assert set(result["meetings"][0]) == {"id", "title", "date", "summary", "relevance"}
    assert embeds == [("pricing", "gemini", 0)], "a connection was held during the embedding call"


def test_meetings_without_a_summary_vector_are_skipped(db, user, embeds):
    ranked = add_meeting(db, user.id, when=AUG_1, summary_embedding=toward(GEMINI_AXIS, 0.5))
    add_meeting(db, user.id, when=AUG_1, summary_embedding=None)

    assert [m["id"] for m in topic(user.id)["meetings"]] == [str(ranked)]


def test_gemini_and_jina_meetings_each_use_their_own_query_vector(db, user, embeds):
    """
    Each meeting is ranked against the query embedded by its own provider.
    Compared against the other provider's query vector, every one of these
    would score 0.
    """
    gemini = add_meeting(db, user.id, when=AUG_1, embedding_provider="gemini", summary_embedding=toward(GEMINI_AXIS, 0.6))
    legacy = add_meeting(db, user.id, when=AUG_1, embedding_provider=None, summary_embedding=toward(GEMINI_AXIS, 0.4))
    jina = add_meeting(db, user.id, when=AUG_1, embedding_provider="jina", summary_embedding=toward(JINA_AXIS, 0.8))

    result = topic(user.id)

    assert [m["id"] for m in result["meetings"]] == [str(jina), str(gemini), str(legacy)]
    assert sorted(provider for _, provider, _ in embeds) == ["gemini", "jina"], "one embedding per provider"
    assert all(held == 0 for _, _, held in embeds)


def test_the_top_ten_are_merged_across_providers_by_distance(db, user, embeds):
    rows = [
        meeting_row(user.id, when=AUG_1, title=f"G{i}", embedding_provider="gemini", summary_embedding=toward(GEMINI_AXIS, 0.05 + i * 0.1))
        for i in range(8)
    ] + [
        meeting_row(user.id, when=AUG_1, title=f"J{i}", embedding_provider="jina", summary_embedding=toward(JINA_AXIS, 0.1 + i * 0.1))
        for i in range(8)
    ]
    add_meetings(db, rows)

    result = topic(user.id)

    relevances = [m["relevance"] for m in result["meetings"]]
    assert len(relevances) == 10
    assert relevances == sorted(relevances, reverse=True)
    # Gemini scores 0.05, 0.15 ... 0.75 and Jina 0.1, 0.2 ... 0.8: the ten
    # best across both run from 0.8 down to 0.35.
    assert relevances[-1] == pytest.approx(0.35), "the ten most relevant across both providers"


def test_a_date_range_limits_the_candidates(db, user, embeds):
    in_range = add_meeting(db, user.id, when=AUG_1, summary_embedding=toward(GEMINI_AXIS, 0.3))
    add_meeting(db, user.id, when=AUG_1 + timedelta(days=40), summary_embedding=toward(GEMINI_AXIS, 0.9))

    result = topic(user.id, start_date="2026-08-01", end_date="2026-08-31")

    assert [m["id"] for m in result["meetings"]] == [str(in_range)]


def test_no_candidates_is_a_note_and_no_embedding_call(db, user, embeds):
    add_meeting(db, user.id, when=AUG_1, summary_embedding=None)

    result = topic(user.id, start_date="2026-08-01")

    assert result["meetings"] == []
    assert "No meetings found in 1 Aug 2026 to rank by topic" in result["note"]
    assert embeds == []


def test_an_empty_topic_is_a_note(db, user, embeds):
    assert "No topic was given" in topic(user.id, query="   ")["note"]
    assert embeds == []


def test_an_embedding_failure_is_a_note_not_an_exception(db, user, monkeypatch):
    add_meeting(db, user.id, when=AUG_1, summary_embedding=toward(GEMINI_AXIS, 0.5))

    def down(*args, **kwargs):
        raise RuntimeError("503 UNAVAILABLE")

    monkeypatch.setattr(search_module, "embed_query", down)

    assert topic(user.id) == search_module.SEARCH_UNAVAILABLE


# ---------------------------------------------------------------------------
# search_across_meetings
# ---------------------------------------------------------------------------

def test_passages_are_ranked_across_meetings_and_carry_their_meeting(db, user, embeds):
    budget = add_meeting(db, user.id, when=AUG_1, title="Budget review")
    hiring = add_meeting(db, user.id, when=AUG_1 + timedelta(days=1), title="Hiring sync")
    add_chunk(db, budget, "Rahul - The Q3 budget is tight.", toward(GEMINI_AXIS, 0.9), speakers=["Rahul"], timestamp_start="04:10")
    add_chunk(db, hiring, "Priya - Budget allows two hires.", toward(GEMINI_AXIS, 0.6), chunk_index=0)
    add_chunk(db, hiring, "Priya - Interviews start Monday.", toward(GEMINI_AXIS, 0.1), chunk_index=1)

    result = search(user.id)

    assert [m["content"] for m in result["matches"]] == [
        "Rahul - The Q3 budget is tight.", "Priya - Budget allows two hires.", "Priya - Interviews start Monday.",
    ]
    first = result["matches"][0]
    assert first == {
        "meeting_id": str(budget), "title": "Budget review", "date": "Sat 1 Aug 2026, 10:00",
        "content": "Rahul - The Q3 budget is tight.", "speakers": ["Rahul"], "timestamp_start": "04:10",
    }
    assert embeds == [("budget", "gemini", 0)], "a connection was held during the embedding call"


def test_only_the_top_ten_passages_come_back(db, user, embeds):
    meeting = add_meeting(db, user.id, when=AUG_1)
    for i in range(15):
        add_chunk(db, meeting, f"chunk {i}", toward(GEMINI_AXIS, 0.05 + i * 0.06), chunk_index=i)

    matches = search(user.id)["matches"]

    assert [m["content"] for m in matches] == [f"chunk {i}" for i in range(14, 4, -1)]


def test_gemini_and_jina_chunks_each_use_their_own_query_vector(db, user, embeds):
    gemini = add_meeting(db, user.id, when=AUG_1, embedding_provider="gemini")
    jina = add_meeting(db, user.id, when=AUG_1, embedding_provider="jina")
    add_chunk(db, gemini, "from gemini", toward(GEMINI_AXIS, 0.5))
    add_chunk(db, jina, "from jina", toward(JINA_AXIS, 0.7))

    matches = search(user.id)["matches"]

    assert [m["content"] for m in matches] == ["from jina", "from gemini"]
    assert sorted(provider for _, provider, _ in embeds) == ["gemini", "jina"]


def test_without_a_range_only_the_most_recent_200_meetings_are_searched(db, user, embeds):
    rows = [meeting_row(user.id, when=AUG_1 + timedelta(hours=i)) for i in range(201)]
    oldest, newest = add_meetings(db, rows)[0], rows[-1]["id"]
    add_chunk(db, oldest, "the best match, but too old", toward(GEMINI_AXIS, 0.99))
    add_chunk(db, newest, "a recent match", toward(GEMINI_AXIS, 0.3))

    assert [m["content"] for m in search(user.id)["matches"]] == ["a recent match"]

    ranged = search(user.id, start_date="2026-08-01", end_date="2026-08-31")["matches"]
    assert [m["content"] for m in ranged] == ["the best match, but too old", "a recent match"], (
        "an explicit range is not capped"
    )


def test_no_meetings_or_no_passages_are_notes(db, user, embeds):
    assert search(user.id, start_date="2026-08-01")["note"] == "No completed meetings found in 1 Aug 2026."
    assert embeds == []

    add_meeting(db, user.id, when=AUG_1)
    assert search(user.id)["note"] == "None of these meetings have searchable transcript passages yet."


def test_an_empty_query_is_a_note(db, user, embeds):
    assert "No query was given" in search(user.id, query="")["note"]


def test_the_passage_ranking_never_uses_the_shared_ivfflat_index(db, user, embeds):
    """
    The ivfflat index on meeting_chunks.embedding (production has it; the test
    schema doesn't) searches one of its lists across every user's chunks and
    filters afterwards, so it can silently drop the best matches. Seq scans
    are switched off to push the planner toward the index as hard as
    possible; the query search_across_meetings actually runs must still
    avoid it. The same query written without the MATERIALIZED CTE is checked
    too, so this test can tell the difference.
    """
    meeting = add_meeting(db, user.id, when=AUG_1)
    other = add_meeting(db, add_user(db, "other@example.com"), when=AUG_1)
    for i in range(30):
        add_chunk(db, meeting, f"mine {i}", toward(GEMINI_AXIS, 0.02 * i), chunk_index=i)
        add_chunk(db, other, f"theirs {i}", toward(GEMINI_AXIS, 0.02 * i), chunk_index=i)

    captured = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "candidate_chunks" in statement:
            captured.append((statement, parameters))

    with database.engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX test_meeting_chunks_embedding_idx ON meeting_chunks "
            "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)"
        ))
    event.listen(database.engine, "before_cursor_execute", capture)
    try:
        assert len(search(user.id)["matches"]) == 10
        [(statement, parameters)] = captured

        naive = (
            "SELECT id FROM meeting_chunks WHERE meeting_id = ANY(%(ids)s) "
            "ORDER BY embedding <=> %(q)s::vector LIMIT 10"
        )
        with database.engine.connect() as conn:
            conn.exec_driver_sql("SET enable_seqscan = off")
            plan = "\n".join(r[0] for r in conn.exec_driver_sql("EXPLAIN " + statement, parameters))
            naive_plan = "\n".join(r[0] for r in conn.exec_driver_sql(
                "EXPLAIN " + naive, {"ids": [meeting], "q": str(unit(GEMINI_AXIS))},
            ))
    finally:
        event.remove(database.engine, "before_cursor_execute", capture)
        with database.engine.begin() as conn:
            conn.execute(text("DROP INDEX IF EXISTS test_meeting_chunks_embedding_idx"))

    assert "test_meeting_chunks_embedding_idx" in naive_plan, "the planner never picks the index, so this test proves nothing"
    assert "test_meeting_chunks_embedding_idx" not in plan, plan
