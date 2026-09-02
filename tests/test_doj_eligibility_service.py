"""Tests for the shared DOJ (Date of Joining) monthly-eligibility rule
(app.doj_eligibility_service) -- pure functions, no database, mirroring
test_hierarchy_service.py's own "pure in-memory, no DB" philosophy.

Both RGD Coverage (app.work_distribution_service) and Manager Work
Allocation (app.manager_work_allocation_service /
_rbm_service) read the SAME monthly_status()/is_eligible_for_month()/
resolve_doj() this file tests directly -- see their own test files
(test_work_distribution_doj.py, test_manager_work_allocation_doj.py) for
the end-to-end integration of this rule into each engine.
"""

from datetime import date

from app.doj_eligibility_service import (
    ACTIVE,
    NOT_YET_JOINED,
    STARTED_MONTH,
    is_eligible_for_month,
    monthly_status,
    parse_doj,
    resolve_doj,
)


def test_parse_doj_iso_string():
    assert parse_doj("2026-04-15") == date(2026, 4, 15)


def test_parse_doj_blank_or_none_is_none():
    assert parse_doj("") is None
    assert parse_doj(None) is None


def test_parse_doj_unparseable_is_none():
    assert parse_doj("not a date") is None


def test_no_doj_is_always_active():
    """Vacant positions never get a hierarchy row at all (see
    app.hierarchy_parser._is_vacant), and any employee this module can't
    resolve a DOJ for behaves identically -- existing behavior unchanged."""
    assert monthly_status(None, 2026, 3) == ACTIVE
    assert is_eligible_for_month(None, 2026, 3) is True


def test_joined_before_the_six_month_period_is_active_every_month():
    """Employee who joined well before any month in question -- unaffected
    by DOJ eligibility, exactly as before this feature existed."""
    doj = date(2024, 1, 1)
    for month in range(1, 7):
        assert monthly_status(doj, 2026, month) == ACTIVE
        assert is_eligible_for_month(doj, 2026, month) is True


def test_doj_after_month_end_is_not_yet_joined():
    """DOJ is after a particular month's end -> NOT_YET_JOINED, and
    therefore not eligible."""
    doj = date(2026, 4, 15)
    assert monthly_status(doj, 2026, 1) == NOT_YET_JOINED
    assert monthly_status(doj, 2026, 3) == NOT_YET_JOINED
    assert is_eligible_for_month(doj, 2026, 3) is False


def test_doj_within_month_is_started_month_and_eligible():
    """Employee who joined DURING one of the historical months -- the
    "started month" case: still eligible (counted), just labeled
    differently than a fully active month."""
    doj = date(2026, 4, 15)
    assert monthly_status(doj, 2026, 4) == STARTED_MONTH
    assert is_eligible_for_month(doj, 2026, 4) is True


def test_doj_before_the_month_is_active():
    """DOJ on the 1st of April is STARTED_MONTH for April itself (joined
    within it) but plain ACTIVE for every month after."""
    doj = date(2026, 4, 1)
    assert monthly_status(doj, 2026, 4) == STARTED_MONTH
    assert monthly_status(doj, 2026, 5) == ACTIVE


def test_doj_on_last_day_of_month_is_started_month_not_not_yet_joined():
    """Edge case: DOJ falls exactly on the month's last calendar day --
    still eligible (STARTED_MONTH), never NOT_YET_JOINED (which requires
    DOJ strictly AFTER the month-end)."""
    doj = date(2026, 2, 28)  # last day of Feb 2026 (not a leap year)
    assert monthly_status(doj, 2026, 2) == STARTED_MONTH
    assert is_eligible_for_month(doj, 2026, 2) is True


def test_unresolvable_period_never_restricts():
    """If the caller couldn't determine which (year, month) to compare
    against (e.g. an unparseable period label), monthly_status() must not
    guess -- ACTIVE, existing behavior unchanged, even for an employee with
    a real, future DOJ."""
    doj = date(2099, 1, 1)
    assert monthly_status(doj, None, None) == ACTIVE
    assert is_eligible_for_month(doj, None, None) is True


def test_resolve_doj_prefers_code_over_name():
    by_code = {"E1": date(2026, 1, 1)}
    by_name = {"someone else": date(2020, 1, 1)}
    assert resolve_doj("E1", "Someone Else", by_code, by_name) == date(2026, 1, 1)


def test_resolve_doj_falls_back_to_name_when_code_missing_or_unmapped():
    by_code = {}
    by_name = {"bilal bm": date(2026, 4, 15)}
    assert resolve_doj(None, "Bilal BM", by_code, by_name) == date(2026, 4, 15)
    assert resolve_doj("UNKNOWN_CODE", "Bilal BM", by_code, by_name) == date(2026, 4, 15)


def test_resolve_doj_none_when_neither_found():
    assert resolve_doj("UNKNOWN", "Nobody", {}, {}) is None
