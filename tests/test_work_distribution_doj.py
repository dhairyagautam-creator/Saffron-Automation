"""End-to-end tests for DOJ (Date of Joining) eligibility in RGD Coverage
(app.work_distribution_service) -- verifies process_work_distribution_report
and get_dashboard_summary against a real (in-memory) database, with the DOJ
lookup itself stubbed to a fixed map (see _use_doj_map below) rather than
exercising app.hierarchy_parser's own DB -- the DOJ RULE itself is already
covered directly, DB-free, in test_doj_eligibility_service.py.

The in-memory database is wired in by monkeypatching
database.connection._ConfigSession -- get_config_session() (used by every
service module here) reads that name fresh on every call, so one patch
covers app.work_distribution_service AND app.work_distribution_parameters_service
(whose get_all() falls back to its own DEFAULTS when no row exists, so no
Settings seeding is needed for these tests)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.work_distribution_service as wds
from database.connection import Base


def _in_memory_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _use_in_memory_db(monkeypatch):
    monkeypatch.setattr("database.connection._ConfigSession", _in_memory_session_factory())


def _use_doj_map(monkeypatch, by_name: dict):
    """Stubs both DOJ lookup tiers (see app.doj_eligibility_service.resolve_doj)
    -- load_doj_by_code always empty, so every lookup falls through to the
    name tier these tests actually exercise -- plus find_by_employee_code
    (used by _resolve_employee_name for the display name) always
    unresolved, so a fixture's `bm=`/`abm=` value is echoed back as-is
    rather than depending on this dev environment's real, unmocked
    hierarchy data (get_data_engine() is NOT the in-memory DB
    _use_in_memory_db wires up -- app.hierarchy_parser reads a separate
    engine entirely)."""
    monkeypatch.setattr(wds, "load_doj_by_code", lambda: {})
    monkeypatch.setattr(wds, "load_doj_by_name", lambda: by_name)
    monkeypatch.setattr(wds, "find_by_employee_code", lambda code: None)


def _doctor(bm=None, abm=None, category="B-RGD", abm_rgd="A-RGD", bm_visits=150, abm_visits=150, **overrides):
    doctor = {
        "division": "Onyx",
        "doctor_code": "D1",
        "doctor_name": "Dr. Test",
        "speciality": "General",
        "category": category,
        "abm_rgd": abm_rgd,
        "city": "City",
        "hq": "HQ",
        "region": "Region",
        "bm_code": bm,
        "abm_code": abm,
        "bm_visit_count": bm_visits,
        "abm_visit_count": abm_visits,
        "period_label": "Apr-2026",
    }
    doctor.update(overrides)
    return doctor


def test_bm_already_active_before_the_period_is_evaluated_normally(monkeypatch):
    """Employee who joined well before the reporting period -- unaffected
    by DOJ eligibility, exactly as before this feature existed."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, {"tara bm": __import__("datetime").date(2020, 1, 1)})

    result = wds.process_work_distribution_report([_doctor(bm="Tara BM", bm_visits=150)])
    findings = {f["employee_name"]: f for f in wds.get_all_findings()}

    assert result["flagged_count"] == 0
    assert findings["Tara BM"]["status"] != wds.STATUS_NOT_YET_JOINED


def test_bm_with_doj_after_period_month_end_is_not_yet_joined(monkeypatch):
    """Employee whose DOJ is after this period's own month-end -> shown as
    NOT YET JOINED, never evaluated against the KPI thresholds (their
    doctors are still 0-visit, which would otherwise flag them)."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, {"bilal bm": __import__("datetime").date(2026, 5, 1)})

    wds.process_work_distribution_report([_doctor(bm="Bilal BM", bm_visits=0)])
    findings = {f["employee_name"]: f for f in wds.get_all_findings()}

    assert findings["Bilal BM"]["status"] == "NOT YET JOINED"
    assert findings["Bilal BM"]["reason"] != "Calls below target"


def test_bm_who_started_during_the_period_is_evaluated_not_blocked(monkeypatch):
    """Employee who joined DURING this period's own month (a "started
    month") -- still evaluated under the existing business logic, not
    treated as NOT YET JOINED."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, {"amit bm": __import__("datetime").date(2026, 4, 10)})

    wds.process_work_distribution_report([_doctor(bm="Amit BM", bm_visits=150)])
    findings = {f["employee_name"]: f for f in wds.get_all_findings()}

    assert findings["Amit BM"]["status"] != "NOT YET JOINED"


def test_bm_with_no_doj_on_file_is_evaluated_normally(monkeypatch):
    """No DOJ resolvable at all (e.g. name not found in the hierarchy, or
    a vacant position -- which never even gets a hierarchy row) -> ACTIVE
    by default, evaluated exactly as before this feature existed."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, {})  # nobody resolves to a DOJ

    wds.process_work_distribution_report([_doctor(bm="Unknown BM", bm_visits=150)])
    findings = {f["employee_name"]: f for f in wds.get_all_findings()}

    assert findings["Unknown BM"]["status"] != "NOT YET JOINED"


def test_dashboard_average_excludes_not_yet_joined_rather_than_counting_zero(monkeypatch):
    """A month containing NOT YET JOINED employees: their (zero) coverage
    must not drag the dashboard's average_coverage down -- they are
    excluded from the average entirely."""
    import datetime

    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, {"future bm": datetime.date(2026, 5, 1)})

    # One fully-active, fully-covered BM (100% coverage) + one BM who
    # hasn't joined yet (0 visits -- would drag the average to 50% if
    # wrongly included).
    doctors = [
        _doctor(bm="Active BM", bm_visits=2, doctor_code="D1"),
        _doctor(bm="Future BM", bm_visits=0, doctor_code="D2"),
    ]
    wds.process_work_distribution_report(doctors)
    summary = wds.get_dashboard_summary()

    findings = {f["employee_name"]: f for f in wds.get_all_findings()}
    assert findings["Future BM"]["status"] == "NOT YET JOINED"
    # Only "Active BM" (100% coverage, 1 doctor with 2 visits) feeds the
    # average -- not diluted by "Future BM"'s excluded zero.
    assert summary["average_coverage"] == 100.0
    # Still counted as a headcount, just not a performance aggregate.
    assert summary["total_employees"] == 2


def test_existing_flagged_employee_unaffected_by_doj_feature(monkeypatch):
    """Regression: a real underperforming, already-active BM must still
    be Flagged exactly as before -- DOJ eligibility must never soften an
    otherwise-correct flag."""
    _use_in_memory_db(monkeypatch)
    _use_doj_map(monkeypatch, {"struggling bm": __import__("datetime").date(2020, 1, 1)})

    wds.process_work_distribution_report([_doctor(bm="Struggling BM", bm_visits=0)])
    findings = {f["employee_name"]: f for f in wds.get_all_findings()}

    assert findings["Struggling BM"]["status"] == wds.STATUS_FLAGGED
