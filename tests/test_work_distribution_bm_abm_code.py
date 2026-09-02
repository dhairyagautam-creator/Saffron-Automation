"""Tests for the 2026-08 Work Distribution production fix:

1. The parser requires "BM Code"/"ABM Code" columns (the real hierarchy
   column names), not the old "BM"/"ABM".
2. Employee identity is the Employee Code, never the name -- two different
   real employees who share a name must remain two separate findings/
   books throughout process_work_distribution_report and
   get_employee_doctors.
"""

import openpyxl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.work_distribution_service as wds
from app.work_distribution_parser import COLUMN_SYNONYMS, parse_work_distribution_report
from database.connection import Base


# --- 1. Column-name fix (parser level) --------------------------------------

def test_column_synonyms_require_bm_code_and_abm_code_not_bare_bm_abm():
    assert "BM Code" in COLUMN_SYNONYMS
    assert "ABM Code" in COLUMN_SYNONYMS
    assert "BM" not in COLUMN_SYNONYMS
    assert "ABM" not in COLUMN_SYNONYMS
    assert COLUMN_SYNONYMS["BM Code"] == ["bm code"]
    assert COLUMN_SYNONYMS["ABM Code"] == ["abm code"]
    # No alias/backward-compat entry for the bare old names anywhere in
    # the accepted synonym lists.
    all_synonyms = {s for syns in COLUMN_SYNONYMS.values() for s in syns}
    assert "bm" not in all_synonyms
    assert "abm" not in all_synonyms


HEADER_ROW = [
    "Division", "Dr. Code", "Dr. Name", "Speciality", "Category", "ABM RGD",
    "City", "HQ", "Region", "BM Code", "ABM Code", "BM Visit Apr-2026", "ABM Visit Apr-2026",
]
DATA_ROW = [
    "Onyx", "D1", "Dr. Test", "General", "B-RGD", "A-RGD",
    "City", "HQ", "Region", "12345", "99999", "04,12", "04",
]


def _write_workbook(path, header_row) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header_row)
    ws.append(DATA_ROW)
    wb.save(path)


def test_parser_accepts_bm_code_abm_code_headers(tmp_path):
    path = tmp_path / "real_report.xlsx"
    _write_workbook(path, HEADER_ROW)

    result = parse_work_distribution_report(str(path))

    assert result["success"] is True
    assert result["missing_columns"] == []
    [doctor] = result["doctors"]
    assert doctor["bm_code"] == "12345"
    assert doctor["abm_code"] == "99999"


def test_parser_rejects_old_bare_bm_abm_headers():
    """A file using the OLD "BM"/"ABM" header text (no "Code") must no
    longer validate -- confirms the parser genuinely stopped depending on
    those literal column names, not just added an alias alongside them."""
    old_header = [h if h not in ("BM Code", "ABM Code") else h.replace(" Code", "") for h in HEADER_ROW]
    assert "BM" in old_header and "ABM" in old_header  # sanity-check the fixture itself

    def _write(path):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(old_header)
        ws.append(DATA_ROW)
        wb.save(path)

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "old_report.xlsx"
        _write(path)
        result = parse_work_distribution_report(str(path))

    assert result["success"] is False
    assert "BM Code" in result["missing_columns"]
    assert "ABM Code" in result["missing_columns"]


# --- 2. Employee identity = code, not name (service level) -----------------

def _in_memory_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _isolated_db_and_hierarchy(monkeypatch):
    monkeypatch.setattr("database.connection._ConfigSession", _in_memory_session_factory())
    monkeypatch.setattr(wds, "load_doj_by_code", lambda: {})
    monkeypatch.setattr(wds, "load_doj_by_name", lambda: {})


def _doctor(bm_code=None, abm_code=None, doctor_code="D1", category="B-RGD", abm_rgd="A-RGD",
            bm_visits=150, abm_visits=150, **overrides):
    doctor = {
        "division": "Onyx", "doctor_code": doctor_code, "doctor_name": f"Dr. {doctor_code}",
        "speciality": "General", "category": category, "abm_rgd": abm_rgd,
        "city": "City", "hq": "HQ", "region": "Region",
        "bm_code": bm_code, "abm_code": abm_code,
        "bm_visit_count": bm_visits, "abm_visit_count": abm_visits, "period_label": "Apr-2026",
    }
    doctor.update(overrides)
    return doctor


def test_same_name_different_code_remain_two_separate_employees(monkeypatch):
    """The exact case the fix targets: two real BMs both named "Rahul
    Sharma", codes 12345 and 67890 -- must produce two separate findings
    with two separate, non-overlapping doctor books, never merged into one."""
    monkeypatch.setattr(wds, "find_by_employee_code", lambda code: {"employee_name": "Rahul Sharma"})

    doctors = [
        _doctor(bm_code="12345", doctor_code="D1", bm_visits=150),
        _doctor(bm_code="67890", doctor_code="D2", bm_visits=0),  # would flag if wrongly merged into D1's book
    ]
    result = wds.process_work_distribution_report(doctors)

    assert result["bm_count"] == 2  # two distinct BM groups, not one
    findings = wds.get_all_findings()
    assert len(findings) == 2
    codes = {f["employee_code"] for f in findings}
    assert codes == {"12345", "67890"}
    # Both display as "Rahul Sharma" (same name) but are tracked separately.
    assert all(f["employee_name"] == "Rahul Sharma" for f in findings)

    by_code = {f["employee_code"]: f for f in findings}
    assert by_code["12345"]["status"] != wds.STATUS_FLAGGED  # 150 calls, healthy
    assert by_code["67890"]["status"] == wds.STATUS_FLAGGED  # 0 calls, flagged
    assert by_code["12345"]["total_doctors"] == 1
    assert by_code["67890"]["total_doctors"] == 1

    # get_employee_doctors keyed by code -- each employee's book stays
    # separate even though both display under the identical name.
    doctors_12345 = wds.get_employee_doctors("12345", "BM")
    doctors_67890 = wds.get_employee_doctors("67890", "BM")
    assert {d["doctor_code"] for d in doctors_12345} == {"D1"}
    assert {d["doctor_code"] for d in doctors_67890} == {"D2"}


def test_same_code_different_rows_combine_into_one_employee(monkeypatch):
    """The normal case: multiple doctor rows for the SAME code correctly
    aggregate into one employee's book, exactly as before."""
    monkeypatch.setattr(wds, "find_by_employee_code", lambda code: None)  # unresolved -- falls back to the code itself

    doctors = [
        _doctor(bm_code="12345", doctor_code="D1", bm_visits=10),
        _doctor(bm_code="12345", doctor_code="D2", bm_visits=20),
    ]
    result = wds.process_work_distribution_report(doctors)

    assert result["bm_count"] == 1
    [finding] = wds.get_all_findings()
    assert finding["employee_code"] == "12345"
    assert finding["total_doctors"] == 2
    assert finding["total_calls"] == 30


def test_unresolved_code_falls_back_to_showing_the_code_as_display_name(monkeypatch):
    """Existing-behavior guarantee: an employee whose code has no
    hierarchy match still shows SOMETHING (never blank) -- the code
    itself, rather than crashing or silently vanishing."""
    monkeypatch.setattr(wds, "find_by_employee_code", lambda code: None)

    wds.process_work_distribution_report([_doctor(bm_code="NOCODE99", bm_visits=150)])
    [finding] = wds.get_all_findings()
    assert finding["employee_name"] == "NOCODE99"
    assert finding["employee_code"] == "NOCODE99"


def test_vacant_bm_code_is_excluded_exactly_as_vacant_names_were():
    doctors = [_doctor(bm_code="Vacant_Someone", bm_visits=150)]
    result = wds.process_work_distribution_report(doctors)
    assert result["bm_count"] == 0
    assert wds.get_all_findings() == []
