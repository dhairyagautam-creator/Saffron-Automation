"""Region Suppression fixes (Flaw A + Flaw B).

A: is_region_suppressed handles the 'Base - Zone' region naming convention.
B: the Location-Based tab shows 'Suppressed - Region Rule' from the canonical
   LIVE rule, before/independent of the persisted notification_status -- while
   Master/HR still exclude and email still withholds.

Pure: region maps and id-sets are constructed directly; no DB, no Tk, no email.
"""

from datetime import date
from types import SimpleNamespace

from app.region_suppression import is_region_suppressed
from app.suppression_service import region_suppressed_ids, suppressed_finding_ids
from ui.findings_page import _notification_status_display, status_text_for_display


def _f(fid, code, notif=None, rule="SAME_LOCATION"):
    return SimpleNamespace(
        finding_id=fid, employee_code=code, rule_name=rule,
        visit_date=date(2026, 8, 7), notification_status=notif, suppression_reason=None,
    )


def _rmap(pairs):
    return {(code, "07-08-2026"): region for code, region in pairs}


# --- Fix A: region matching ------------------------------------------------

def test_suppressed_regions_including_suffixes():
    for reg in [
        "Kerala", "Kerala - KOC", "kerala - KOC", " Kerala - KOC ",
        "Punjab", "Punjab - LDH", "Punjab - anything",
    ]:
        assert is_region_suppressed(reg), reg


def test_non_suppressed_regions_unaffected():
    for reg in [
        "Karnataka", "Uttar Pradesh", "Gujarat - AHM", "MP CG - CG",
        "Punjabi Bagh", "West Punjab", "Keralaish", None, "",
    ]:
        assert not is_region_suppressed(reg), reg


# --- Fix B: Location tab uses the LIVE rule, before email stamps ------------

def test_location_shows_region_suppressed_before_email_stamp():
    f = _f(1, "E1", notif=None)                      # email pipeline hasn't run
    rsupp = region_suppressed_ids([f], _rmap([("E1", "Kerala - KOC")]))
    assert 1 in rsupp
    assert status_text_for_display(f, rsupp) == "Suppressed - Region Rule"
    # Without the live rule (old behaviour) it would have read "Pending".
    assert _notification_status_display(f) == "Pending"


def test_display_stable_regardless_of_persisted_status():
    # Same finding before vs after the email pipeline stamps it -- and across a
    # 15s refresh -- reads identically, because the display is driven by the
    # live region, not the persisted status.
    rsupp = region_suppressed_ids([_f(1, "E1")], _rmap([("E1", "Punjab")]))
    before = _f(1, "E1", notif=None)
    after = _f(1, "E1", notif="Suppressed - Region Rule")
    assert status_text_for_display(before, rsupp) == "Suppressed - Region Rule"
    assert status_text_for_display(after, rsupp) == "Suppressed - Region Rule"


def test_non_region_finding_uses_persisted_status():
    rsupp = region_suppressed_ids([_f(1, "E1")], _rmap([("E1", "Karnataka")]))
    assert status_text_for_display(_f(1, "E1", notif="Sent"), rsupp) == "Email Sent"
    assert status_text_for_display(_f(1, "E1", notif=None), rsupp) == "Pending"


# --- Master/HR still exclude, email still withholds ------------------------

def test_master_hr_still_exclude_region_suppressed():
    # suppressed_finding_ids drives Master + HR exclusion (region OR persisted).
    f = _f(1, "E1", notif=None)                      # not stamped
    supp = suppressed_finding_ids([f], _rmap([("E1", "Kerala - KOC")]))
    assert 1 in supp                                 # excluded from Master/HR


def test_email_withholds_suffixed_region():
    # build_email_batch withholds via is_region_suppressed -- now suffix-aware.
    assert is_region_suppressed("Kerala - KOC")
    assert is_region_suppressed("Punjab - LDH")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Region suppression display: all checks passed")
