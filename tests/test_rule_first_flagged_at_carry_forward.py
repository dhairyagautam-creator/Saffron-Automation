"""InvestigationFinding.first_flagged_at must survive rules/same_location.py's
and rules/hours_worked.py's own delete+recreate cycle when a finding still
matches on re-run -- the exact mechanism app/notification_service.py's
STALE_FINDING_AGE_DAYS gate depends on (see that module's own module-level
comment). created_at, by contrast, is deliberately re-stamped fresh on
every re-run -- confirmed here too, as the contrasting case.

Real evaluate() calls against an isolated in-memory database -- no mocking
of the rule logic itself, matching the original 2.3.2 fix's own stated
verification approach ("seeded a finding, re-ran the rule, confirmed the
outcome survived the delete+reinsert cycle").
"""

from datetime import timedelta

import openpyxl
import pandas as pd
import pytest

from app.coordinates import parse_coordinates
from app.rule_parameters import set_parameter
from database.connection import get_session, utcnow
from database.import_service import save_import
from database.models import InvestigationFinding
from rules import hours_worked, same_location
from tests.db_isolation import isolate_database

_PV_MODULE_KEY = "employee_module"

REQUIRED_COLUMNS = [
    "Division", "Zone", "Region", "Employee Name", "Employee Code", "Reporting HQ", "Desig.",
    "Reporting Sr. Name", "Reporting Sr. Emp Code", "Reporting Sr. Desig.", "Date", "Day",
    "Customer Type", "Code", "Name", "Category", "Specialty", "City/Place",
    "Visit Registration Time", "Visit Registration Date", "Actual Lat-Long",
]


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    isolate_database(monkeypatch)


def _visit_row(emp_code: str) -> dict:
    return {
        "Division": "Onyx", "Zone": "Z1", "Region": "R1", "Employee Name": "Emp One",
        "Employee Code": emp_code, "Reporting HQ": "HQ1", "Desig.": "BM",
        "Reporting Sr. Name": "Sr One", "Reporting Sr. Emp Code": "S1", "Reporting Sr. Desig.": "RBM",
        "Date": "15-07-2026", "Day": "Wednesday", "Customer Type": "Doctor", "Code": "D1",
        "Name": "Dr One", "Category": "A", "Specialty": "Cardio", "City/Place": "City1",
        "Visit Registration Time": "10:00", "Visit Registration Date": "15-07-2026",
        "Actual Lat-Long": "19.0760,72.8777",
    }


def _save_single_visit_import(emp_code: str = "E1") -> int:
    df = pd.DataFrame([_visit_row(emp_code)])
    parse_coordinates(df)
    stats = save_import(df, "test_import.xlsx")
    return stats["import_id"]


def _write_hierarchy_workbook(path, emp_code: str = "E1") -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Emp Code", "Name", "Designation", "Mobile", "Email", "DOJ"])
    ws.append([emp_code, "Emp One", "BM", "9990000000", "emp1@example.com", ""])
    wb.save(path)


def _configure_hierarchy_for_same_location(tmp_path, emp_code: str = "E1") -> None:
    from app.hierarchy_parser import refresh_hierarchy
    from app.workbook_connections import set_connection

    hier_path = tmp_path / "onyx_hierarchy.xlsx"
    _write_hierarchy_workbook(hier_path, emp_code=emp_code)
    set_connection(_PV_MODULE_KEY, "Onyx", str(hier_path))
    refresh_hierarchy(_PV_MODULE_KEY)


def _set_permissive_same_location_params() -> None:
    # A single visit is enough to trigger a finding -- isolates this test
    # from the real production thresholds, which aren't what's being tested.
    set_parameter("SAME_LOCATION", "same_place_radius_meters", "1000")
    set_parameter("SAME_LOCATION", "concentration_threshold_percent", "0")
    set_parameter("SAME_LOCATION", "minimum_valid_gps_visits", "1")


def _the_finding(import_id: int, rule_name: str) -> InvestigationFinding:
    session = get_session()
    try:
        return (
            session.query(InvestigationFinding)
            .filter_by(import_id=import_id, rule_name=rule_name)
            .one()
        )
    finally:
        session.close()


def _backdate_first_flagged_at(finding_id: int, days_ago: int) -> None:
    session = get_session()
    try:
        row = session.query(InvestigationFinding).filter_by(finding_id=finding_id).one()
        row.first_flagged_at = utcnow() - timedelta(days=days_ago)
        session.commit()
    finally:
        session.close()


# --- SAME_LOCATION ---------------------------------------------------------

def test_same_location_first_flagged_at_survives_rerun_when_finding_still_matches(tmp_path):
    _set_permissive_same_location_params()
    _configure_hierarchy_for_same_location(tmp_path)
    import_id = _save_single_visit_import()

    same_location.evaluate(import_id)
    first_run = _the_finding(import_id, "SAME_LOCATION")
    original_created_at = first_run.created_at
    _backdate_first_flagged_at(first_run.finding_id, days_ago=30)
    backdated_value = _the_finding(import_id, "SAME_LOCATION").first_flagged_at

    # Re-run: same underlying data, so the exact same finding matches again
    # -- this is the delete+recreate cycle first_flagged_at must survive.
    same_location.evaluate(import_id)
    second_run = _the_finding(import_id, "SAME_LOCATION")

    assert second_run.first_flagged_at == backdated_value  # carried forward, untouched
    assert second_run.created_at != original_created_at  # re-stamped fresh, as expected/unavoidable


def test_same_location_genuinely_new_finding_gets_fresh_first_flagged_at(tmp_path):
    _set_permissive_same_location_params()
    _configure_hierarchy_for_same_location(tmp_path)
    import_id = _save_single_visit_import()

    before = utcnow()
    same_location.evaluate(import_id)  # first-ever run: nothing to carry forward from
    finding = _the_finding(import_id, "SAME_LOCATION")

    assert finding.first_flagged_at is not None
    assert before - timedelta(seconds=5) <= finding.first_flagged_at <= utcnow() + timedelta(seconds=5)


# --- HOURS_WORKED ------------------------------------------------------------
# No BM/ABM/hierarchy requirement (see rules/hours_worked.py's own docstring
# -- applies to every employee/day) -- a single visit already produces a
# 0.0h span, below any real minimum_hours_threshold, with no extra setup.

def test_hours_worked_first_flagged_at_survives_rerun_when_finding_still_matches():
    import_id = _save_single_visit_import()

    hours_worked.evaluate(import_id)
    first_run = _the_finding(import_id, "HOURS_WORKED")
    original_created_at = first_run.created_at
    _backdate_first_flagged_at(first_run.finding_id, days_ago=30)
    backdated_value = _the_finding(import_id, "HOURS_WORKED").first_flagged_at

    hours_worked.evaluate(import_id)
    second_run = _the_finding(import_id, "HOURS_WORKED")

    assert second_run.first_flagged_at == backdated_value
    assert second_run.created_at != original_created_at


def test_hours_worked_genuinely_new_finding_gets_fresh_first_flagged_at():
    import_id = _save_single_visit_import()

    before = utcnow()
    hours_worked.evaluate(import_id)
    finding = _the_finding(import_id, "HOURS_WORKED")

    assert finding.first_flagged_at is not None
    assert before - timedelta(seconds=5) <= finding.first_flagged_at <= utcnow() + timedelta(seconds=5)
