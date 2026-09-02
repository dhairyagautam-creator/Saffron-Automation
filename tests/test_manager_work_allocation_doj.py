"""End-to-end tests for DOJ (Date of Joining) eligibility in Manager Work
Allocation -- both engines: ABM (app.manager_work_allocation_service,
rolling AVERAGE) and RBM (app.manager_work_allocation_rbm_service, rolling
SUM/covered-if-any). Verifies process_manager_work_allocation_report/
process_rbm_report, plus each engine's own get_employee_bm_monthly_history,
against a real (in-memory) database -- the DOJ RULE itself is already
covered directly, DB-free, in test_doj_eligibility_service.py; these tests
only verify it's actually wired into both engines identically (same
app.doj_eligibility_service functions), so the two views can never
disagree about a given BM's eligibility.

The in-memory database is wired in the same way as
test_work_distribution_doj.py -- see that file's own docstring for why one
monkeypatch of database.connection._ConfigSession covers every service
module here (including each engine's own parameters service, whose
get_all()/get_rbm_flag_tiers() fall back to DEFAULTS with no Settings
row)."""

import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.manager_work_allocation_rbm_service as rbm_svc
import app.manager_work_allocation_service as abm_svc
from database.connection import Base


def _in_memory_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _use_in_memory_db(monkeypatch):
    monkeypatch.setattr("database.connection._ConfigSession", _in_memory_session_factory())


def _use_doj_map(monkeypatch, module, by_code: dict | None = None, by_name: dict | None = None):
    monkeypatch.setattr(module, "load_doj_by_code", lambda: by_code or {})
    monkeypatch.setattr(module, "load_doj_by_name", lambda: by_name or {})


def _record(manager_code, manager_name, manager_designation, bm_code, bm_name, month, joint_days, division="Onyx"):
    return {
        "division": division,
        "emp_code": manager_code,
        "emp_name": manager_name,
        "emp_designation": manager_designation,
        "team_emp_code": bm_code,
        "team_emp_name": bm_name,
        "team_emp_designation": "BM",
        "month": month,
        "joint_days": joint_days,
    }


# --- ABM engine (rolling AVERAGE) -------------------------------------------


def test_abm_bm_already_active_before_window_is_averaged_over_every_month(monkeypatch):
    """Employee who joined before the six-month period -- every retained
    month counts, exactly as before this feature existed."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, abm_svc, by_code={"B1": datetime.date(2020, 1, 1)})

    records = [
        _record("A1", "Amit ABM", "ABM", "B1", "Bilal BM", "Jan-2026", 5),
        _record("A1", "Amit ABM", "ABM", "B1", "Bilal BM", "Feb-2026", 5),
    ]
    abm_svc.process_manager_work_allocation_report(records)
    detail = abm_svc.get_employee_bm_details("Amit ABM")[0]
    assert detail["status"] == abm_svc.STATUS_PASS
    assert detail["joint_days"] == "5"


def test_abm_pre_join_zero_months_excluded_from_average_flips_fail_to_pass(monkeypatch):
    """The core regression: a BM who joined mid-window has a phantom
    pre-join record (0 joint days, e.g. the system assigned the pair
    before their actual first day). Naively averaging over all 3 retained
    months gives (0+5+5)/3 = 3.33 -- below the default 4-day threshold, a
    false FAIL. Excluding the pre-join month (DOJ-aware) gives (5+5)/2 =
    5.0 -- correctly PASS."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, abm_svc, by_code={"B1": datetime.date(2026, 2, 10)})

    records = [
        _record("A1", "Amit ABM", "ABM", "B1", "Bilal BM", "Jan-2026", 0),
        _record("A1", "Amit ABM", "ABM", "B1", "Bilal BM", "Feb-2026", 5),
        _record("A1", "Amit ABM", "ABM", "B1", "Bilal BM", "Mar-2026", 5),
    ]
    abm_svc.process_manager_work_allocation_report(records)
    detail = abm_svc.get_employee_bm_details("Amit ABM")[0]

    assert detail["status"] == abm_svc.STATUS_PASS
    assert detail["joint_days"] == "5"


def test_abm_entirely_not_yet_joined_bm_is_not_scored_as_zero_failure(monkeypatch):
    """A BM whose DOJ is after every retained month's own end -- must be
    NOT YET JOINED, never a 0-average FAIL, and must not drag the ABM's
    own finding into Flagged on their account alone."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, abm_svc, by_code={"B1": datetime.date(2026, 4, 1)})

    records = [
        _record("A1", "Amit ABM", "ABM", "B1", "Bilal BM", "Jan-2026", 0),
        _record("A1", "Amit ABM", "ABM", "B1", "Bilal BM", "Feb-2026", 0),
    ]
    result = abm_svc.process_manager_work_allocation_report(records)
    detail = abm_svc.get_employee_bm_details("Amit ABM")[0]
    findings = abm_svc.get_all_findings()

    assert detail["status"] == "NOT YET JOINED"
    assert result["flagged_count"] == 0
    assert findings[0]["status"] == abm_svc.STATUS_PASS
    assert findings[0]["failed_bms"] == 0


def test_abm_bm_with_no_doj_on_file_is_unaffected(monkeypatch):
    """No DOJ resolvable (vacant, or simply not found in the hierarchy) ->
    every month counts, exactly as before this feature existed."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, abm_svc)  # empty maps

    records = [
        _record("A1", "Amit ABM", "ABM", "B1", "Bilal BM", "Jan-2026", 0),
        _record("A1", "Amit ABM", "ABM", "B1", "Bilal BM", "Feb-2026", 0),
    ]
    abm_svc.process_manager_work_allocation_report(records)
    detail = abm_svc.get_employee_bm_details("Amit ABM")[0]
    assert detail["status"] == abm_svc.STATUS_BM_FAIL  # a real 0-average failure, unaffected


def test_abm_monthly_history_flags_not_yet_joined_month_separately_and_excludes_it_from_average(monkeypatch):
    """The Employee Details trend view must be able to distinguish a
    not-yet-joined month from "no record at all" (2026-08 presentation
    fix) via the separate `not_yet_joined_months` key, while `monthly`
    itself stays numeric-only (still excluded from it, unchanged -- this
    is the same dict app.work_distribution_notification_service sums for
    its own email summary, so it must never carry a non-numeric value),
    and the displayed average is still unaffected."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, abm_svc, by_code={"B1": datetime.date(2026, 2, 10)})

    records = [
        _record("A1", "Amit ABM", "ABM", "B1", "Bilal BM", "Jan-2026", 0),
        _record("A1", "Amit ABM", "ABM", "B1", "Bilal BM", "Feb-2026", 5),
    ]
    abm_svc.process_manager_work_allocation_report(records)
    history = abm_svc.get_employee_bm_monthly_history("Amit ABM")
    bm_history = history["bms"][0]

    assert "Jan-2026" not in bm_history["monthly"]
    assert bm_history["not_yet_joined_months"] == {"Jan-2026"}
    assert bm_history["monthly"]["Feb-2026"] == 5
    assert bm_history["average"] == "5"  # unaffected: still excludes Jan-2026


# --- RBM engine (rolling SUM / covered-if-any) ------------------------------


def test_rbm_pre_join_phantom_days_excluded_flips_covered_to_not_covered(monkeypatch):
    """A BM's pre-join record (5 joint days logged before they actually
    started, in Jan) must not make them falsely "covered" for a window
    where they in fact never worked with this RBM after joining in Feb
    (their own started month, correctly still eligible with its real,
    zero, reported value)."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, rbm_svc, by_code={"B1": datetime.date(2026, 2, 1)})

    records = [
        _record("R1", "Rahul RBM", "RBM", "B1", "Bilal BM", "Jan-2026", 5),
        _record("R1", "Rahul RBM", "RBM", "B1", "Bilal BM", "Feb-2026", 0),
    ]
    rbm_svc.process_rbm_report(records)
    detail = rbm_svc.get_employee_bm_details("Rahul RBM")[0]
    assert detail["status"] == rbm_svc.STATUS_BM_NOT_COVERED


def test_rbm_entirely_not_yet_joined_bm_is_not_scored_as_missed(monkeypatch):
    """A BM whose DOJ is after every retained month -- NOT YET JOINED, not
    "No" (not covered), and excluded from the RBM's own missed-BM count
    and coverage percentage."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, rbm_svc, by_code={"B1": datetime.date(2026, 4, 1)})

    records = [
        _record("R1", "Rahul RBM", "RBM", "B1", "Bilal BM", "Jan-2026", 0),
        _record("R1", "Rahul RBM", "RBM", "B2", "Bashir BM", "Jan-2026", 3),
    ]
    rbm_svc.process_rbm_report(records)
    detail = rbm_svc.get_employee_bm_details("Rahul RBM")
    by_name = {d["subordinate_name"]: d for d in detail}
    findings = rbm_svc.get_all_findings()[0]

    assert by_name["Bilal BM"]["status"] == "NOT YET JOINED"
    assert by_name["Bashir BM"]["status"] == rbm_svc.STATUS_BM_COVERED
    # Total still counts both BMs (a headcount), but the not-yet-joined
    # one contributes to neither the missed count nor coverage %.
    assert findings["total_bms"] == 2
    assert findings["failed_bms"] == 0
    assert findings["coverage_percent"] == "100.0%"


def test_rbm_already_active_bm_coverage_unchanged(monkeypatch):
    """Regression: a BM who genuinely worked with their RBM at some point
    in the window, joined long ago -- still correctly covered."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, rbm_svc, by_code={"B1": datetime.date(2020, 1, 1)})

    records = [_record("R1", "Rahul RBM", "RBM", "B1", "Bilal BM", "Jan-2026", 3)]
    rbm_svc.process_rbm_report(records)
    detail = rbm_svc.get_employee_bm_details("Rahul RBM")[0]
    assert detail["status"] == rbm_svc.STATUS_BM_COVERED


def test_rbm_monthly_history_flags_not_yet_joined_month_separately_and_excludes_it_from_average(monkeypatch):
    """The RBM Employee Details trend view must be able to distinguish a
    not-yet-joined month from "no record at all" (2026-08 presentation
    fix, same as ABM's own trend view) via the separate
    `not_yet_joined_months` key, while `monthly` stays numeric-only and
    the DISPLAY ONLY average still excludes it (unchanged)."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, rbm_svc, by_code={"B1": datetime.date(2026, 3, 1)})

    records = [
        _record("R1", "Rahul RBM", "RBM", "B1", "Bilal BM", "Jan-2026", 5),
        _record("R1", "Rahul RBM", "RBM", "B1", "Bilal BM", "Mar-2026", 3),
    ]
    rbm_svc.process_rbm_report(records)
    history = rbm_svc.get_employee_bm_monthly_history("Rahul RBM")
    bm_history = history["bms"][0]

    assert "Jan-2026" not in bm_history["monthly"]
    assert bm_history["not_yet_joined_months"] == {"Jan-2026"}
    assert bm_history["monthly"]["Mar-2026"] == 3
    assert bm_history["average"] == "3"  # unaffected: still excludes Jan-2026
