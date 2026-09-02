"""Tests for the 2026-08 RGD Coverage vacancy-detection fix.

The real production file moved from embedding vacancy directly in the
BM/ABM Code (e.g. Code="Vacant_Lokesh Kumar Singh") to giving a vacant
position an ordinary-looking Code (e.g. "V02677") with the vacancy text
in a separate BM Name/ABM Name column instead (e.g. Name=
"Vacant_Harsh Vardhan"). app.work_distribution_service._is_vacant() now
checks both fields; app.work_distribution_parser now optionally captures
BM Name/ABM Name (see OPTIONAL_COLUMN_SYNONYMS) so there's something for
it to check. Employee-code identity (grouping/aggregation by BM Code/ABM
Code, never by name) is UNCHANGED -- this fix is additive to it, not a
reversion.
"""

import openpyxl
import pytest

import app.work_distribution_service as wds
from app.work_distribution_parser import parse_work_distribution_report


# --- _is_vacant() itself -----------------------------------------------

def test_normal_code_and_name_is_not_vacant():
    assert wds._is_vacant("SF2365", "Chandan Kumar") is False


def test_code_only_vacant_convention_still_recognized():
    """The OLDER real file's own convention -- vacancy embedded directly
    in the Code -- must keep working exactly as before this fix."""
    assert wds._is_vacant("Vacant_Lokesh Kumar Singh", "") is True


def test_ordinary_code_with_vacant_name_is_recognized():
    """The exact real case the fix targets: Code doesn't say "vacant",
    Name does."""
    assert wds._is_vacant("V02677", "Vacant_Harsh Vardhan") is True


def test_name_only_arg_defaults_safely_when_omitted():
    """Callers that don't pass a name at all (or pass None) must not
    crash and must fall back to the code-only check."""
    assert wds._is_vacant("SF2365") is False
    assert wds._is_vacant("SF2365", None) is False
    assert wds._is_vacant("Vacant_Someone", None) is True


def test_ordinary_name_containing_unrelated_text_is_not_misclassified():
    """A legitimate employee's name is never mistaken for a vacancy
    marker just because it happens to be unusual -- only the literal
    "vacant" substring (case-insensitive) counts, per the same convention
    app.hierarchy_parser._is_vacant already uses."""
    assert wds._is_vacant("SF9001", "Vasant Rao Deshmukh") is False
    assert wds._is_vacant("SF9002", "Vacancy Management Specialist") is False  # contains "vacan" but not "vacant"...


def test_vacant_substring_is_case_insensitive():
    assert wds._is_vacant("V1", "VACANT_Someone") is True
    assert wds._is_vacant("V2", "vacant_someone") is True


# --- End-to-end via process_work_distribution_report() -----------------

def _doctor(bm_code=None, bm_name="", abm_code=None, abm_name="", doctor_code="D1",
            category="B-RGD", abm_rgd="A-RGD", bm_visits=150, abm_visits=150, **overrides):
    doctor = {
        "division": "Onyx", "doctor_code": doctor_code, "doctor_name": f"Dr. {doctor_code}",
        "speciality": "General", "category": category, "abm_rgd": abm_rgd,
        "city": "City", "hq": "HQ", "region": "Region",
        "bm_code": bm_code, "abm_code": abm_code, "bm_name": bm_name, "abm_name": abm_name,
        "bm_visit_count": bm_visits, "abm_visit_count": abm_visits, "period_label": "Aug-2026",
    }
    doctor.update(overrides)
    return doctor


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from database.connection import Base

    def _factory():
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return sessionmaker(bind=engine, autoflush=False, autocommit=False)

    monkeypatch.setattr("database.connection._ConfigSession", _factory())
    monkeypatch.setattr(wds, "load_doj_by_code", lambda: {})
    monkeypatch.setattr(wds, "load_doj_by_name", lambda: {})
    monkeypatch.setattr(wds, "find_by_employee_code", lambda code: None)


def test_vacant_bm_by_name_is_excluded_from_findings_and_grouping():
    doctors = [
        _doctor(bm_code="V02677", bm_name="Vacant_Harsh Vardhan", doctor_code="D1"),
        _doctor(bm_code="SF2365", bm_name="Chandan Kumar", doctor_code="D2"),
    ]
    result = wds.process_work_distribution_report(doctors)

    assert result["bm_count"] == 1  # only the real BM, vacant one excluded
    findings = wds.get_all_findings()
    codes = {f["employee_code"] for f in findings}
    assert "V02677" not in codes
    assert "SF2365" in codes


def test_vacant_abm_by_name_is_excluded_from_findings_and_grouping():
    doctors = [
        _doctor(abm_code="V09999", abm_name="Vacant_Some ABM", doctor_code="D1"),
        _doctor(abm_code="SF1899", abm_name="Mohammad Shadab Ghani", doctor_code="D2"),
    ]
    result = wds.process_work_distribution_report(doctors)

    assert result["abm_count"] == 1
    findings = wds.get_all_findings()
    codes = {f["employee_code"] for f in findings}
    assert "V09999" not in codes
    assert "SF1899" in codes


def test_same_name_different_code_still_two_separate_employees():
    """Regression guard: this vacancy fix must not reintroduce name-based
    merging -- two real, distinct employees sharing a name (the exact
    case the employee-code identity migration fixed) must still be two
    separate findings."""
    doctors = [
        _doctor(bm_code="SF1904", bm_name="Ajay Kumar Singh", doctor_code="D1", bm_visits=150),
        _doctor(bm_code="SF0046", bm_name="Ajay Kumar Singh", doctor_code="D2", bm_visits=0),
    ]
    result = wds.process_work_distribution_report(doctors)

    assert result["bm_count"] == 2
    findings = wds.get_all_findings()
    codes = {f["employee_code"] for f in findings}
    assert codes == {"SF1904", "SF0046"}
    by_code = {f["employee_code"]: f for f in findings}
    assert by_code["SF1904"]["status"] != wds.STATUS_FLAGGED
    assert by_code["SF0046"]["status"] == wds.STATUS_FLAGGED


def test_doctors_with_no_bm_name_key_at_all_still_work():
    """Backward compatibility: a doctor dict built without a "bm_name"/
    "abm_name" key at all (e.g. an older caller, or a file with no Name
    columns -- see the parser test below) must not crash."""
    doctor = _doctor(bm_code="SF2365", doctor_code="D1")
    del doctor["bm_name"]
    del doctor["abm_name"]
    result = wds.process_work_distribution_report([doctor])
    assert result["bm_count"] == 1


# --- Parser: optional BM Name/ABM Name columns --------------------------

REAL_HEADER_ROW = [
    "Division", "Dr. Code", "Dr. Name", "Speciality", "Category", "ABM RGD",
    "City", "HQ", "Region", "BM Code", "BM Name", "ABM Code", "ABM Name",
    "BM Visit Aug-2026", "ABM Visit Aug-2026",
]
OLD_HEADER_ROW = [
    "Division", "Dr. Code", "Dr. Name", "Speciality", "Category", "ABM RGD",
    "City", "HQ", "Region", "BM Code", "ABM Code",
    "BM Visit Aug-2026", "ABM Visit Aug-2026",
]


def _write_workbook(path, header_row, data_rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header_row)
    for row in data_rows:
        ws.append(row)
    wb.save(path)


def test_parser_captures_bm_name_and_abm_name_from_real_style_file(tmp_path):
    """The current real production file structure -- BM Code, BM Name,
    ABM Code, ABM Name all present. Confirms vacancy information (the
    Name) is actually captured and flows through to the doctor dict."""
    path = tmp_path / "real_style_report.xlsx"
    _write_workbook(path, REAL_HEADER_ROW, [
        ["Onyx", "D1", "Dr. Vacant Book", "General", "B-RGD", "A-RGD",
         "City", "HQ", "Region", "V02677", "Vacant_Harsh Vardhan", "SF2157", "Sushil Kumar Sharma", "01,02", "01"],
        ["Onyx", "D2", "Dr. Real Book", "General", "B-RGD", "A-RGD",
         "City", "HQ", "Region", "SF2365", "Chandan Kumar", "SF1899", "Mohammad Shadab Ghani", "01,02,03", "01,02"],
    ])

    result = parse_work_distribution_report(str(path))

    assert result["success"] is True
    assert result["missing_columns"] == []
    vacant_row, real_row = result["doctors"]
    assert vacant_row["bm_code"] == "V02677"
    assert vacant_row["bm_name"] == "Vacant_Harsh Vardhan"
    assert vacant_row["abm_code"] == "SF2157"
    assert vacant_row["abm_name"] == "Sushil Kumar Sharma"
    assert real_row["bm_name"] == "Chandan Kumar"
    assert real_row["abm_name"] == "Mohammad Shadab Ghani"


def test_parser_still_accepts_older_file_with_no_name_columns(tmp_path):
    """BM Name/ABM Name are OPTIONAL -- an older file without them must
    still parse successfully, exactly as before this fix, with
    bm_name/abm_name simply blank."""
    path = tmp_path / "old_style_report.xlsx"
    _write_workbook(path, OLD_HEADER_ROW, [
        ["Onyx", "D1", "Dr. Real Book", "General", "B-RGD", "A-RGD",
         "City", "HQ", "Region", "SF2365", "SF1899", "01,02,03", "01,02"],
    ])

    result = parse_work_distribution_report(str(path))

    assert result["success"] is True
    assert result["missing_columns"] == []
    [doctor] = result["doctors"]
    assert doctor["bm_code"] == "SF2365"
    assert doctor["bm_name"] == ""
    assert doctor["abm_name"] == ""


def test_parser_still_rejects_file_missing_required_bm_abm_code(tmp_path):
    """Required-column validation is UNCHANGED by this fix -- a file
    missing BM Code/ABM Code still fails, Name columns or not."""
    header = [h for h in REAL_HEADER_ROW if h not in ("BM Code", "ABM Code")]
    path = tmp_path / "missing_code_report.xlsx"
    _write_workbook(path, header, [
        ["Onyx", "D1", "Dr. X", "General", "B-RGD", "A-RGD",
         "City", "HQ", "Region", "Vacant_Someone", "SF1899", "Name X", "01,02", "01"],
    ])

    result = parse_work_distribution_report(str(path))

    assert result["success"] is False
    assert "BM Code" in result["missing_columns"]
    assert "ABM Code" in result["missing_columns"]
