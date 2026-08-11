"""Suppression integration checks (Phase 3).

Verifies the ONE canonical suppression rule -- app.findings_service.
is_finding_suppressed, keyed off the notification_status the email pass
persists (region: app.region_suppression; hospital: app.hospital_service) --
flows into the Master aggregation and the HR-actionable set. No emails sent.
"""

from datetime import date
from types import SimpleNamespace

from app.findings_service import (
    SUPPRESSED_NOTIFICATION_STATUSES,
    is_finding_suppressed,
)
from app.master_attention_service import FindingType, build_attention_records
from app.suppression_service import suppressed_finding_ids
from ui.master_page import kpi_counts

HR_MSG = "Worked 6.2h (10:15–16:25), below the 7.5h minimum. Review Required."


def _finding(code, name, rule, notif=None):
    return SimpleNamespace(
        finding_id=code, employee_code=code, employee_name=name, rule_name=rule,
        visit_date=date(2026, 8, 7), message=(HR_MSG if rule == "HOURS_WORKED" else "loc"),
        notification_status=notif,
        concentration_percent=45.0 if rule == "SAME_LOCATION" else None,
        matched_visit_count=3 if rule == "SAME_LOCATION" else None,
        valid_visit_count=7 if rule == "SAME_LOCATION" else None,
        radius_meters=50 if rule == "SAME_LOCATION" else None,
        threshold_percent=30.0 if rule == "SAME_LOCATION" else None,
    )


# Region map keyed the way suppression_service keys it: (code, 'dd-mm-YYYY').
def _region_map(pairs):
    return {(code, "07-08-2026"): region for code, region in pairs}


# Mirrors get_current_attention_records / the HR table: drop suppressed ids
# using the canonical suppression_service (region map injected for the test).
def _actionable(findings, region_map):
    suppressed = suppressed_finding_ids(findings, region_map)
    return [f for f in findings if f.finding_id not in suppressed]


def test_is_finding_suppressed_persisted_helper():
    assert SUPPRESSED_NOTIFICATION_STATUSES == ("Suppressed - Region Rule", "Hospital Suppressed")
    assert is_finding_suppressed(_finding("E", "E", "SAME_LOCATION", "Suppressed - Region Rule"))
    assert is_finding_suppressed(_finding("E", "E", "SAME_LOCATION", "Hospital Suppressed"))
    assert not is_finding_suppressed(_finding("E", "E", "SAME_LOCATION", None))


def test_live_region_suppression_even_without_persisted_status():
    # HR findings whose notification_status is None (never emailed yet) must
    # STILL be suppressed live when their region is Kerala/Punjab.
    findings = [
        _finding("E_HR", "Hours Hank", "HOURS_WORKED"),        # Maharashtra -> actionable
        _finding("E_KL", "Kerala Ken", "HOURS_WORKED"),        # Kerala      -> suppressed live
        _finding("E_PB", "Punjab Pam", "HOURS_WORKED"),        # Punjab      -> suppressed live
    ]
    region_map = _region_map([("E_HR", "Maharashtra"), ("E_KL", "Kerala"), ("E_PB", "Punjab")])
    suppressed = suppressed_finding_ids(findings, region_map)
    assert suppressed == {"E_KL", "E_PB"}


def _records(findings, region_map):
    return build_attention_records(
        _actionable(findings, region_map), resolve_hierarchy=lambda c, n: ("BM", "Rajan RBM")
    )


def test_region_suppressed_employee_absent_from_master_and_kpis():
    findings = [
        _finding("E_OK", "Valid Vera", "SAME_LOCATION"),       # Maharashtra actionable
        _finding("E_HR", "Hours Hank", "HOURS_WORKED"),        # Maharashtra actionable
        _finding("E_KL", "Kerala Ken", "HOURS_WORKED"),        # Kerala -> live-suppressed
        _finding("E_PB", "Punjab Pam", "SAME_LOCATION"),       # Punjab -> live-suppressed
    ]
    region_map = _region_map([
        ("E_OK", "Maharashtra"), ("E_HR", "Gujarat"), ("E_KL", "Kerala"), ("E_PB", "Punjab"),
    ])
    recs = _records(findings, region_map)
    assert {r.employee_code for r in recs} == {"E_OK", "E_HR"}
    assert kpi_counts(recs) == {"flagged": 2, "location": 1, "low_hours": 1, "multiple": 0}


def test_hospital_suppressed_location_absent():
    findings = [
        _finding("E_OK", "Valid Vera", "SAME_LOCATION"),
        _finding("E_HOSP", "Hospital Hugh", "SAME_LOCATION", "Hospital Suppressed"),
    ]
    region_map = _region_map([("E_OK", "Maharashtra"), ("E_HOSP", "Maharashtra")])
    recs = _records(findings, region_map)
    assert {r.employee_code for r in recs} == {"E_OK"}


def test_hr_actionable_set_excludes_suppressed():
    hr = [
        _finding("E_HR", "Hours Hank", "HOURS_WORKED"),
        _finding("E_KL", "Kerala Ken", "HOURS_WORKED"),
    ]
    region_map = _region_map([("E_HR", "Maharashtra"), ("E_KL", "Kerala")])
    actionable_hr = [f for f in _actionable(hr, region_map) if f.rule_name == "HOURS_WORKED"]
    assert {f.employee_code for f in actionable_hr} == {"E_HR"}


def test_employee_with_both_valid_findings_appears_once():
    findings = [
        _finding("E_BOTH", "Both Bea", "SAME_LOCATION"),
        _finding("E_BOTH", "Both Bea", "HOURS_WORKED"),
    ]
    recs = _records(findings, _region_map([("E_BOTH", "Maharashtra")]))
    assert len(recs) == 1
    assert {af.finding_type for af in recs[0].applicable_findings} == {
        FindingType.LOCATION, FindingType.LOW_WORKING_HOURS
    }


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Suppression integration: all checks passed")
