"""
Ask AI Phase 3 - dates.

The model sends YYYY-MM-DD in the user's local calendar; meetings are stored
in UTC. These pin the conversion, and that the date expression the tools
filter on is the one ix_meetings_user_meeting_date indexes.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text

from app.ask_ai.tools import list_meetings
from app.ask_ai.tools.dates import (
    InvalidDate, describe_range, format_local, local_range_to_utc, meeting_date_expr, resolve_tz,
)
from app.db.models import Meeting

from tests.ask_ai_fixtures import KOLKATA, add_meeting

IST = ZoneInfo(KOLKATA)
UTC = timezone.utc


def utc(*args):
    return datetime(*args, tzinfo=UTC)


# ---------------------------------------------------------------------------
# local_range_to_utc
# ---------------------------------------------------------------------------

def test_a_single_day_is_local_midnight_to_local_midnight():
    assert local_range_to_utc("2026-09-15", None, IST) == (utc(2026, 9, 14, 18, 30), utc(2026, 9, 15, 18, 30))


def test_the_end_date_includes_its_whole_day():
    assert local_range_to_utc("2026-09-15", "2026-09-16", IST) == (utc(2026, 9, 14, 18, 30), utc(2026, 9, 16, 18, 30))


def test_a_swapped_range_is_reordered():
    assert local_range_to_utc("2026-09-16", "2026-09-15", IST) == local_range_to_utc("2026-09-15", "2026-09-16", IST)


def test_a_lone_end_date_is_that_one_day():
    assert local_range_to_utc(None, "2026-09-15", IST) == local_range_to_utc("2026-09-15", None, IST)


def test_no_dates_means_no_range():
    assert local_range_to_utc(None, None, IST) is None
    assert local_range_to_utc("", "", IST) is None


def test_a_whole_month_crosses_its_boundaries_in_local_time():
    assert local_range_to_utc("2026-08-01", "2026-08-31", IST) == (utc(2026, 7, 31, 18, 30), utc(2026, 8, 31, 18, 30))
    # Year boundary.
    assert local_range_to_utc("2026-12-31", None, IST) == (utc(2026, 12, 30, 18, 30), utc(2026, 12, 31, 18, 30))


def test_a_daylight_saving_day_is_as_long_as_it_really_was():
    new_york = ZoneInfo("America/New_York")
    start, end = local_range_to_utc("2026-11-01", None, new_york)   # clocks go back: a 25-hour day
    assert (start, end) == (utc(2026, 11, 1, 4), utc(2026, 11, 2, 5))


@pytest.mark.parametrize("bad", ["15/09/2026", "2026-02-30", "yesterday", "2026-9-15x"])
def test_an_invalid_date_is_a_note_not_a_crash(bad):
    with pytest.raises(InvalidDate) as e:
        local_range_to_utc(bad, None, IST)
    assert "YYYY-MM-DD" in str(e.value)


# ---------------------------------------------------------------------------
# Formatting and timezones
# ---------------------------------------------------------------------------

def test_format_local_shows_the_users_wall_clock():
    assert format_local(utc(2026, 9, 15, 9, 0), IST) == "Tue 15 Sep 2026, 14:30"
    assert format_local(datetime(2026, 9, 15, 9, 0), IST) == "Tue 15 Sep 2026, 14:30", "naive datetimes are UTC"
    assert format_local(None, IST) is None


def test_describe_range():
    assert describe_range("2026-09-15", None) == "15 Sep 2026"
    assert describe_range("2026-08-31", "2026-08-01") == "1 Aug 2026 to 31 Aug 2026"


def test_an_unknown_timezone_falls_back_to_utc():
    assert resolve_tz("Not/AZone") == ZoneInfo("UTC")
    assert resolve_tz(None) == ZoneInfo("UTC")
    assert resolve_tz(KOLKATA) == IST


# ---------------------------------------------------------------------------
# Against the database
# ---------------------------------------------------------------------------

def test_a_late_evening_meeting_lands_on_the_users_day_not_the_utc_day(db, user):
    """23:30 IST on the 15th is 18:00 UTC on the 15th; 00:30 IST on the 16th is 19:00 UTC on the 15th."""
    late = add_meeting(db, user.id, when=utc(2026, 9, 15, 18, 0), title="Late call")
    after_midnight = add_meeting(db, user.id, when=utc(2026, 9, 15, 19, 0), title="Past midnight")

    on_15th = list_meetings(user_id=str(user.id), tz_name=KOLKATA, start_date="2026-09-15")
    on_16th = list_meetings(user_id=str(user.id), tz_name=KOLKATA, start_date="2026-09-16")

    assert [m["id"] for m in on_15th["meetings"]] == [str(late)]
    assert on_15th["meetings"][0]["date"] == "Tue 15 Sep 2026, 23:30"
    assert [m["id"] for m in on_16th["meetings"]] == [str(after_midnight)]


def test_a_scheduled_meeting_is_dated_by_its_scheduled_time(db, user):
    scheduled = add_meeting(db, user.id, when=utc(2026, 9, 15, 9, 0), scheduled=True)

    result = list_meetings(user_id=str(user.id), tz_name=KOLKATA, start_date="2026-09-15")

    assert [m["id"] for m in result["meetings"]] == [str(scheduled)], "dated by created_at (2020) instead"


def test_the_meeting_date_filter_can_use_the_expression_index(db, user):
    """
    If meeting_date_expr() ever drifts from the indexed expression, Postgres
    silently stops using the index. Seq scans are off so a near-empty table
    still shows whether the index *can* serve the filter.
    """
    query = (
        db.query(Meeting.id)
        .filter(Meeting.user_id == user.id, meeting_date_expr() >= utc(2026, 8, 1), meeting_date_expr() < utc(2026, 9, 1))
    )
    sql = query.statement.compile(db.get_bind(), compile_kwargs={"literal_binds": True})

    db.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(db.execute(text(f"EXPLAIN {sql}")).scalars())
    db.rollback()

    assert "ix_meetings_user_meeting_date" in plan, plan
