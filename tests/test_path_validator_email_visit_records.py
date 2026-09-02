"""Tests for the Path Validator email's address-level visit presentation
(2026-08-25): every underlying visit record shown (never deduplicated),
each with its own doctor name, and highlighted exactly when it falls
within the ALREADY-COMPUTED 50m cluster for its finding.

Two layers, no DB/SMTP/network:
  - app.notification_service._visit_is_flagged -- the pure highlight
    derivation (same haversine_km formula the rule itself already used,
    reading the finding's own already-stored cluster_lat/cluster_lon/
    radius_meters; never recomputes the flag decision).
  - app.email_template._visit_records_table / render_manager_email_html /
    render_manager_email_text -- the pure rendering of already-built visit
    rows, mirroring tests/test_low_working_hours_email.py's own approach
    of exercising _build_consolidated_email with fabricated data.
"""

from datetime import date
from types import SimpleNamespace

from app.email_template import _visit_records_table, render_manager_email_text
from app.notification_service import _build_consolidated_email, _visit_is_flagged

# Guntur, India -- real-ish coordinates so haversine_km distances are meaningful.
ANCHOR_LAT, ANCHOR_LON = 16.3067, 80.4365


def _finding(radius_meters=50, cluster_lat=ANCHOR_LAT, cluster_lon=ANCHOR_LON):
    return SimpleNamespace(radius_meters=radius_meters, cluster_lat=cluster_lat, cluster_lon=cluster_lon)


# --- _visit_is_flagged -------------------------------------------------------

def test_visit_at_the_anchor_itself_is_flagged():
    assert _visit_is_flagged(ANCHOR_LAT, ANCHOR_LON, _finding()) is True


def test_visit_well_outside_radius_is_not_flagged():
    # ~1.1km away -- far outside a 50m radius.
    assert _visit_is_flagged(ANCHOR_LAT + 0.01, ANCHOR_LON, _finding(radius_meters=50)) is False


def test_visit_just_inside_radius_is_flagged():
    # ~0.00035 deg latitude ~= 39m -- inside 50m.
    assert _visit_is_flagged(ANCHOR_LAT + 0.00035, ANCHOR_LON, _finding(radius_meters=50)) is True


def test_missing_cluster_data_never_flags_anything():
    """A finding predating cluster_lat/cluster_lon/radius_meters (nullable
    columns) must never crash, and must never be highlighted -- fail-safe,
    not fail-flagged."""
    assert _visit_is_flagged(ANCHOR_LAT, ANCHOR_LON, _finding(radius_meters=None)) is False
    assert _visit_is_flagged(ANCHOR_LAT, ANCHOR_LON, _finding(cluster_lat=None)) is False
    assert _visit_is_flagged(ANCHOR_LAT, ANCHOR_LON, _finding(cluster_lon=None)) is False


# --- _visit_records_table (HTML) --------------------------------------------

def test_identical_addresses_render_as_separate_rows_not_deduplicated():
    visits = [
        {"doctor": "Dr. A. Sharma", "address": "123 Example Road, Ahmedabad", "flagged": False},
        {"doctor": "Dr. B. Patel", "address": "123 Example Road, Ahmedabad", "flagged": True},
    ]
    html = _visit_records_table(visits)
    assert html.count("123 Example Road, Ahmedabad") == 2  # two rows, not merged into one
    assert html.count("<tr>") == 3  # header + 2 data rows


def test_flagged_row_is_highlighted_and_labeled():
    visits = [{"doctor": "Dr. Flagged", "address": "Address A", "flagged": True}]
    html = _visit_records_table(visits)
    assert "50m Flag" in html
    assert "#FFC7CE" in html  # the flag highlight color


def test_normal_row_is_not_highlighted():
    visits = [{"doctor": "Dr. Normal", "address": "Address B", "flagged": False}]
    html = _visit_records_table(visits)
    assert "50m Flag" not in html
    assert "#FFC7CE" not in html
    assert "Normal" in html


def test_only_flagged_rows_are_highlighted_among_mixed_rows():
    """A non-flagged row must never be highlighted just because another row
    for the same employee is flagged."""
    visits = [
        {"doctor": "Dr. A. Sharma", "address": "Address A", "flagged": False},
        {"doctor": "Dr. B. Patel", "address": "Address A", "flagged": True},
        {"doctor": "Dr. C. Shah", "address": "Address B", "flagged": False},
    ]
    html = _visit_records_table(visits)
    assert html.count("50m Flag") == 1
    assert html.count("Normal") == 2


def test_address_appears_before_doctor_in_the_header():
    html = _visit_records_table([{"doctor": "Dr. X", "address": "Addr X", "flagged": False}])
    assert html.index("Visited Address") < html.index("Doctor")


def test_empty_visit_list_does_not_crash():
    html = _visit_records_table([])
    assert "No resolved visit records available." in html


def test_missing_doctor_name_handled_gracefully():
    """notification_service substitutes a fallback label before this ever
    reaches rendering -- confirm the template itself doesn't choke on an
    empty string either way."""
    html = _visit_records_table([{"doctor": "", "address": "Addr Y", "flagged": False}])
    assert "Addr Y" in html  # renders without raising


# --- End-to-end via _build_consolidated_email (mirrors the existing
# test_low_working_hours_email.py convention: fabricated findings/contexts/
# addresses, no DB) ----------------------------------------------------------

def _location_finding(fid, code, name):
    return SimpleNamespace(
        finding_id=fid, employee_code=code, employee_name=name, rule_name="SAME_LOCATION",
        message="", visit_date=date(2026, 8, 7), division="Onyx",
        matched_visit_count=2, valid_visit_count=3, radius_meters=50, threshold_percent=30,
        concentration_percent=67,
    )


def test_multiple_doctors_same_address_all_appear_in_final_email_text():
    finding = _location_finding(1, "E1", "Asha")
    contexts = {1: {"hq": "HQ1", "region": None, "coordinates": []}}
    designations = {1: "BM"}
    addresses = {
        "Asha": [
            {"doctor": "Dr. A. Sharma", "address": "123 Example Road, Ahmedabad", "flagged": False},
            {"doctor": "Dr. B. Patel", "address": "123 Example Road, Ahmedabad", "flagged": True},
            {"doctor": "Dr. C. Shah", "address": "456 Example Street, Ahmedabad", "flagged": False},
        ]
    }
    html, text, _, _ = _build_consolidated_email("RBM One", [finding], contexts, designations, addresses)

    for doctor in ("Dr. A. Sharma", "Dr. B. Patel", "Dr. C. Shah"):
        assert doctor in html
        assert doctor in text
    assert html.count("123 Example Road, Ahmedabad") == 2
    assert "[50m FLAG]" in text  # plain-text fallback marks the flagged row too
    assert text.count("[50m FLAG]") == 1
