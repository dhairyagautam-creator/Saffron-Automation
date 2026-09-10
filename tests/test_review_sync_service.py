"""Pure-logic checks for app/review_sync_service.py -- version comparison,
slot labeling, and the slot-to-affected-report mapping. The network/DB-bound
flows (check_for_updates, apply_pending_updates) are covered by the manual
two-machine verification in the PR description, not here -- they need a real
Supabase project with 0023_sync_manifest.sql applied.
"""

from app.review_sync_service import _is_newer_version, _parse_version, _affected_report_divisions
from ui.review_uploads_page import _slot_label


def test_parse_version_basic():
    assert _parse_version("2.4.0") == (2, 4, 0)


def test_parse_version_strips_dev_suffix():
    assert _parse_version("1.3.1-dev.0.2") == (1, 3, 1)


def test_parse_version_malformed_is_lowest():
    assert _parse_version("not-a-version") == (0,)
    assert _parse_version("") == (0,)


def test_is_newer_version():
    assert _is_newer_version("2.5.0", "2.4.0")
    assert not _is_newer_version("2.4.0", "2.4.0")
    assert not _is_newer_version("2.3.0", "2.4.0")


def test_is_newer_version_dev_suffix_ignored_for_comparison():
    # A -dev build on the same numeric version as a remote row is NOT
    # newer -- the dev suffix carries no ordering information here.
    assert not _is_newer_version("2.4.0", "2.4.0-dev.0.1")


def test_slot_label_standalone():
    assert _slot_label("opus_annual_targets") == "Annual Targets"


def test_slot_label_grouped_names_source():
    assert _slot_label("opus_secondary_sales_onyx") == "Secondary Sales (Onyx)"
    assert _slot_label("coverage_avg_calls_guardians") == "Avg. and Calls (Guardians)"


def test_affected_report_divisions_shared_opus_input():
    # opus_annual_targets feeds Opus Summary for ALL THREE divisions, not
    # just one -- this is exactly the case that makes a replacing pull
    # here expensive to get wrong (see REQUIRED_SLOTS_FOR_OPUS).
    affected = _affected_report_divisions("opus_annual_targets")
    assert set(affected) == {
        ("Opus Summary", "Xandra"), ("Opus Summary", "Onyx"), ("Opus Summary", "Guardians"),
    }


def test_affected_report_divisions_visits_support_feeds_two_report_types():
    # One upload slot feeds BOTH Coverage Summary and RGD Visit and
    # Support for its division (see app.review_schemas._visits_support_slot).
    affected = _affected_report_divisions("coverage_visits_support_onyx")
    assert ("Coverage Summary", "Onyx") in affected
    assert ("RGD VISIT AND SUPPORT", "Onyx") in affected
    assert ("Opus Summary", "Onyx") not in affected


def test_affected_report_divisions_avg_calls_feeds_only_coverage():
    affected = _affected_report_divisions("coverage_avg_calls_xandra")
    assert affected == [("Coverage Summary", "Xandra")]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Review sync service pure-logic checks: all passed")
