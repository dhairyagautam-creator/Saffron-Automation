"""Regression: a 15s refresh must not reset the Findings view when nothing
findings-related changed, and MUST refresh it when a finding's suppression
status changes.

The bug this guards: the poller re-rendered the Findings page every 15s
(driven by config/workbook sync churn), rebuilding the tables and clearing the
user's selection + the open "Suppressed - Region Rule" detail -- even though
the finding value never changed. findings_signature() is the change-detector
that lets the page skip a redundant rebuild.
"""

from datetime import date
from types import SimpleNamespace

from ui.findings_page import findings_signature


def _f(fid, notif=None, status="Open", reason=None, name="Asha", message="m"):
    return SimpleNamespace(
        finding_id=fid, notification_status=notif, suppression_reason=reason,
        status=status, employee_name=name, message=message, rule_name="SAME_LOCATION",
        visit_date=date(2026, 8, 7),
    )


def test_unchanged_findings_have_equal_signature():
    a = [_f(1, "Suppressed - Region Rule"), _f(2, None), _f(3, "Sent")]
    b = [_f(1, "Suppressed - Region Rule"), _f(2, None), _f(3, "Sent")]
    assert findings_signature(a) == findings_signature(b)   # no-op refresh -> skip rebuild


def test_suppression_applied_changes_signature():
    before = [_f(1, None)]                                   # not yet suppressed
    after = [_f(1, "Suppressed - Region Rule", reason="Region rule")]
    assert findings_signature(before) != findings_signature(after)  # -> rebuild shows it


def test_suppression_reverted_changes_signature():
    # If sync ever DID revert it, the page would still notice and rebuild.
    supp = [_f(1, "Suppressed - Region Rule")]
    reverted = [_f(1, None)]
    assert findings_signature(supp) != findings_signature(reverted)


def test_review_status_change_changes_signature():
    assert findings_signature([_f(1, status="Open")]) != findings_signature([_f(1, status="Reviewed")])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Findings refresh signature: all checks passed")
