"""Tests for Coverage Summary generation (app.review_coverage_service).

Two kinds of coverage, same split as tests/test_review_opus_service.py:
  - Synthetic tests (fast, no file dependency) for _compute_bm_block's pure
    arithmetic -- the two-population distinction (Total Rxrs/Support vs
    RGD Missed), zero-denominator safety, HQ applicability matching.
  - Real-file validation (skips cleanly if review_uploads/ isn't present)
    that regenerates Xandra/Onyx/Guardians and spot-checks one real BM
    (SF1619, Xandra) against values hand-verified directly from the raw
    source files on 2026-08-19 -- see conversation history for the
    verification: 44 Total Rxrs, 33 Total RGD Rxrs, support sum
    287286.55/100000, RGD support sum 251443.65/100000, 0 RGD Missed.
"""

import os

import pandas as pd
import pytest

from app.review_coverage_service import (
    COVERAGE_REPORT_MONTHS,
    DIVISIONS,
    ROW_LABELS,
    _build_bm_roster,
    _compute_bm_block,
    _hq_is_applicable,
    coverage_prerequisites_ready,
    generate_coverage_summary,
)

_REAL_UPLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "review_uploads")
_REQUIRES_REAL_FILES = pytest.mark.skipif(
    not os.path.isdir(_REAL_UPLOADS_DIR), reason="no real review_uploads/ files present on this machine"
)


# --- Synthetic: pure arithmetic layer (no files needed) ---------------------

def _avg_df(emp_code="E1", division="Xandra", hq="Guntur", visit_days=20.0, calls=150.0, call_avg=7.5):
    return pd.DataFrame([
        {"Division": division, "Region": "R", "Employee Name": "Test BM", "Employee Code": emp_code,
         "Desig.": "BM", "Reporting HQ": hq, "Parameters": "Field Visit Days", "Apr-2026": visit_days},
        {"Division": division, "Region": "R", "Employee Name": "Test BM", "Employee Code": emp_code,
         "Desig.": "BM", "Reporting HQ": hq, "Parameters": "# of Doctor Visits", "Apr-2026": calls},
        {"Division": division, "Region": "R", "Employee Name": "Test BM", "Employee Code": emp_code,
         "Desig.": "BM", "Reporting HQ": hq, "Parameters": "Doctor Call Average", "Apr-2026": call_avg},
    ])


def _bm_row(emp_code="E1", division="Xandra", hq="Guntur"):
    return pd.Series({"Division": division, "Region": "R", "Employee Name": "Test BM",
                       "Employee Code": emp_code, "Desig.": "BM", "Reporting HQ": hq})


APR = pd.Timestamp("2026-04-01")
SUPPORT_COLS = {"APR": APR}
VISIT_COL = "BM Visit Count Apr-2026"


def _vs_df(rows):
    # rows: list of (dr_code, category, support_value_or_None, visit_count_or_None)
    columns = ["BM code", "Dr. Code", "Category", APR, VISIT_COL]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame([
        {"BM code": "E1", "Dr. Code": dr, "Category": cat, APR: sup, VISIT_COL: vc}
        for dr, cat, sup, vc in rows
    ])


def test_avg_calls_rows_pass_through_with_call_average_rounded():
    avg_df = _avg_df(call_avg=7.5)
    vs_df = _vs_df([])
    block = _compute_bm_block(_bm_row(), avg_df, vs_df, SUPPORT_COLS, ("APR",))
    assert block.rows["Field Visit Days"]["APR"] == 20.0
    assert block.rows["Total Calls"]["APR"] == 150.0
    assert block.rows["Call Average"]["APR"] == 8  # round(7.5) -- banker's rounding, but even here


def test_non_blank_support_population_is_total_rxrs_and_support():
    vs_df = _vs_df([
        ("D1", "General", 100000, 1),
        ("D2", "General", None, None),  # blank support -- excluded from Total Rxrs
        ("D3", "B-RGD", 50000, 1),
    ])
    block = _compute_bm_block(_bm_row(), _avg_df(), vs_df, SUPPORT_COLS, ("APR",))
    assert block.rows["Total Rxrs"]["APR"] == 2
    assert block.rows["Total Dr Support"]["APR"] == pytest.approx(1.5)  # (100000+50000)/100000


def test_rgd_category_is_anything_not_general():
    vs_df = _vs_df([
        ("D1", "General", 100000, 1),
        ("D2", "B-RGD", 50000, 1),
        ("D3", "B-RGD/A-RGD", 30000, 1),
    ])
    block = _compute_bm_block(_bm_row(), _avg_df(), vs_df, SUPPORT_COLS, ("APR",))
    assert block.rows["Total RGD Rxrs"]["APR"] == 2
    assert block.rows["Total RGD Support"]["APR"] == pytest.approx(0.8)  # (50000+30000)/100000


def test_no_rgd_doctors_gives_zero_not_none():
    vs_df = _vs_df([("D1", "General", 100000, 1)])
    block = _compute_bm_block(_bm_row(), _avg_df(), vs_df, SUPPORT_COLS, ("APR",))
    assert block.rows["Total RGD Rxrs"]["APR"] == 0
    assert block.rows["Total RGD Support"]["APR"] == 0.0
    assert block.rows["% RGD Support"]["APR"] == 0.0


def test_rgd_support_equal_to_total_support_gives_100_percent():
    vs_df = _vs_df([("D1", "B-RGD", 100000, 1)])
    block = _compute_bm_block(_bm_row(), _avg_df(), vs_df, SUPPORT_COLS, ("APR",))
    assert block.rows["% RGD Support"]["APR"] == pytest.approx(1.0)


def test_zero_total_support_gives_none_percent_not_a_crash():
    vs_df = _vs_df([("D1", "General", None, 1)])  # only doctor is blank -> total support 0
    block = _compute_bm_block(_bm_row(), _avg_df(), vs_df, SUPPORT_COLS, ("APR",))
    assert block.rows["Total Dr Support"]["APR"] == 0.0
    assert block.rows["% RGD Support"]["APR"] is None


def test_zero_doctors_entirely_gives_zero_counts_not_a_crash():
    block = _compute_bm_block(_bm_row(), _avg_df(), _vs_df([]), SUPPORT_COLS, ("APR",))
    assert block.rows["Total Rxrs"]["APR"] == 0
    assert block.rows["Total Dr Support"]["APR"] == 0.0
    assert block.rows["% RGD Support"]["APR"] is None


def test_rgd_missed_uses_wider_population_than_total_rxrs():
    """Spec's core distinction: RGD Missed counts every RGD doctor assigned
    to the BM regardless of blank support this month -- D2 here has blank
    support (excluded from Total RGD Rxrs) but must still count toward RGD
    Missed's denominator, and its blank BM Visit Count makes it 'missed'."""
    vs_df = _vs_df([
        ("D1", "B-RGD", 50000, 1),      # non-blank support, visited -> not missed
        ("D2", "B-RGD", None, None),    # blank support (excluded from Total RGD Rxrs) but blank visit -> missed
        ("D3", "General", 100000, None),  # not RGD -- irrelevant to RGD Missed
    ])
    block = _compute_bm_block(_bm_row(), _avg_df(), vs_df, SUPPORT_COLS, ("APR",))
    assert block.rows["Total RGD Rxrs"]["APR"] == 1  # only D1 (non-blank support)
    assert block.rows["RGD Missed"]["APR"] == 1       # only D2 (blank visit count, RGD regardless of support)


def test_rgd_missed_zero_when_all_rgd_docs_visited():
    vs_df = _vs_df([("D1", "B-RGD", 50000, 1), ("D2", "B-RGD", None, 1)])
    block = _compute_bm_block(_bm_row(), _avg_df(), vs_df, SUPPORT_COLS, ("APR",))
    assert block.rows["RGD Missed"]["APR"] == 0


def test_different_months_can_have_different_values():
    two_months_support = {"APR": APR, "MAY": pd.Timestamp("2026-05-01")}
    vs_df = pd.DataFrame([
        {"BM code": "E1", "Dr. Code": "D1", "Category": "General",
         APR: 100000, two_months_support["MAY"]: 200000,
         "BM Visit Count Apr-2026": 1, "BM Visit Count May-2026": None},
    ])
    block = _compute_bm_block(_bm_row(), _avg_df(), vs_df, two_months_support, ("APR", "MAY"))
    assert block.rows["Total Dr Support"]["APR"] == pytest.approx(1.0)
    assert block.rows["Total Dr Support"]["MAY"] == pytest.approx(2.0)


def test_july_is_not_in_coverage_report_months():
    # Real Visits & Support files have no July support-value column at all
    # (verified 2026-08-19) -- July must never be fabricated.
    assert "JUL" not in COVERAGE_REPORT_MONTHS
    assert COVERAGE_REPORT_MONTHS == ("APR", "MAY", "JUN")


def test_row_labels_are_the_nine_spec_kpis_in_order():
    assert ROW_LABELS == (
        "Field Visit Days", "Total Calls", "Call Average", "Total Rxrs", "Total RGD Rxrs",
        "Total Dr Support", "Total RGD Support", "% RGD Support", "RGD Missed",
    )


# --- HQ applicability matching -----------------------------------------------

def test_hq_matches_exact_name():
    assert _hq_is_applicable("Guntur", {"GUNTUR"}) is True


def test_hq_matches_via_pool_suffix():
    assert _hq_is_applicable("Ahmedabad", {"AHMEDABAD POOL"}) is True


def test_hq_matches_via_spelling_alias():
    assert _hq_is_applicable("Chapra", {"CHHAPRA"}) is True
    assert _hq_is_applicable("Purnia", {"PURNEA"}) is True
    assert _hq_is_applicable("Gadhinglaj", {"GADHINGLAJ/KUDAL"}) is True
    assert _hq_is_applicable("Nasik", {"NASHIK POOL"}) is True


def test_hq_with_no_match_is_not_applicable():
    # 2026-08-19: user confirmed these stay excluded rather than guessed --
    # neighborhood/directional splits and genuinely absent names.
    assert _hq_is_applicable("Bangalor_Kormangal", {"BANGALORE POOL"}) is False
    assert _hq_is_applicable("Delhi - East", {"DELHI POOL", "SOUTH DELHI POOL"}) is False
    assert _hq_is_applicable("Valsad", {"NAVSARI/VALSAD"}) is False
    assert _hq_is_applicable("Barmer", set()) is False


def test_roster_falls_back_to_everything_when_no_hq_distribution_file(monkeypatch):
    import app.review_coverage_service as coverage_service
    monkeypatch.setattr(coverage_service, "get_valid_hqs_for_division", lambda division: None)
    avg_df = pd.concat([_avg_df(emp_code="E1", hq="AnyHQ"), _avg_df(emp_code="E2", hq="AnotherHQ")], ignore_index=True)
    roster = _build_bm_roster("Xandra", avg_df)
    assert set(roster["Employee Code"]) == {"E1", "E2"}


def test_roster_excludes_hq_not_applicable_to_division(monkeypatch):
    import app.review_coverage_service as coverage_service
    monkeypatch.setattr(coverage_service, "get_valid_hqs_for_division", lambda division: {"GUNTUR"})
    avg_df = pd.concat([_avg_df(emp_code="E1", hq="Guntur"), _avg_df(emp_code="E2", hq="Amritsar")], ignore_index=True)
    roster = _build_bm_roster("Xandra", avg_df)
    assert set(roster["Employee Code"]) == {"E1"}


def test_roster_keyed_by_employee_code_not_name_for_duplicate_names():
    avg_df = pd.concat([
        _avg_df(emp_code="E1", hq="Guntur"),
        _avg_df(emp_code="E2", hq="Guntur"),  # same "Test BM" name, different code
    ], ignore_index=True)
    roster = _build_bm_roster("Xandra", avg_df)
    assert set(roster["Employee Code"]) == {"E1", "E2"}


# --- Real-file validation (regression guard) --------------------------------

@_REQUIRES_REAL_FILES
def test_sf1619_block_matches_hand_verified_raw_source_values():
    from app.review_coverage_service import (
        _load_avg_calls, _load_visits_support, _support_value_columns_by_month,
    )

    ready, missing = coverage_prerequisites_ready("Xandra")
    if not ready:
        pytest.skip(f"Xandra source files not fully uploaded/valid: {missing}")

    avg_df = _load_avg_calls("Xandra")
    vs_df = _load_visits_support("Xandra")
    support_cols = _support_value_columns_by_month(vs_df)
    roster = _build_bm_roster("Xandra", avg_df)

    bm_row = roster[roster["Employee Code"] == "SF1619"]
    if bm_row.empty:
        pytest.skip("SF1619 not present in current Xandra upload")
    # _compute_bm_block now takes this BM's ALREADY-filtered rows (see its
    # own docstring, 2026-08-20) -- the production caller groups once via
    # pandas .groupby(); a single explicit filter here is the test-side
    # equivalent for this one BM.
    avg_sub = avg_df[avg_df["Employee Code"] == "SF1619"]
    bm_docs = vs_df[vs_df["BM code"] == "SF1619"]
    block = _compute_bm_block(bm_row.iloc[0], avg_sub, bm_docs, support_cols, COVERAGE_REPORT_MONTHS)

    assert block.rows["Total Rxrs"]["APR"] == 44
    assert block.rows["Total RGD Rxrs"]["APR"] == 33
    assert block.rows["Total Dr Support"]["APR"] == pytest.approx(2.8728655)
    assert block.rows["Total RGD Support"]["APR"] == pytest.approx(2.5144365)
    assert block.rows["RGD Missed"]["APR"] == 0


@_REQUIRES_REAL_FILES
@pytest.mark.parametrize("division", DIVISIONS)
def test_generate_coverage_summary_end_to_end(division):
    ready, missing = coverage_prerequisites_ready(division)
    if not ready:
        pytest.skip(f"{division} source files not fully uploaded/valid: {missing}")

    result = generate_coverage_summary(division)
    assert result["success"] is True, result["errors"]
    assert result["bm_count"] > 0
    assert os.path.isfile(result["file_path"])

    from app.review_coverage_service import generated_coverage_preview_path
    assert generated_coverage_preview_path(division).is_file()


@_REQUIRES_REAL_FILES
def test_bm_code_column_casing_normalized_across_divisions():
    """2026-08-19 discovery: Xandra/Guardians' Visits & Support file calls
    it "BM code", Onyx calls it "BM Code" -- same column, different casing.
    Generation for all three divisions must not KeyError on this."""
    from app.review_coverage_service import _load_visits_support

    for division in DIVISIONS:
        ready, missing = coverage_prerequisites_ready(division)
        if not ready:
            pytest.skip(f"{division} source files not fully uploaded/valid: {missing}")
        df = _load_visits_support(division)
        assert "BM code" in df.columns


def test_unknown_division_reported_not_raised():
    result = generate_coverage_summary("NotADivision")
    assert result["success"] is False
    assert result["errors"]
