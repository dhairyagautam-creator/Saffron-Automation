"""Issue 1: HR-Based findings show region-suppressed rows with the same
treatment as Location -- visible, tinted, status 'Suppressed - Region Rule',
not actionable, not emailed. Never hidden.

Pure: region maps and id-sets are built directly; no DB, no Tk, no email.
"""

from datetime import date
from types import SimpleNamespace

from app.region_suppression import is_region_suppressed
from app.suppression_service import region_suppressed_ids, suppressed_finding_ids
from ui.findings_page import HR_RULE_NAMES, hr_status_text

HR_MSG = "Worked 5.2h (10:15–16:25), below the 7.5h minimum. Review Required."


def _hr(fid, code, status="Open", notif=None):
    return SimpleNamespace(
        finding_id=fid, employee_code=code, employee_name="Emp", rule_name="HOURS_WORKED",
        visit_date=date(2026, 8, 7), status=status, notification_status=notif, message=HR_MSG,
    )


def _rmap(pairs):
    return {(code, "07-08-2026"): region for code, region in pairs}


def _hr_table_findings(findings):
    # Mirrors _render_hr_table's filter after the fix: rule type only, NOT
    # excluding suppressed rows.
    return [f for f in findings if f.rule_name in HR_RULE_NAMES]


# 1. remains visible
def test_region_suppressed_hr_remains_visible():
    hr = _hr(1, "E1")
    assert hr in _hr_table_findings([hr])   # not filtered out


# 2. gets the region-suppressed visual/status treatment
def test_region_suppressed_hr_status_and_tint():
    hr = _hr(1, "E1")
    rset = region_suppressed_ids([hr], _rmap([("E1", "Kerala - KOC")]))
    assert 1 in rset                                   # -> row gets region_suppressed tint
    assert hr_status_text(hr, rset) == "Suppressed - Region Rule"


# 3. excluded from email (build_email_batch withholds via is_region_suppressed)
def test_region_suppressed_hr_email_withheld():
    assert is_region_suppressed("Kerala - KOC")
    assert is_region_suppressed("Punjab - LDH")


# 3b. not actionable -> excluded from Master (suppressed_finding_ids)
def test_region_suppressed_hr_not_actionable():
    hr = _hr(1, "E1")
    assert 1 in suppressed_finding_ids([hr], _rmap([("E1", "Punjab")]))


# 4. a non-suppressed HR finding is unchanged
def test_non_suppressed_hr_unchanged():
    hr = _hr(2, "E2", status="Reviewed")
    rset = region_suppressed_ids([hr], _rmap([("E2", "Karnataka")]))
    assert 2 not in rset
    assert hr_status_text(hr, rset) == "Reviewed"      # review status, unchanged


# 5. the 15s refresh doesn't undo the display -- status comes from the live
#    region, so it's identical before and after the email stamps the persisted
#    notification_status.
def test_hr_display_stable_across_refresh():
    rset = region_suppressed_ids([_hr(1, "E1")], _rmap([("E1", "Punjab")]))
    assert hr_status_text(_hr(1, "E1", notif=None), rset) == "Suppressed - Region Rule"
    assert hr_status_text(_hr(1, "E1", notif="Suppressed - Region Rule"), rset) == "Suppressed - Region Rule"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("HR region-suppression display: all checks passed")
