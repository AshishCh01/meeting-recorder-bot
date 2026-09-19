"""
Ask AI Phase 3 - list_meetings.

The detail level is chosen from the match count (settings ask_ai_*_limit), so
"what was discussed in August?" covers every meeting in the month instead of
an arbitrary first 20.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.ask_ai.tools import list_meetings

from tests.ask_ai_fixtures import KOLKATA, add_meeting, add_meetings, add_user, meeting_row

AUG_1 = datetime(2026, 8, 1, 4, 30, tzinfo=timezone.utc)   # 10:00 IST


def listed(user_id, **kwargs):
    return list_meetings(user_id=str(user_id), tz_name=KOLKATA, **kwargs)


def add_n(db, user_id, n, *, start=AUG_1, step=timedelta(hours=1), **kwargs):
    return add_meetings(db, [meeting_row(user_id, when=start + i * step, title=f"Meeting {i}", **kwargs) for i in range(n)])


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("count, mode", [
    (1, "detailed"), (20, "detailed"),
    (21, "compact"), (100, "compact"),
    (101, "titles"), (300, "titles"),
])
def test_the_mode_follows_the_match_count(db, user, count, mode):
    add_n(db, user.id, count)

    result = listed(user.id)

    assert (result["mode"], result["total_count"], result["returned"], result["truncated"]) == (mode, count, count, False)
    assert "note" not in result


def test_no_matches_is_an_empty_result_with_a_note(db, user):
    result = listed(user.id)

    assert (result["mode"], result["total_count"], result["returned"], result["meetings"]) == (None, 0, 0, [])
    assert result["note"] == "No completed meetings found."


def test_past_the_titles_limit_the_most_recent_are_returned_and_marked_truncated(db, user):
    ids = add_n(db, user.id, 301)

    result = listed(user.id)

    assert (result["mode"], result["total_count"], result["returned"], result["truncated"]) == ("titles", 301, 300, True)
    assert str(ids[0]) not in {m["id"] for m in result["meetings"]}, "the oldest should be the one left out"
    assert "most recent 300 of 301" in result["note"]


def test_fifty_august_meetings_all_come_back_in_compact(db, user):
    august = add_n(db, user.id, 50, step=timedelta(hours=14))                     # 1 Aug to 29 Aug
    add_n(db, user.id, 5, start=datetime(2026, 9, 2, tzinfo=timezone.utc))        # September, not wanted

    result = listed(user.id, start_date="2026-08-01", end_date="2026-08-31")

    assert (result["mode"], result["total_count"], result["returned"]) == ("compact", 50, 50)
    assert {m["id"] for m in result["meetings"]} == {str(m) for m in august}


def test_each_mode_carries_id_title_and_date_and_its_own_detail(db, user):
    long_summary = "word " * 100
    add_meeting(
        db, user.id, when=AUG_1, title="Launch review", summary=long_summary, duration_seconds=2700,
        key_points=["First point " * 10, "Second", "  ", "Third", "Fourth"],
    )

    [detailed] = listed(user.id)["meetings"]
    assert detailed["title"] == "Launch review"
    assert detailed["date"] == "Sat 1 Aug 2026, 10:00"
    assert (detailed["platform"], detailed["duration_minutes"]) == ("google_meet", 45)
    assert len(detailed["summary"]) == 300 and detailed["summary"].endswith("…")
    assert "key_points" not in detailed

    add_n(db, user.id, 20, start=AUG_1 + timedelta(days=1))
    compact = listed(user.id)["meetings"]
    [entry] = [m for m in compact if m["title"] == "Launch review"]
    assert set(entry) == {"id", "title", "date", "key_points"}
    assert entry["key_points"][1:] == ["Second", "Third"], "blank points skipped, only the first three kept"
    assert len(entry["key_points"][0]) == 80


def test_titles_mode_is_titles_and_dates_only(db, user):
    add_n(db, user.id, 101)

    entry = listed(user.id)["meetings"][0]

    assert set(entry) == {"id", "title", "date"}


# ---------------------------------------------------------------------------
# Filtering and order
# ---------------------------------------------------------------------------

def test_only_the_users_completed_meetings_are_listed(db, user):
    mine = add_meeting(db, user.id, when=AUG_1)
    for status in ("failed", "transcribing", "recording", "scheduled"):
        add_meeting(db, user.id, when=AUG_1, status=status)
    add_meeting(db, add_user(db, "someone-else@example.com"), when=AUG_1)

    result = listed(user.id)

    assert [m["id"] for m in result["meetings"]] == [str(mine)]
    assert result["total_count"] == 1


def test_newest_first(db, user):
    ids = add_n(db, user.id, 3, step=timedelta(days=1))

    assert [m["id"] for m in listed(user.id)["meetings"]] == [str(i) for i in reversed(ids)]


def test_title_query_is_a_case_insensitive_contains(db, user):
    wanted = add_meeting(db, user.id, when=AUG_1, title="Q3 Budget Review")
    add_meeting(db, user.id, when=AUG_1, title="Hiring sync")

    result = listed(user.id, title_query="budget")

    assert [m["id"] for m in result["meetings"]] == [str(wanted)]


def test_title_query_wildcards_are_literal(db, user):
    add_meeting(db, user.id, when=AUG_1, title="Anything at all")
    percent = add_meeting(db, user.id, when=AUG_1, title="Growth 100% plan")

    assert listed(user.id, title_query="%")["total_count"] == 1
    assert [m["id"] for m in listed(user.id, title_query="100%")["meetings"]] == [str(percent)]
    assert listed(user.id, title_query="_")["total_count"] == 0


def test_a_meeting_with_no_title_is_still_citable(db, user):
    add_meeting(db, user.id, when=AUG_1, title=None)

    assert listed(user.id)["meetings"][0]["title"] == "Untitled meeting"


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def test_an_empty_range_says_which_range_was_searched(db, user):
    add_meeting(db, user.id, when=AUG_1)

    assert listed(user.id, start_date="2026-09-15")["note"] == "No completed meetings found on 15 Sep 2026."
    assert listed(user.id, start_date="2026-09-16", end_date="2026-09-15")["note"] == (
        "No completed meetings found between 15 Sep 2026 and 16 Sep 2026."
    )
    assert listed(user.id, start_date="2026-08-01", title_query="retro")["note"] == (
        "No completed meetings found on 1 Aug 2026 with a title containing 'retro'."
    )


def test_an_invalid_date_is_a_note_for_the_model(db, user):
    result = listed(user.id, start_date="15/09/26")

    assert set(result) == {"note"}
    assert "YYYY-MM-DD" in result["note"]
