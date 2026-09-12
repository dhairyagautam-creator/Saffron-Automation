"""Tests for the HQ Distribution master file (app.hq_distribution_service)
and its integration into Opus generation's applicability filter
(app.review_opus_service._filter_applicable_blocks).

Deliberately code-level only, per explicit instruction (2026-08-19): no
real HQ Distribution file exists yet (the user uploads their own and runs
analysis manually), so these tests build small synthetic fixtures and never
call app.review_opus_service.generate_opus_summary -- no fake "final
review file" is produced just to demonstrate the feature.
"""

import pandas as pd
import pytest

import app.hq_distribution_service as hq_distribution_service
from app.hq_distribution_service import (
    get_hq_distribution_state,
    get_valid_hqs_for_division,
    remove_hq_distribution_file,
    upload_hq_distribution_file,
)


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """Redirects storage to a per-test temp directory -- mirrors
    tests/test_review_validation.py's _isolated_review_storage fixture, so
    no test here can ever touch the user's real HQ Distribution upload."""
    monkeypatch.setattr(hq_distribution_service, "REVIEW_UPLOADS_DIR", tmp_path)
    yield


def _write_distribution_file(tmp_path, filename, rows, columns=("Division", "Region Name", "HQ")):
    path = tmp_path / filename
    pd.DataFrame(rows, columns=list(columns)).to_excel(path, index=False)
    return str(path)


SAMPLE_ROWS = [
    ("Xandra", "Andhra Pradesh", "Guntur"),
    ("Xandra", "Andhra Pradesh", "Nellore"),
    ("Xandra", "Gujarat - AHM", "Ahmedabad Pool"),
    ("Onyx", "Gujarat - AHM", "Ahmedabad Pool"),
    ("Onyx", "Punjab", "Amritsar"),
    ("Guardians", "Kerala", "Cochin Pool"),
]


# --- Upload + validation ------------------------------------------------------

def test_upload_and_state_roundtrip(tmp_path):
    source = _write_distribution_file(tmp_path, "hq_dist.xlsx", SAMPLE_ROWS)
    result = upload_hq_distribution_file(source)
    assert result["uploaded"] is True
    assert result["valid"] is True
    assert result["row_count"] == 6

    state = get_hq_distribution_state()
    assert state["uploaded"] is True
    assert state["valid"] is True
    assert state["filename"] == "hq_dist.xlsx"


def test_not_uploaded_returns_empty_state():
    state = get_hq_distribution_state()
    assert state["uploaded"] is False
    assert state["valid"] is False


def test_missing_required_column_is_invalid(tmp_path):
    source = _write_distribution_file(
        tmp_path, "bad.xlsx", [("Xandra", "Guntur")], columns=("Division", "HQ")
    )
    result = upload_hq_distribution_file(source)
    assert result["valid"] is False
    assert any("Region Name" in e for e in result["errors"])


def test_division_values_recognized(tmp_path):
    source = _write_distribution_file(tmp_path, "hq_dist.xlsx", SAMPLE_ROWS)
    result = upload_hq_distribution_file(source)
    assert result["division_counts"] == {"XANDRA": 3, "ONYX": 2, "GUARDIANS": 1}


def test_remove_file(tmp_path):
    source = _write_distribution_file(tmp_path, "hq_dist.xlsx", SAMPLE_ROWS)
    upload_hq_distribution_file(source)
    assert get_hq_distribution_state()["uploaded"] is True

    remove_hq_distribution_file()
    assert get_hq_distribution_state()["uploaded"] is False


def test_existing_review_uploads_are_untouched(tmp_path):
    """The HQ Distribution file must never write into any of the 12
    required-slot filenames -- it has its own, separate storage path."""
    source = _write_distribution_file(tmp_path, "hq_dist.xlsx", SAMPLE_ROWS)
    upload_hq_distribution_file(source)
    other_files = {p.name for p in tmp_path.iterdir()}
    assert "opus_annual_targets.xlsx" not in other_files
    assert "hq_distribution.xlsx" in other_files


# --- Region/HQ relationships per division -------------------------------------

def test_get_valid_hqs_for_division_returns_only_that_divisions_hqs(tmp_path):
    source = _write_distribution_file(tmp_path, "hq_dist.xlsx", SAMPLE_ROWS)
    upload_hq_distribution_file(source)

    xandra_hqs = get_valid_hqs_for_division("Xandra")
    onyx_hqs = get_valid_hqs_for_division("Onyx")
    guardians_hqs = get_valid_hqs_for_division("Guardians")

    assert xandra_hqs == {"GUNTUR", "NELLORE", "AHMEDABAD POOL"}
    assert onyx_hqs == {"AHMEDABAD POOL", "AMRITSAR"}
    assert guardians_hqs == {"COCHIN POOL"}


def test_hq_belonging_only_to_xandra_is_not_in_onyx_or_guardians(tmp_path):
    source = _write_distribution_file(tmp_path, "hq_dist.xlsx", SAMPLE_ROWS)
    upload_hq_distribution_file(source)

    assert "GUNTUR" in get_valid_hqs_for_division("Xandra")
    assert "GUNTUR" not in get_valid_hqs_for_division("Onyx")
    assert "GUNTUR" not in get_valid_hqs_for_division("Guardians")


def test_matching_is_by_hq_name_only_not_region_text():
    """2026-08-19 real-file discovery: the HQ Distribution file's own
    Region Name spelling ("GUJARAT-AHM") does not match the Opus reference
    structure's region display text ("Gujarat - AHM") -- matching on the
    (region, hq) tuple wrongly excluded ~55% of Xandra's real HQs the first
    time this was tried. HQ names matched cleanly across every source file
    checked, so that's the only signal used -- this pins that decision."""
    from app.review_opus_mapping import OpusHqBlock
    from app.review_opus_service import _filter_applicable_blocks

    import pandas as pd
    df = pd.DataFrame([("Xandra", "GUJARAT-AHM", "AHMEDABAD POOL")], columns=["Division", "Region Name", "HQ"])

    def fake_get_valid_hqs(division):
        return {"AHMEDABAD POOL"} if division == "Xandra" else set()

    import app.review_opus_service as opus_service
    original = opus_service.get_valid_hqs_for_division
    opus_service.get_valid_hqs_for_division = fake_get_valid_hqs
    try:
        # The reference block's OWN region text ("Gujarat - AHM") differs
        # from the distribution's ("GUJARAT-AHM" -> normalizes differently)
        # -- must still resolve as applicable via HQ name alone.
        block = OpusHqBlock(region="Gujarat - AHM", hq="Ahmedabad Pool", annual_targets_keys=None)
        assert [b.hq for b in _filter_applicable_blocks("Xandra", (block,))] == ["Ahmedabad Pool"]
    finally:
        opus_service.get_valid_hqs_for_division = original


def test_get_valid_hqs_returns_none_when_not_uploaded():
    assert get_valid_hqs_for_division("Xandra") is None


def test_get_valid_hqs_returns_none_when_invalid(tmp_path):
    source = _write_distribution_file(tmp_path, "bad.xlsx", [("Xandra", "Guntur")], columns=("Division", "HQ"))
    upload_hq_distribution_file(source)
    assert get_valid_hqs_for_division("Xandra") is None


# --- Applicability filter integration (app.review_opus_service) -------------

def test_not_applicable_hq_is_excluded_not_marked_unresolved(tmp_path, monkeypatch):
    """An HQ that belongs only to Xandra must simply be ABSENT from Onyx's
    block list -- not present-and-unresolved (that's DATA MISSING, a
    different, narrower situation: the HQ IS applicable but its source data
    is absent). This is the core distinction the whole feature exists for."""
    from app.review_opus_mapping import OpusHqBlock
    from app.review_opus_service import _filter_applicable_blocks

    source = _write_distribution_file(tmp_path, "hq_dist.xlsx", SAMPLE_ROWS)
    upload_hq_distribution_file(source)

    blocks = (
        OpusHqBlock(region="Andhra Pradesh", hq="Guntur", annual_targets_keys=(("ANDHRA PRADESH", "GUNTUR"),)),
        OpusHqBlock(region="Punjab", hq="Amritsar", annual_targets_keys=None),
    )

    xandra_applicable = _filter_applicable_blocks("Xandra", blocks)
    assert [b.hq for b in xandra_applicable] == ["Guntur"]  # Amritsar is Onyx-only -- excluded, not unresolved

    onyx_applicable = _filter_applicable_blocks("Onyx", blocks)
    assert [b.hq for b in onyx_applicable] == ["Amritsar"]  # Guntur is Xandra-only -- excluded from Onyx


def test_hq_belonging_only_to_guardians_not_treated_as_missing_in_onyx(tmp_path):
    from app.review_opus_mapping import OpusHqBlock
    from app.review_opus_service import _filter_applicable_blocks

    source = _write_distribution_file(tmp_path, "hq_dist.xlsx", SAMPLE_ROWS)
    upload_hq_distribution_file(source)

    blocks = (OpusHqBlock(region="Kerala", hq="Cochin Pool", annual_targets_keys=None),)
    assert _filter_applicable_blocks("Onyx", blocks) == []
    assert [b.hq for b in _filter_applicable_blocks("Guardians", blocks)] == ["Cochin Pool"]


def test_no_distribution_file_falls_back_to_treating_everything_as_applicable():
    """Preserves today's pre-existing behavior when the HQ Distribution
    file hasn't been uploaded yet -- generation must not break or start
    excluding blocks just because this optional file is absent."""
    from app.review_opus_mapping import OpusHqBlock
    from app.review_opus_service import _filter_applicable_blocks

    blocks = (
        OpusHqBlock(region="Andhra Pradesh", hq="Guntur", annual_targets_keys=None),
        OpusHqBlock(region="Punjab", hq="Amritsar", annual_targets_keys=None),
    )
    assert _filter_applicable_blocks("Xandra", blocks) == list(blocks)
    assert _filter_applicable_blocks("Onyx", blocks) == list(blocks)
