"""Tests for the DOJ (Date of Joining) column app.hierarchy_parser now
extracts alongside every other per-employee field -- pure grid-based, no
database, mirroring test_hierarchy_service.py's own parser-level tests
(see its "Parser-level: the vacant-reset bug fix" section)."""

import datetime

from app.hierarchy_parser import _parse_sheet


def _grid(rows):
    header = ["Emp Code", "Name", "Designation", "Mobile", "Email-Id", "DOJ"]
    return [header] + rows


def test_doj_iso_string_cell_is_normalized_unchanged():
    grid = _grid([["B1", "Bilal BM", "BM", None, "b1@example.com", "2026-04-15"]])
    records, _ = _parse_sheet(grid, "Onyx", "Sheet1")
    assert records[0]["doj"] == "2026-04-15"


def test_doj_slash_format_cell_is_normalized_to_iso():
    grid = _grid([["B1", "Bilal BM", "BM", None, "b1@example.com", "15/04/2026"]])
    records, _ = _parse_sheet(grid, "Onyx", "Sheet1")
    assert records[0]["doj"] == "2026-04-15"


def test_doj_excel_datetime_cell_is_normalized_to_date_only():
    """openpyxl (data_only=True) hands back a python datetime for an Excel
    date-formatted cell, not a string -- must still normalize cleanly."""
    grid = _grid([["B1", "Bilal BM", "BM", None, "b1@example.com", datetime.datetime(2026, 4, 15, 0, 0)]])
    records, _ = _parse_sheet(grid, "Onyx", "Sheet1")
    assert records[0]["doj"] == "2026-04-15"


def test_doj_blank_cell_is_empty_string_not_none_or_nan():
    grid = _grid([["B1", "Bilal BM", "BM", None, "b1@example.com", None]])
    records, _ = _parse_sheet(grid, "Onyx", "Sheet1")
    assert records[0]["doj"] == ""


def test_doj_unparseable_cell_is_empty_string():
    grid = _grid([["B1", "Bilal BM", "BM", None, "b1@example.com", "not a date"]])
    records, _ = _parse_sheet(grid, "Onyx", "Sheet1")
    assert records[0]["doj"] == ""


def test_vacant_position_produces_no_record_at_all_doj_never_invented():
    """A vacant row is skipped entirely (see app.hierarchy_parser's own
    _is_vacant convention) -- there is no record, and therefore no DOJ, to
    invent or infer for it."""
    grid = _grid([["B1", "Vacant", "BM", None, None, None]])
    records, stats = _parse_sheet(grid, "Onyx", "Sheet1")
    assert records == []
    assert stats["vacant_ignored"] == 1


def test_workbook_without_a_doj_column_still_parses_doj_blank():
    """No DOJ header present at all -- every other field still parses
    fine, and doj comes back blank (never guessed at), same outcome as
    "no DOJ on file" downstream."""
    grid = [
        ["Emp Code", "Name", "Designation", "Mobile", "Email-Id"],
        ["B1", "Bilal BM", "BM", None, "b1@example.com"],
    ]
    records, _ = _parse_sheet(grid, "Onyx", "Sheet1")
    assert records[0]["doj"] == ""
