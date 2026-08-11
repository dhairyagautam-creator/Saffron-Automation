"""Issue 2: Master aggregates ACTIONABLE findings per employee_code, using the
canonical suppression rule -- including the real 'two employees share a name'
case (Vishal Kumar: SF2080 Location, SF0565 Hours Worked).

Mirrors get_current_attention_records: pre-filter suppressed findings
(filter_actionable), then build_attention_records. Pure -- region maps and
hierarchy resolver injected; no DB.
"""

from datetime import date
from types import SimpleNamespace

from app.master_attention_service import FindingType, build_attention_records
from app.suppression_service import suppressed_finding_ids

HR_MSG = "Worked 5.2h (10:15–16:25), below the 7.5h minimum. Review Required."

_HIER = {
    "SF2080": ("ABM", "Ramsanehi Yadav"),
    "SF0565": ("ABM", "Sumeet Kumar Jha"),
    "E1": ("BM", "RBM One"),
}


def _resolve(code, name):
    return _HIER.get(code, (None, None))


def _loc(fid, code, name, day=7):
    return SimpleNamespace(
        finding_id=fid, employee_code=code, employee_name=name, rule_name="SAME_LOCATION",
        visit_date=date(2026, 8, day), message="loc", notification_status=None,
        concentration_percent=45.0, matched_visit_count=3, valid_visit_count=7,
        radius_meters=50, threshold_percent=30.0,
    )


def _hr(fid, code, name, day=7):
    return SimpleNamespace(
        finding_id=fid, employee_code=code, employee_name=name, rule_name="HOURS_WORKED",
        visit_date=date(2026, 8, day), message=HR_MSG, notification_status=None,
        concentration_percent=None, matched_visit_count=None, valid_visit_count=None,
        radius_meters=None, threshold_percent=None,
    )


def _rmap(triples):
    return {(code, day): region for code, day, region in triples}


def _master(findings, region_map=None):
    region_map = region_map or {}
    actionable = [f for f in findings if f.finding_id not in suppressed_finding_ids(findings, region_map)]
    return build_attention_records(actionable, resolve_hierarchy=_resolve)


def _types(record):
    return sorted(a.finding_type for a in record.applicable_findings)


# 1
def test_only_location():
    recs = _master([_loc(1, "E1", "Asha")])
    assert len(recs) == 1 and _types(recs[0]) == [FindingType.LOCATION]


# 2
def test_only_hr():
    recs = _master([_hr(1, "E1", "Asha")])
    assert len(recs) == 1 and _types(recs[0]) == [FindingType.LOW_WORKING_HOURS]


# 3
def test_both_actionable_same_code():
    recs = _master([_loc(1, "E1", "Asha"), _hr(2, "E1", "Asha")])
    assert len(recs) == 1
    assert _types(recs[0]) == sorted([FindingType.LOCATION, FindingType.LOW_WORKING_HOURS])


# 4  (location actionable + HR region-suppressed -> only Location)
def test_location_actionable_hr_suppressed():
    findings = [_loc(1, "E1", "Asha", day=7), _hr(2, "E1", "Asha", day=8)]
    rmap = _rmap([("E1", "07-08-2026", "Karnataka"), ("E1", "08-08-2026", "Kerala")])
    recs = _master(findings, rmap)
    assert len(recs) == 1 and _types(recs[0]) == [FindingType.LOCATION]


# 5  (location region-suppressed + HR actionable -> only HR)
def test_location_suppressed_hr_actionable():
    findings = [_loc(1, "E1", "Asha", day=7), _hr(2, "E1", "Asha", day=8)]
    rmap = _rmap([("E1", "07-08-2026", "Punjab - LDH"), ("E1", "08-08-2026", "Karnataka")])
    recs = _master(findings, rmap)
    assert len(recs) == 1 and _types(recs[0]) == [FindingType.LOW_WORKING_HOURS]


# 6  Vishal Kumar's exact case: two codes sharing a name -> two records
def test_vishal_kumar_two_codes_two_records():
    findings = [_loc(6242, "SF2080", "Vishal Kumar"), _hr(6535, "SF0565", "Vishal Kumar")]
    rmap = _rmap([("SF2080", "07-08-2026", "MP CG - MP"), ("SF0565", "07-08-2026", "Bihar")])
    recs = _master(findings, rmap)
    assert len(recs) == 2
    by_code = {r.employee_code: r for r in recs}
    assert _types(by_code["SF2080"]) == [FindingType.LOCATION]
    assert _types(by_code["SF0565"]) == [FindingType.LOW_WORKING_HOURS]
    assert by_code["SF2080"].rbm_name == "Ramsanehi Yadav"
    assert by_code["SF0565"].rbm_name == "Sumeet Kumar Jha"


# 7  region-suppressed HR is not actionable in Master (excluded), even though
#    it stays visible in the HR-Based table (see test_hr_region_suppression_display).
def test_region_suppressed_hr_excluded_from_master():
    findings = [_hr(1, "E1", "Kerala Ken")]
    recs = _master(findings, _rmap([("E1", "07-08-2026", "Kerala - KOC")]))
    assert recs == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Master actionable aggregation: all checks passed")
