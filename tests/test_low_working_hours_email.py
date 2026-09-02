"""Aggregation check for Low Working Hours in the Path Validator email.

Exercises _build_consolidated_email directly (no DB, no SMTP, no network):
an employee with a location finding only, Low Working Hours only, both, and
several employees in one RBM group -- verifying each employee appears ONCE
with only the applicable sections, and that both-condition employees get a
Combined interpretation.
"""

from datetime import date
from types import SimpleNamespace

from app.notification_service import _build_consolidated_email


def _finding(fid, code, name, rule, message="", *, matched=None, valid=None,
             radius=None, threshold=None, concentration=None, division="Onyx"):
    return SimpleNamespace(
        finding_id=fid,
        employee_code=code,
        employee_name=name,
        rule_name=rule,
        message=message,
        visit_date=date(2026, 8, 7),
        division=division,
        matched_visit_count=matched,
        valid_visit_count=valid,
        radius_meters=radius,
        threshold_percent=threshold,
        concentration_percent=concentration,
    )


HR_MSG = "Worked 6.2h (10:15–16:25), below the 7.5h minimum. Review Required."


def _loc(fid, code, name):
    return _finding(fid, code, name, "SAME_LOCATION",
                    matched=3, valid=7, radius=50, threshold=30, concentration=45)


def _hr(fid, code, name):
    return _finding(fid, code, name, "HOURS_WORKED", message=HR_MSG)


def _build(findings):
    contexts = {f.finding_id: {"hq": "HQ1", "region": None, "coordinates": []} for f in findings}
    designations = {f.finding_id: "BM" for f in findings}
    addresses = {
        f.employee_name: [{"doctor": "Dr. Test", "address": "12 MG Road, Pune", "flagged": False}]
        for f in findings
    }
    return _build_consolidated_email("RBM One", findings, contexts, designations, addresses)


def test_location_only():
    html, text, _, distinct = _build([_loc(1, "E1", "Asha")])
    assert distinct == 1
    assert "Working Hours" not in html
    assert "Combined interpretation" not in html
    assert "Location" in html and "Visit Records" in html


def test_hours_only():
    html, text, _, distinct = _build([_hr(2, "E2", "Bala")])
    assert distinct == 1
    assert "Working Hours" in html
    assert "First call: 10:15" in html and "Short by: 1h 20m" in html
    assert "Combined interpretation" not in html
    assert "Visit Records" not in html  # HR-only: no location block


def test_both_one_employee():
    html, text, _, distinct = _build([_loc(3, "E3", "Rahul Shah"), _hr(4, "E3", "Rahul Shah")])
    assert distinct == 1  # ONE entry, not two
    assert "Status: Location + Low Working Hours" in html
    assert "Working Hours" in html and "Location" in html
    assert "Combined interpretation" in html
    assert "Combined interpretation:" in text
    # combined line uses real values from both detectors
    assert "45% of 7 valid GPS-tagged calls" in html
    assert "1h 20m short of the 7h 30m minimum" in html


def test_multiple_employees_one_email():
    findings = [
        _loc(5, "E1", "Asha"),
        _hr(6, "E2", "Bala"),
        _loc(7, "E3", "Rahul"), _hr(8, "E3", "Rahul"),
    ]
    html, text, table_rows, distinct = _build(findings)
    assert distinct == 3  # three employees, one email
    assert len(table_rows) == 4  # one summary row per finding
    for code in ("E1", "E2", "E3"):
        assert f"({code})" in html


if __name__ == "__main__":
    test_location_only()
    test_hours_only()
    test_both_one_employee()
    test_multiple_employees_one_email()
    print("Low Working Hours email aggregation: all checks passed")
