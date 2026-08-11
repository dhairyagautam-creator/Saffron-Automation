"""UI-logic checks for the Master page (ui/master_page).

Tests the page's PURE presentation logic -- KPI counts, filtering, issue
badges -- over real Phase 1 EmployeeAttentionRecords. No Tk root, no widgets,
no emails: the widget code delegates to these module-level functions.
"""

from datetime import date
from types import SimpleNamespace

from app.master_attention_service import FindingType, Severity, build_attention_records
from ui.master_page import COLUMNS, _issue_badges, filter_records, kpi_counts

HR_MSG = "Worked 6.2h (10:15–16:25), below the 7.5h minimum. Review Required."

_HIER = {
    "E_BM": ("BM", "Rajan RBM"),
    "E_BM2": ("BM", "Rajan RBM"),
    "E_ABM": ("ABM", "Meera RBM"),
}


def _loc(code, name, day=7):
    return SimpleNamespace(
        finding_id=id((code, day, "l")), employee_code=code, employee_name=name,
        rule_name="SAME_LOCATION", visit_date=date(2026, 8, day), message="loc",
        concentration_percent=45.0, matched_visit_count=3, valid_visit_count=7,
        radius_meters=50, threshold_percent=30.0,
    )


def _hr(code, name, day=7):
    return SimpleNamespace(
        finding_id=id((code, day, "h")), employee_code=code, employee_name=name,
        rule_name="HOURS_WORKED", visit_date=date(2026, 8, day), message=HR_MSG,
        concentration_percent=None, matched_visit_count=None, valid_visit_count=None,
        radius_meters=None, threshold_percent=None,
    )


def _records(findings):
    return build_attention_records(findings, resolve_hierarchy=lambda c, n: _HIER.get(c, (None, None)))


def _population():
    return _records([
        _loc("E_BM", "Critical Carl"), _hr("E_BM", "Critical Carl"),        # both -> Critical, BM
        _loc("E_BM2", "Attention Ann", day=6), _loc("E_BM2", "Attention Ann", day=7),  # 2 loc -> Attention, BM
        _hr("E_ABM", "Watch Wes"),                                          # hr only -> Watch, ABM
    ])


def test_severity_column_removed():
    # Phase 3: Severity is no longer a visible column; RBM column stays.
    assert "severity" not in COLUMNS
    assert COLUMNS == ("employee", "designation", "rbm", "issues", "summary")


def test_empty_dataset_kpis_and_filter():
    assert kpi_counts([]) == {"flagged": 0, "location": 0, "low_hours": 0, "multiple": 0}
    assert filter_records([], "All", "All", "All", "") == []


def test_kpi_counts():
    assert kpi_counts(_population()) == {"flagged": 3, "location": 2, "low_hours": 2, "multiple": 1}


def test_issue_badges():
    recs = {r.employee_code: r for r in _population()}
    assert _issue_badges(recs["E_BM"]) == "LOCATION + LOW HOURS"
    assert _issue_badges(recs["E_BM2"]) == "LOCATION"
    assert _issue_badges(recs["E_ABM"]) == "LOW HOURS"


def test_severity_ordering():
    order = [r.severity for r in _population()]
    assert order == [Severity.CRITICAL, Severity.ATTENTION, Severity.WATCH]


def test_filter_by_severity():
    recs = _population()
    assert [r.employee_code for r in filter_records(recs, "Critical", "All", "All", "")] == ["E_BM"]
    assert [r.employee_code for r in filter_records(recs, "Watch", "All", "All", "")] == ["E_ABM"]


def test_filter_by_issue_type():
    recs = _population()
    assert {r.employee_code for r in filter_records(recs, "All", "All", "Low Working Hours", "")} == {"E_BM", "E_ABM"}
    assert [r.employee_code for r in filter_records(recs, "All", "All", "Multiple", "")] == ["E_BM"]


def test_filter_by_designation_bm_and_abm():
    recs = _population()
    assert {r.employee_code for r in filter_records(recs, "All", "BM", "All", "")} == {"E_BM", "E_BM2"}
    assert [r.employee_code for r in filter_records(recs, "All", "ABM", "All", "")] == ["E_ABM"]


def test_search():
    recs = _population()
    assert [r.employee_code for r in filter_records(recs, "All", "All", "All", "wes")] == ["E_ABM"]


def test_missing_hierarchy_record_present():
    recs = _records([_loc("E_UNKNOWN", "Ghost")])
    assert recs[0].designation is None and recs[0].rbm_name is None
    assert filter_records(recs, "All", "All", "All", "") == recs


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Master page UI-logic: all checks passed")
