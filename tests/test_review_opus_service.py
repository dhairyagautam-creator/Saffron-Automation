"""Tests for Opus Summary generation (app.review_opus_service).

Two kinds of coverage:
  - Synthetic tests (fast, no file dependency) for the pure-arithmetic
    formula layer (recompute_formula_rows) and the Mumbai-style
    unresolved-mapping safety guard.
  - Real-file validation tests (skip cleanly if review_uploads/ isn't
    present, same pattern as tests/test_review_validation.py) that
    regenerate Xandra's actual summary and check its Guntur block against
    the exact full-precision reference values extracted from the manual
    reference workbook (2026-08-19) -- these are what caught the
    average-vs-SUM and CN sign-flip mistakes in the first place, so they
    stay as the regression guard against re-introducing either.
"""

import os

import pytest

from app.review_opus_mapping import XANDRA_OPUS_HQ_BLOCKS, OpusHqBlock
from app.review_opus_service import (
    ComputedHqBlock,
    DIVISIONS,
    OPUS_REPORT_MONTHS,
    ROW_LABELS,
    _compute_hq_block,
    _load_annual_targets,
    _load_primary_sales_lookups,
    _load_secondary_sales,
    generate_opus_summary,
    opus_prerequisites_ready,
    recompute_formula_rows,
)

_REAL_UPLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "review_uploads")
_REQUIRES_REAL_FILES = pytest.mark.skipif(
    not os.path.isdir(_REAL_UPLOADS_DIR), reason="no real review_uploads/ files present on this machine"
)

# Exact full-precision values extracted directly from the manual reference
# workbook's Guntur block (Andhra Pradesh / Guntur, No of BM=1), 2026-08-19.
GUNTUR_REFERENCE = {
    "TARGET": {"APR": 2.25, "MAY": 2.25, "JUN": 2.25, "JUL": 2.25, "CUMMULATIVE": 9.0},
    "PRIMARY": {"APR": 2.2074823000000006, "MAY": 2.1502569, "JUN": 2.3704559, "JUL": 1.6253935000000002, "CUMMULATIVE": 8.3535886},
    "LY PRIMARY": {"APR": 2.0823675999999987, "MAY": 2.1932260999999995, "JUN": 2.3207777000000007, "JUL": 2.2125106999999997, "CUMMULATIVE": 8.808882099999998},
    "% ACH (Normal)": {"APR": 0.9811032444444447, "MAY": 0.9556697333333334, "JUN": 1.0535359555555557, "JUL": 0.7223971111111112, "CUMMULATIVE": 0.9281765111111111},
    "% GR": {"APR": 0.06008290755196248, "MAY": -0.019591778522059122, "JUN": 0.021405841671091257, "JUL": -0.2653624228800338, "CUMMULATIVE": -0.05168572979311394},
    "YPM (PRIMARY)": {"APR": 2.2074823000000006, "MAY": 2.1502569, "JUN": 2.3704559, "JUL": 1.6253935000000002, "CUMMULATIVE": 2.08839715},
    "SALABLE CN": {"APR": 0.5340741, "MAY": 0.086787, "JUN": 0, "JUL": 0, "CUMMULATIVE": 0.6208610999999999},
    "EXPIRY CN": {"APR": 0, "MAY": 0.0028929, "JUN": 0, "JUL": 0, "CUMMULATIVE": 0.0028929},
    "TOTAL CN": {"APR": 0.5340741, "MAY": 0.0896799, "JUN": 0, "JUL": 0, "CUMMULATIVE": 0.623754},
    "DUAL INCREMENT MIN ELIGIBLE TGT": {"APR": 2.6059787666666665, "MAY": 2.6059787666666665, "JUN": 2.6059787666666665, "JUL": 2.6059787666666665, "CUMMULATIVE": 10.423915066666666},
    "DUAL INCREMENT NET ACH": {"APR": 1.6734082000000006, "MAY": 2.060577, "JUN": 2.3704559, "JUL": 1.6253935000000002, "CUMMULATIVE": 7.729834600000001},
    "DUAL INCREMENT NET ACH %": {"APR": 0.642141916659004, "MAY": 0.790711354350636, "JUN": 0.9096221083305579, "JUL": 0.6237170927064218, "CUMMULATIVE": 0.741548118011655},
    "DUAL INCREMENT VALUE GAIN / DEFICIT": {"APR": -0.9325705666666659, "MAY": -0.5454017666666666, "JUN": -0.23552286666666644, "JUL": -0.9805852666666663, "CUMMULATIVE": -2.6940804666666653},
}


# --- Synthetic: pure formula-layer correctness (no files needed) ------------

def _synthetic_block(no_of_bm=2):
    return ComputedHqBlock(
        region="Test Region", hq="Test HQ", unresolved=False, no_of_bm=no_of_bm,
        source_rows={
            "TARGET": {"APR": 10.0, "MAY": 20.0},
            "PRIMARY": {"APR": 11.0, "MAY": 18.0},
            "LY PRIMARY": {"APR": 10.0, "MAY": 20.0},
            "SECONDARY": {"APR": 5.0, "MAY": 5.0},
            "SALABLE CN": {"APR": 1.0, "MAY": 0.0},
            "EXPIRY CN": {"APR": 0.5, "MAY": 0.0},
            "DUAL INCREMENT MIN ELIGIBLE TGT": {"APR": 4.0, "MAY": 4.0},
        },
    )


def test_pct_ach_and_gr_formulas():
    rows = recompute_formula_rows(_synthetic_block(), ("APR", "MAY"))
    assert rows["% ACH (Normal)"]["APR"] == pytest.approx(11.0 / 10.0)
    assert rows["% GR"]["APR"] == pytest.approx(11.0 / 10.0 - 1)
    assert rows["% GR"]["MAY"] == pytest.approx(18.0 / 20.0 - 1)


def test_ypm_uses_one_formula_for_monthly_and_cumulative():
    # Spec: "Use ONE formula for both monthly and cumulative -- do NOT
    # create separate formulas." Monthly divides by (BM x 1); cumulative by
    # (BM x number of months shown).
    rows = recompute_formula_rows(_synthetic_block(no_of_bm=2), ("APR", "MAY"))
    assert rows["YPM (PRIMARY)"]["APR"] == pytest.approx(11.0 / (2 * 1))
    assert rows["YPM (PRIMARY)"]["MAY"] == pytest.approx(18.0 / (2 * 1))
    cumulative_primary = 11.0 + 18.0
    assert rows["YPM (PRIMARY)"]["CUMMULATIVE"] == pytest.approx(cumulative_primary / (2 * 2))


def test_total_cn_and_dual_increment_chain():
    rows = recompute_formula_rows(_synthetic_block(), ("APR", "MAY"))
    assert rows["TOTAL CN"]["APR"] == pytest.approx(1.0 + 0.5)
    net_ach_apr = 11.0 - 1.5  # PRIMARY - TOTAL CN
    assert rows["DUAL INCREMENT NET ACH"]["APR"] == pytest.approx(net_ach_apr)
    assert rows["DUAL INCREMENT NET ACH %"]["APR"] == pytest.approx(net_ach_apr / 4.0)


def test_value_gain_deficit_is_net_ach_minus_target_not_the_reverse():
    """2026-08-19 correction: the master spec originally said
    MIN ELIGIBLE TGT - NET ACH, but the reference workbook's real numbers
    proved it's the other way around (positive = beat target = gain)."""
    rows = recompute_formula_rows(_synthetic_block(), ("APR", "MAY"))
    net_ach_apr = 11.0 - 1.5
    min_eligible_apr = 4.0
    assert rows["DUAL INCREMENT VALUE GAIN / DEFICIT"]["APR"] == pytest.approx(net_ach_apr - min_eligible_apr)
    assert rows["DUAL INCREMENT VALUE GAIN / DEFICIT"]["APR"] != pytest.approx(min_eligible_apr - net_ach_apr)


def test_zero_denominator_yields_none_not_a_crash():
    block = ComputedHqBlock(
        region="R", hq="H", unresolved=False, no_of_bm=1,
        source_rows={
            "TARGET": {"APR": 0.0}, "PRIMARY": {"APR": 5.0}, "LY PRIMARY": {"APR": 0.0},
            "SECONDARY": {"APR": 0.0}, "SALABLE CN": {"APR": 0.0}, "EXPIRY CN": {"APR": 0.0},
            "DUAL INCREMENT MIN ELIGIBLE TGT": {"APR": 0.0},
        },
    )
    rows = recompute_formula_rows(block, ("APR",))
    assert rows["% ACH (Normal)"]["APR"] is None
    assert rows["% GR"]["APR"] is None
    assert rows["DUAL INCREMENT NET ACH %"]["APR"] is None


# --- Unresolved-mapping safety guard (Mumbai Pool pattern) -------------------

def test_unresolved_block_computes_nothing():
    unresolved_block = OpusHqBlock(region="Mumbai - 1", hq="Mumbai Pool", annual_targets_keys=None)
    result = _compute_hq_block(unresolved_block, at_lookup={}, primary_lookups={"primary": {}, "cn_gst": {}, "cn_exp": {}, "apr_sep_sum": {}},
                                ly_lookups={"primary": {}, "cn_gst": {}, "cn_exp": {}, "apr_sep_sum": {}},
                                secondary_lookup={}, months=OPUS_REPORT_MONTHS)
    assert result.unresolved is True
    assert result.no_of_bm is None
    assert result.source_rows == {}
    assert recompute_formula_rows(result, OPUS_REPORT_MONTHS) == {}


def test_mumbai_pool_unresolved_because_it_repeats_within_the_structure_itself():
    """2026-08-19 architecture correction: Mumbai Pool is unresolved not
    because of a BM-sum coincidence specific to Xandra's reference numbers,
    but because "Mumbai Pool" appears 4 times in the 165-block reference
    STRUCTURE itself (across Mumbai - 1 / Mumbai - 2) -- there is no signal
    for which Annual Targets row belongs to which occurrence, for ANY
    division, even one with no reference workbook of its own."""
    unresolved = [b for b in XANDRA_OPUS_HQ_BLOCKS if b.annual_targets_keys is None]
    assert len(unresolved) == 4
    assert all(b.hq == "Mumbai Pool" for b in unresolved)
    assert {b.region for b in unresolved} == {"Mumbai - 1", "Mumbai - 2"}


def test_row_labels_has_no_no_column_removed_and_14_rows_in_spec_order():
    # 2026-08-19 correction: the master spec originally said "no NO column"
    # but the reference workbook has one 1-14 (+15 spacer) -- that's
    # generated at write time from ROW_LABELS' order (see
    # app.review_opus_service._write_workbook), not stored per-row here.
    assert len(ROW_LABELS) == 14
    assert ROW_LABELS[0] == "TARGET"
    assert ROW_LABELS[-1] == "DUAL INCREMENT VALUE GAIN / DEFICIT"


# --- All three divisions share ONE structure, resolve independently --------

def test_all_three_divisions_share_the_same_165_block_structure_and_order():
    from app.review_opus_mapping import GUARDIANS_OPUS_HQ_BLOCKS, ONYX_OPUS_HQ_BLOCKS

    xandra_pairs = [(b.region, b.hq) for b in XANDRA_OPUS_HQ_BLOCKS]
    onyx_pairs = [(b.region, b.hq) for b in ONYX_OPUS_HQ_BLOCKS]
    guardians_pairs = [(b.region, b.hq) for b in GUARDIANS_OPUS_HQ_BLOCKS]
    assert xandra_pairs == onyx_pairs == guardians_pairs
    assert len(xandra_pairs) == 165


def test_onyx_and_guardians_resolve_fewer_blocks_than_xandra():
    # Real, expected: Onyx and Guardians are smaller divisions that don't
    # operate in every HQ Xandra does (verified 2026-08-19 -- Onyx's own
    # Annual Targets sheet has no Andhra Pradesh/Bihar/Karnataka/Telangana
    # rows at all). Not a bug -- these blocks must stay unresolved, not be
    # guessed from Xandra's data.
    from app.review_opus_mapping import GUARDIANS_OPUS_HQ_BLOCKS, ONYX_OPUS_HQ_BLOCKS

    xandra_resolved = sum(1 for b in XANDRA_OPUS_HQ_BLOCKS if b.annual_targets_keys is not None)
    onyx_resolved = sum(1 for b in ONYX_OPUS_HQ_BLOCKS if b.annual_targets_keys is not None)
    guardians_resolved = sum(1 for b in GUARDIANS_OPUS_HQ_BLOCKS if b.annual_targets_keys is not None)
    assert xandra_resolved == 161
    assert 0 < onyx_resolved < xandra_resolved
    assert 0 < guardians_resolved < onyx_resolved


def test_no_of_bm_is_not_shared_across_divisions():
    """2026-08-19 architecture correction: No of BM must be computed fresh
    per division from that division's own Annual Targets rows, never
    copied from Xandra's reference workbook -- verified real divergence:
    Ahmedabad Pool is 6 BM under Xandra, 5 under Onyx, 3 under Guardians."""
    from app.review_opus_mapping import GUARDIANS_OPUS_HQ_BLOCKS, ONYX_OPUS_HQ_BLOCKS

    def bm_for(blocks, hq):
        block = next(b for b in blocks if b.hq == hq)
        if block.annual_targets_keys is None:
            return None
        return {k for k in block.annual_targets_keys}  # just confirm it resolves; actual sum needs Annual Targets

    xandra_block = next(b for b in XANDRA_OPUS_HQ_BLOCKS if b.hq == "Ahmedabad Pool")
    onyx_block = next(b for b in ONYX_OPUS_HQ_BLOCKS if b.hq == "Ahmedabad Pool")
    guardians_block = next(b for b in GUARDIANS_OPUS_HQ_BLOCKS if b.hq == "Ahmedabad Pool")
    # Each division's own Annual Targets rows for the same HQ are genuinely
    # different rows (different (region, hq) tuples repeated N times each,
    # reflecting that division's own duplicate-row count) -- the field
    # simply doesn't exist as a static value on OpusHqBlock at all anymore.
    assert not hasattr(xandra_block, "no_of_bm")
    assert xandra_block.annual_targets_keys is not None
    assert onyx_block.annual_targets_keys is not None
    assert guardians_block.annual_targets_keys is not None


# --- Real-file validation (regression guard for the SUM/sign-flip fix) ------

@_REQUIRES_REAL_FILES
def test_guntur_block_matches_reference_workbook_exactly():
    ready, missing = opus_prerequisites_ready("Xandra")
    if not ready:
        pytest.skip(f"Xandra source files not fully uploaded/valid: {missing}")

    at_lookup = _load_annual_targets("Xandra")
    primary_lookups = _load_primary_sales_lookups("opus_primary_sales", "Xandra")
    ly_lookups = _load_primary_sales_lookups("opus_last_year_primary_sales", "Xandra")
    secondary_lookup = _load_secondary_sales("Xandra")

    guntur_block = next(b for b in XANDRA_OPUS_HQ_BLOCKS if b.hq == "Guntur")
    computed = _compute_hq_block(guntur_block, at_lookup, primary_lookups, ly_lookups, secondary_lookup, OPUS_REPORT_MONTHS)
    assert computed.unresolved is False
    assert computed.no_of_bm == 1

    formula_rows = recompute_formula_rows(computed, OPUS_REPORT_MONTHS)
    all_rows = dict(computed.source_rows)
    for label in all_rows:
        all_rows[label] = dict(all_rows[label])
        all_rows[label]["CUMMULATIVE"] = sum(all_rows[label][m] for m in OPUS_REPORT_MONTHS)
    all_rows.update(formula_rows)

    # SECONDARY is deliberately excluded: the live Secondary Sales upload's
    # July figure has genuinely diverged from the reference workbook's
    # snapshot since it was manually created (verified 2026-08-19 -- the
    # raw source file itself contains the new figure, not a calculation
    # bug) -- every other row is checked exactly.
    for label in ROW_LABELS:
        if label == "SECONDARY":
            continue
        for m in list(OPUS_REPORT_MONTHS) + ["CUMMULATIVE"]:
            assert all_rows[label][m] == pytest.approx(GUNTUR_REFERENCE[label][m], abs=1e-6), f"{label} {m}"


# hq_count per division once a real HQ Distribution file is uploaded and
# _filter_applicable_blocks genuinely excludes non-applicable HQs (see
# app.review_opus_service._filter_applicable_blocks) -- no longer the flat
# 165-block reference structure size, which was only ever the count when
# no HQ Distribution file existed yet (the "treat everything as
# applicable" fallback). Verified directly against generate_opus_summary's
# own logged output, independent of this test, 2026-08-19.
_EXPECTED_HQ_COUNT = {"Xandra": 159, "Onyx": 73, "Guardians": 33}


@_REQUIRES_REAL_FILES
@pytest.mark.parametrize("division", DIVISIONS)
def test_generate_opus_summary_end_to_end(division):
    ready, missing = opus_prerequisites_ready(division)
    if not ready:
        pytest.skip(f"{division} source files not fully uploaded/valid: {missing}")

    result = generate_opus_summary(division)
    assert result["success"] is True, result["errors"]
    assert result["hq_count"] == _EXPECTED_HQ_COUNT[division]
    assert os.path.isfile(result["file_path"])

    from app.review_opus_service import generated_opus_preview_path
    assert generated_opus_preview_path(division).is_file()


def test_unknown_division_reported_not_raised():
    result = generate_opus_summary("NotADivision")
    assert result["success"] is False
    assert result["errors"]
