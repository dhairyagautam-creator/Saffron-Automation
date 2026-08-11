"""Aggregation checks for the Master page data layer (app/master_attention_service).

Pure: exercises build_attention_records with in-memory findings and an injected
hierarchy resolver -- no DB, no emails, no detectors.
"""

from datetime import date
from types import SimpleNamespace

from app.master_attention_service import (
    FindingType,
    Severity,
    build_attention_records,
    compute_severity,
    ApplicableFinding,
)

HR_MSG = "Worked 6.2h (10:15–16:25), below the 7.5h minimum. Review Required."


def _loc(code, name, day=7):
    return SimpleNamespace(
        finding_id=id((code, day, "loc")),
        employee_code=code, employee_name=name, rule_name="SAME_LOCATION",
        visit_date=date(2026, 8, day), message="Location cluster.",
        concentration_percent=45.0, matched_visit_count=3, valid_visit_count=7,
        radius_meters=50, threshold_percent=30.0,
    )


def _hr(code, name, day=7):
    return SimpleNamespace(
        finding_id=id((code, day, "hr")),
        employee_code=code, employee_name=name, rule_name="HOURS_WORKED",
        visit_date=date(2026, 8, day), message=HR_MSG,
        concentration_percent=None, matched_visit_count=None, valid_visit_count=None,
        radius_meters=None, threshold_percent=None,
    )


# Injected hierarchy: code -> (designation, rbm_name). Missing code -> (None, None).
_HIER = {
    "E_BM": ("BM", "Rajan RBM"),
    "E_ABM": ("ABM", "Rajan RBM"),
    "E_BM2": ("BM", "Rajan RBM"),
}


def _resolver(code, name):
    return _HIER.get(code, (None, None))


def _build(findings):
    return build_attention_records(findings, resolve_hierarchy=_resolver)


def test_location_only():
    recs = _build([_loc("E_BM", "Asha")])
    assert len(recs) == 1
    r = recs[0]
    assert [af.finding_type for af in r.applicable_findings] == [FindingType.LOCATION]
    assert r.severity == Severity.WATCH  # single type, one day
    assert r.summary == "Location (1 day)"
    assert FindingType.LOCATION in r.evidence


def test_hours_only():
    recs = _build([_hr("E_ABM", "Bala")])
    assert len(recs) == 1
    r = recs[0]
    assert [af.finding_type for af in r.applicable_findings] == [FindingType.LOW_WORKING_HOURS]
    ev = r.evidence[FindingType.LOW_WORKING_HOURS]["occurrences"][0]
    assert ev["first_call"] == "10:15" and ev["hours_short"] == 1.2999999999999998


def test_both_one_record():
    recs = _build([_loc("E_BM", "Rahul"), _hr("E_BM", "Rahul")])
    assert len(recs) == 1  # ONE record, both types combined
    r = recs[0]
    types = {af.finding_type for af in r.applicable_findings}
    assert types == {FindingType.LOCATION, FindingType.LOW_WORKING_HOURS}
    assert r.severity == Severity.CRITICAL
    assert r.summary == "Location (1 day) + Low Working Hours"


def test_multiple_employees_same_rbm():
    recs = _build([_loc("E_BM", "Asha"), _hr("E_BM2", "Zoya")])
    assert len(recs) == 2
    assert {r.rbm_name for r in recs} == {"Rajan RBM"}
    assert {r.employee_code for r in recs} == {"E_BM", "E_BM2"}


def test_bm_hierarchy():
    r = _build([_loc("E_BM", "Asha")])[0]
    assert r.designation == "BM" and r.rbm_name == "Rajan RBM"


def test_abm_hierarchy():
    r = _build([_hr("E_ABM", "Bala")])[0]
    assert r.designation == "ABM" and r.rbm_name == "Rajan RBM"


def test_employee_with_no_findings_absent():
    recs = _build([_loc("E_BM", "Asha")])
    assert all(r.employee_code != "E_ABM" for r in recs)  # never emitted


def test_empty_dataset():
    assert build_attention_records([], resolve_hierarchy=_resolver) == []


def test_missing_hierarchy():
    r = _build([_loc("E_UNKNOWN", "Ghost")])[0]
    assert r.designation is None and r.rbm_name is None  # still produced
    assert r.severity == Severity.WATCH


def test_severity_ordering():
    findings = [
        _loc("E_BM", "Critical Carl"), _hr("E_BM", "Critical Carl"),   # Critical (both types)
        _loc("E_BM2", "Attention Ann", day=6), _loc("E_BM2", "Attention Ann", day=7),  # Attention (2 days)
        _hr("E_ABM", "Watch Wes"),                                     # Watch (one-off)
    ]
    recs = _build(findings)
    assert [r.severity for r in recs] == [Severity.CRITICAL, Severity.ATTENTION, Severity.WATCH]


def test_compute_severity_unit():
    loc = ApplicableFinding(FindingType.LOCATION, "Location", 1, "Location (1 day)", {})
    loc2 = ApplicableFinding(FindingType.LOCATION, "Location", 2, "Location (2 days)", {})
    hr = ApplicableFinding(FindingType.LOW_WORKING_HOURS, "Low Working Hours", 1, "Low Working Hours", {})
    assert compute_severity([loc, hr]) == Severity.CRITICAL
    assert compute_severity([loc2]) == Severity.ATTENTION
    assert compute_severity([loc]) == Severity.WATCH


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Master attention aggregation: all checks passed")
