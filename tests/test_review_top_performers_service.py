"""Tests for ALL TOP PERFORMERS -> TOP 10 HQs -- CORPORATE LEVEL
(app.review_top_performers_service.top_hqs_corporate).

_compute_opus_blocks (app.review_opus_service's own real Annual Targets /
Primary Sales / Secondary Sales pipeline) is monkeypatched to fixed
ComputedHqBlock lists per division -- that pipeline's own correctness is
already covered end to end by tests/test_review_opus_service.py; these
tests focus purely on the NEW cross-division combining/ranking logic this
module adds on top of it.
"""

import app.review_top_performers_service as top_performers
from app.review_opus_service import ComputedHqBlock

JUL = "JUL"


def _block(hq, primary_jul, no_of_bm, region="R", unresolved=False):
    """Builds a ComputedHqBlock with a COMPLETE source_rows shape (all rows
    recompute_formula_rows() needs, zeroed out except PRIMARY at JUL) so
    both top_hqs_corporate (reads PRIMARY directly) and top_hqs_division
    (runs the real recompute_formula_rows()) can use the same fixture."""
    if unresolved:
        return ComputedHqBlock(region=region, hq=hq, unresolved=True, no_of_bm=no_of_bm, source_rows={})
    months = top_performers.OPUS_REPORT_MONTHS
    zeros = {m: 0.0 for m in months}
    source_rows = {
        "TARGET": dict(zeros),
        "PRIMARY": {**zeros, JUL: primary_jul},
        "LY PRIMARY": dict(zeros),
        "SALABLE CN": dict(zeros),
        "EXPIRY CN": dict(zeros),
        "DUAL INCREMENT MIN ELIGIBLE TGT": dict(zeros),
    }
    return ComputedHqBlock(region=region, hq=hq, unresolved=False, no_of_bm=no_of_bm, source_rows=source_rows)


def _use_divisions(
    monkeypatch, blocks_by_division: dict, count_calls: dict | None = None,
    bm_blocks_by_division: dict | None = None,
):
    """blocks_by_division: {"Xandra": [block, ...], "Onyx": [...], "Guardians": [...]}
    -- every division in app.review_opus_service.DIVISIONS must be a key
    (an empty list is fine for a division with nothing to contribute).

    Also monkeypatches DIVISIONS itself to exactly `blocks_by_division`'s
    own keys -- lets a test hand in 2 or 4 divisions instead of the usual
    3 to prove nothing here is hardcoded to "three divisions" (see the
    dynamic-discovery tests below), with zero other code changes.

    `count_calls`, if given, is incremented once per _compute_opus_blocks
    call (keyed by division) -- lets a test assert a division's blocks
    were computed exactly once even though both Corporate and that
    division's own Division-wise ranking need them (the whole point of
    load_all_top_performers reusing one blocks_by_division pass).

    `bm_blocks_by_division`, if given, wires up the Coverage/BM side the
    same way (see _bm_block below) for tests that care about BM ranking.
    When omitted (the default), the Coverage side is stubbed to "not
    ready" -- load_all_top_performers's BM stage exists in EVERY test that
    uses this helper (it's one unconditional pass), and without this
    default every HQ-only test would otherwise hit this dev environment's
    real, uploaded Coverage Summary files (multi-minute real reads) purely
    as a side effect of testing HQ ranking."""
    monkeypatch.setattr(top_performers, "DIVISIONS", tuple(blocks_by_division))
    monkeypatch.setattr(
        top_performers, "OPUS_HQ_BLOCKS_BY_DIVISION",
        {division: object() for division in blocks_by_division},  # just needs to be non-None
    )
    monkeypatch.setattr(top_performers, "opus_prerequisites_ready", lambda division: (True, []))

    def _compute(division, report_progress=None):
        if count_calls is not None:
            count_calls[division] = count_calls.get(division, 0) + 1
        if report_progress:
            report_progress(10, "Loading Annual Targets...")
            report_progress(100, "Calculating...")
        return blocks_by_division[division]

    monkeypatch.setattr(top_performers, "_compute_opus_blocks", _compute)

    if bm_blocks_by_division is None:
        monkeypatch.setattr(top_performers, "coverage_prerequisites_ready", lambda division: (False, ["coverage_avg_calls"]))
    else:
        monkeypatch.setattr(top_performers, "COVERAGE_DIVISIONS", tuple(bm_blocks_by_division))
        monkeypatch.setattr(top_performers, "coverage_prerequisites_ready", lambda division: (True, []))
        monkeypatch.setattr(
            top_performers, "_compute_coverage_blocks",
            lambda division, report_progress=None: bm_blocks_by_division[division],
        )


def _bm_block(name, total_rxrs_jun, division="Xandra", hq="H", emp_code=None):
    """A ComputedBmBlock (app.review_coverage_service) with a COMPLETE
    "Total Rxrs" row (all COVERAGE_REPORT_MONTHS, zeroed out except JUN --
    the month top_bms_by_rxrs defaults to, see that function's own
    docstring for why never "JUL")."""
    from app.review_coverage_service import ComputedBmBlock, COVERAGE_REPORT_MONTHS
    zeros = {m: 0 for m in COVERAGE_REPORT_MONTHS}
    return ComputedBmBlock(
        division=division, region="R", hq=hq, emp_code=emp_code or name, name=name,
        designation="BM", rows={"Total Rxrs": {**zeros, "JUN": total_rxrs_jun}},
    )


def test_single_division_hq_corporate_ypm_equals_division_ypm(monkeypatch):
    """Single-division HQ: Corporate YPM should equal its existing
    division-level YPM (Primary / BM count, unchanged by combining)."""
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Guntur", primary_jul=150.0, no_of_bm=3)],
        "Onyx": [], "Guardians": [],
    })
    result = top_performers.top_hqs_corporate(month=JUL)
    assert result["success"] is True
    [guntur] = result["rankings"]
    assert guntur["hq"] == "Guntur"
    assert guntur["corporate_ypm"] == 50.0  # 150 / 3, exactly the division-level YPM
    assert guntur["divisions"] == ["Xandra"]


def test_two_division_hq_sums_before_dividing(monkeypatch):
    """The spec's own worked example: Xandra 30L/3 BMs, Onyx 20L/2 BMs ->
    corporate Primary=50L, BMs=5, corporate YPM=10L -- NEVER 10L+10L=20L,
    and NEVER the plain average of the two division YPMs."""
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Guntur", primary_jul=30.0, no_of_bm=3)],  # division YPM = 10
        "Onyx": [_block("Guntur", primary_jul=20.0, no_of_bm=2)],    # division YPM = 10
        "Guardians": [],
    })
    result = top_performers.top_hqs_corporate(month=JUL)
    [guntur] = result["rankings"]
    assert guntur["primary"] == 50.0
    assert guntur["bm_count"] == 5
    assert guntur["corporate_ypm"] == 10.0
    assert guntur["corporate_ypm"] != 10.0 + 10.0  # the forbidden "add the YPMs" result
    assert sorted(guntur["divisions"]) == ["Onyx", "Xandra"]


def test_two_division_hq_with_unequal_division_ypms_is_not_their_average(monkeypatch):
    """A case where naively averaging the two division YPMs would give a
    DIFFERENT (wrong) answer than sum-then-divide -- catches an accidental
    "average the YPMs" implementation, which the spec explicitly forbids
    alongside "add the YPMs" (see test_two_division_hq_sums_before_dividing).
    Xandra: 100 Primary / 2 BMs (division YPM = 50). Onyx: 10 Primary / 8
    BMs (division YPM = 1.25). Averaging those two YPMs gives 25.625, but
    the correct corporate YPM is (100+10)/(2+8) = 11.0."""
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Nashik", primary_jul=100.0, no_of_bm=2)],
        "Onyx": [_block("Nashik", primary_jul=10.0, no_of_bm=8)],
        "Guardians": [],
    })
    result = top_performers.top_hqs_corporate(month=JUL)
    [nashik] = result["rankings"]
    assert nashik["corporate_ypm"] == 11.0
    assert nashik["corporate_ypm"] != 25.625  # what averaging the two division YPMs would give


def test_three_division_hq_sums_all_three(monkeypatch):
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Ahmedabad Pool", primary_jul=60.0, no_of_bm=6)],
        "Onyx": [_block("Ahmedabad Pool", primary_jul=25.0, no_of_bm=5)],
        "Guardians": [_block("Ahmedabad Pool", primary_jul=15.0, no_of_bm=3)],
    })
    result = top_performers.top_hqs_corporate(month=JUL)
    [ahmedabad] = result["rankings"]
    assert ahmedabad["primary"] == 100.0
    assert ahmedabad["bm_count"] == 14
    assert ahmedabad["corporate_ypm"] == 100.0 / 14
    assert sorted(ahmedabad["divisions"]) == ["Guardians", "Onyx", "Xandra"]


def test_ranking_sorted_descending_by_corporate_ypm(monkeypatch):
    _use_divisions(monkeypatch, {
        "Xandra": [
            _block("Low", primary_jul=10.0, no_of_bm=10),   # YPM 1
            _block("High", primary_jul=90.0, no_of_bm=1),   # YPM 90
            _block("Mid", primary_jul=50.0, no_of_bm=5),    # YPM 10
        ],
        "Onyx": [], "Guardians": [],
    })
    result = top_performers.top_hqs_corporate(month=JUL)
    ypms = [r["corporate_ypm"] for r in result["rankings"]]
    assert ypms == sorted(ypms, reverse=True)
    assert [r["hq"] for r in result["rankings"]] == ["High", "Mid", "Low"]


def test_top_10_limit_never_shows_an_11th(monkeypatch):
    blocks = [_block(f"HQ{i}", primary_jul=float(i), no_of_bm=1) for i in range(1, 16)]  # 15 HQs
    _use_divisions(monkeypatch, {"Xandra": blocks, "Onyx": [], "Guardians": []})
    result = top_performers.top_hqs_corporate(month=JUL)
    assert len(result["rankings"]) == 10
    ranks = [r["rank"] for r in result["rankings"]]
    assert ranks == list(range(1, 11))
    # The highest-YPM 10 of the 15 survive -- HQ15..HQ6, never HQ5 or below.
    assert {r["hq"] for r in result["rankings"]} == {f"HQ{i}" for i in range(6, 16)}


def test_unresolved_hq_block_excluded_from_ranking(monkeypatch):
    """An UNRESOLVED HQ (no Region/HQ mapping for that division -- see
    ComputedHqBlock's own docstring) contributes nothing, exactly like it
    contributes nothing to that division's own Opus Summary workbook."""
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Ghost HQ", primary_jul=0.0, no_of_bm=None, unresolved=True)],
        "Onyx": [], "Guardians": [],
    })
    result = top_performers.top_hqs_corporate(month=JUL)
    assert result["rankings"] == []


def test_hq_with_zero_bm_count_excluded_not_division_by_zero(monkeypatch):
    _use_divisions(monkeypatch, {
        "Xandra": [_block("No BMs HQ", primary_jul=100.0, no_of_bm=0)],
        "Onyx": [], "Guardians": [],
    })
    result = top_performers.top_hqs_corporate(month=JUL)
    assert result["rankings"] == []


def test_missing_prerequisites_reports_error_not_partial_ranking(monkeypatch):
    monkeypatch.setattr(
        top_performers, "OPUS_HQ_BLOCKS_BY_DIVISION", {"Xandra": object(), "Onyx": object(), "Guardians": object()}
    )
    monkeypatch.setattr(
        top_performers, "opus_prerequisites_ready",
        lambda division: (False, ["opus_primary_sales"]) if division == "Onyx" else (True, []),
    )
    result = top_performers.top_hqs_corporate(month=JUL)
    assert result["success"] is False
    assert result["rankings"] == []
    assert "Onyx" in result["errors"][0]


def test_missing_region_hq_mapping_reports_error(monkeypatch):
    monkeypatch.setattr(
        top_performers, "OPUS_HQ_BLOCKS_BY_DIVISION", {"Xandra": object(), "Onyx": None, "Guardians": object()}
    )
    monkeypatch.setattr(top_performers, "opus_prerequisites_ready", lambda division: (True, []))
    result = top_performers.top_hqs_corporate(month=JUL)
    assert result["success"] is False
    assert "Onyx" in result["errors"][0]


def test_unknown_month_reports_error_not_exception():
    result = top_performers.top_hqs_corporate(month="XYZ")
    assert result["success"] is False
    assert result["rankings"] == []


# --- top_hqs_division ------------------------------------------------------
# Division-wise: a single division's own already-computed YPM per HQ, read
# via the real recompute_formula_rows() (never a second Primary/BM-count
# division here) -- these tests assert the result matches that function's
# own output exactly, not a re-derived value.

def test_division_ypm_matches_recompute_formula_rows(monkeypatch):
    from app.review_opus_service import recompute_formula_rows
    block = _block("Guntur", primary_jul=150.0, no_of_bm=3)
    _use_divisions(monkeypatch, {"Xandra": [block], "Onyx": [], "Guardians": []})
    result = top_performers.top_hqs_division("Xandra", month=JUL)
    assert result["success"] is True
    [guntur] = result["rankings"]
    expected_ypm = recompute_formula_rows(block, top_performers.OPUS_REPORT_MONTHS)["YPM (PRIMARY)"][JUL]
    assert guntur["ypm"] == expected_ypm
    assert guntur["primary"] == 150.0
    assert guntur["bm_count"] == 3
    assert guntur["divisions"] == ["Xandra"]


def test_division_wise_never_combines_other_divisions(monkeypatch):
    """The spec's own worked example, but Division-wise: Xandra's Guntur
    (30/3 => YPM 10) must be reported as-is, NOT combined with Onyx's
    Guntur (20/2) the way Corporate would -- Corporate's combined 10.0
    happens to equal this too, so use unequal figures to prove no
    combining happened."""
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Guntur", primary_jul=100.0, no_of_bm=2)],  # division YPM = 50
        "Onyx": [_block("Guntur", primary_jul=10.0, no_of_bm=8)],     # division YPM = 1.25
        "Guardians": [],
    })
    xandra_result = top_performers.top_hqs_division("Xandra", month=JUL)
    [guntur] = xandra_result["rankings"]
    assert guntur["ypm"] == 50.0
    assert guntur["primary"] == 100.0
    assert guntur["bm_count"] == 2


def test_division_wise_sorted_descending_and_capped_at_10(monkeypatch):
    blocks = [_block(f"HQ{i}", primary_jul=float(i), no_of_bm=1) for i in range(1, 16)]  # 15 HQs
    _use_divisions(monkeypatch, {"Xandra": blocks, "Onyx": [], "Guardians": []})
    result = top_performers.top_hqs_division("Xandra", month=JUL)
    assert len(result["rankings"]) == 10
    ypms = [r["ypm"] for r in result["rankings"]]
    assert ypms == sorted(ypms, reverse=True)
    assert {r["hq"] for r in result["rankings"]} == {f"HQ{i}" for i in range(6, 16)}


def test_division_wise_excludes_unresolved_and_zero_bm(monkeypatch):
    _use_divisions(monkeypatch, {
        "Xandra": [
            _block("Ghost HQ", primary_jul=0.0, no_of_bm=None, unresolved=True),
            _block("No BMs HQ", primary_jul=100.0, no_of_bm=0),
        ],
        "Onyx": [], "Guardians": [],
    })
    result = top_performers.top_hqs_division("Xandra", month=JUL)
    assert result["rankings"] == []


def test_division_wise_missing_prerequisites_reports_error(monkeypatch):
    monkeypatch.setattr(
        top_performers, "OPUS_HQ_BLOCKS_BY_DIVISION", {"Xandra": object(), "Onyx": object(), "Guardians": object()}
    )
    monkeypatch.setattr(
        top_performers, "opus_prerequisites_ready",
        lambda division: (False, ["opus_primary_sales"]) if division == "Xandra" else (True, []),
    )
    result = top_performers.top_hqs_division("Xandra", month=JUL)
    assert result["success"] is False
    assert result["rankings"] == []
    assert "Xandra" in result["errors"][0]


def test_division_wise_missing_region_hq_mapping_reports_error(monkeypatch):
    monkeypatch.setattr(
        top_performers, "OPUS_HQ_BLOCKS_BY_DIVISION", {"Xandra": None, "Onyx": object(), "Guardians": object()}
    )
    monkeypatch.setattr(top_performers, "opus_prerequisites_ready", lambda division: (True, []))
    result = top_performers.top_hqs_division("Xandra", month=JUL)
    assert result["success"] is False
    assert "Xandra" in result["errors"][0]


def test_division_wise_unknown_division_reports_error_not_exception():
    result = top_performers.top_hqs_division("Nowhere", month=JUL)
    assert result["success"] is False
    assert result["rankings"] == []


def test_division_wise_unknown_month_reports_error_not_exception():
    result = top_performers.top_hqs_division("Xandra", month="XYZ")
    assert result["success"] is False
    assert result["rankings"] == []


# --- load_all_top_performers / available_hq_combinations -------------------
# The unified batch load: one pass that populates every currently
# available (scope, division) combination, computing each division's
# blocks exactly once and reusing them for both that division's own
# Division-wise ranking and Corporate's combine -- these tests focus on
# that reuse, on isolation between combinations (the bug this replaces),
# and on discovery being driven by DIVISIONS rather than a hardcoded list.

def test_available_combinations_discovered_from_divisions(monkeypatch):
    monkeypatch.setattr(top_performers, "DIVISIONS", ("Xandra", "Onyx"))
    combos = top_performers.available_hq_combinations()
    assert combos == [
        ("HQ", "Corporate", None),
        ("HQ", "Division-wise", "Xandra"),
        ("HQ", "Division-wise", "Onyx"),
    ]


def test_batch_load_computes_each_divisions_blocks_exactly_once(monkeypatch):
    """The whole point of the batch pass: Corporate needs every division's
    blocks AND each division has its own Division-wise entry, but
    _compute_opus_blocks must only be called once per division, not once
    for Division-wise and again inside Corporate's own combine."""
    counts: dict = {}
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Guntur", primary_jul=30.0, no_of_bm=3)],
        "Onyx": [_block("Guntur", primary_jul=20.0, no_of_bm=2)],
        "Guardians": [],
    }, count_calls=counts)

    result = top_performers.load_all_top_performers(month=JUL)

    assert result["success"] is True
    assert counts == {"Xandra": 1, "Onyx": 1, "Guardians": 1}


def test_batch_load_isolation_xandra_and_onyx_never_cross_contaminate(monkeypatch):
    """The exact bug report: loading Xandra's Division-wise ranking must
    never appear as Onyx's, Guardians', or Corporate's result -- each
    combination keeps its own independent entry in `results`."""
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Guntur", primary_jul=100.0, no_of_bm=2)],   # Xandra-only HQ, YPM 50
        "Onyx": [_block("Nashik", primary_jul=10.0, no_of_bm=5)],      # Onyx-only HQ, YPM 2
        "Guardians": [_block("Pune", primary_jul=60.0, no_of_bm=3)],   # Guardians-only HQ, YPM 20
    })

    result = top_performers.load_all_top_performers(month=JUL)
    results = result["results"]

    xandra_hqs = {r["hq"] for r in results[("HQ", "Division-wise", "Xandra")]["rankings"]}
    onyx_hqs = {r["hq"] for r in results[("HQ", "Division-wise", "Onyx")]["rankings"]}
    guardians_hqs = {r["hq"] for r in results[("HQ", "Division-wise", "Guardians")]["rankings"]}

    assert xandra_hqs == {"Guntur"}
    assert onyx_hqs == {"Nashik"}
    assert guardians_hqs == {"Pune"}
    # No division's own ranking leaks another division's HQ.
    assert not (xandra_hqs & onyx_hqs)
    assert not (xandra_hqs & guardians_hqs)
    assert not (onyx_hqs & guardians_hqs)

    # Corporate combines all three HQs (each single-division, so its own
    # combine is a no-op per top_hqs_corporate's own contract).
    corporate_hqs = {r["hq"] for r in results[("HQ", "Corporate", None)]["rankings"]}
    assert corporate_hqs == {"Guntur", "Nashik", "Pune"}


def test_batch_load_division_wise_values_match_standalone_top_hqs_division(monkeypatch):
    """A batch-loaded Division-wise entry must be numerically identical to
    calling top_hqs_division() for that division on its own -- reusing
    precomputed blocks must never change the ranking output."""
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Guntur", primary_jul=100.0, no_of_bm=2), _block("Nashik", primary_jul=10.0, no_of_bm=5)],
        "Onyx": [], "Guardians": [],
    })
    batch = top_performers.load_all_top_performers(month=JUL)
    standalone = top_performers.top_hqs_division("Xandra", month=JUL)
    assert batch["results"][("HQ", "Division-wise", "Xandra")]["rankings"] == standalone["rankings"]


def test_batch_load_one_division_failing_does_not_block_the_others(monkeypatch):
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Guntur", primary_jul=100.0, no_of_bm=2)],
        "Onyx": [_block("Nashik", primary_jul=10.0, no_of_bm=5)],
        "Guardians": [],
    })
    monkeypatch.setattr(
        top_performers, "opus_prerequisites_ready",
        lambda division: (False, ["opus_primary_sales"]) if division == "Guardians" else (True, []),
    )

    result = top_performers.load_all_top_performers(month=JUL)
    results = result["results"]

    assert results[("HQ", "Division-wise", "Xandra")]["success"] is True
    assert results[("HQ", "Division-wise", "Onyx")]["success"] is True
    assert results[("HQ", "Division-wise", "Guardians")]["success"] is False
    # Corporate needs every division -- one missing means it fails too, but
    # is still reported as a normal (not raised) result.
    assert results[("HQ", "Corporate", None)]["success"] is False
    assert "Guardians" in result["errors"][0]


def test_batch_load_progress_reported_and_reaches_100(monkeypatch):
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Guntur", primary_jul=30.0, no_of_bm=3)], "Onyx": [], "Guardians": [],
    })
    updates = []
    top_performers.load_all_top_performers(month=JUL, report_progress=lambda pct, msg: updates.append((pct, msg)))

    assert len(updates) > 1  # more than one real checkpoint, not a single jump
    percents = [pct for pct, _msg in updates]
    assert percents == sorted(percents)  # monotonically advances, never regresses
    assert percents[-1] == 100
    assert all(0 <= p <= 100 for p in percents)


def test_batch_load_dynamic_discovery_picks_up_a_new_division(monkeypatch):
    """Adding a 4th division to DIVISIONS (simulating a future config
    change) must be picked up automatically -- no code change here."""
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Guntur", primary_jul=30.0, no_of_bm=3)],
        "Onyx": [], "Guardians": [], "Zenith": [_block("Nowhere HQ", primary_jul=5.0, no_of_bm=1)],
    })
    result = top_performers.load_all_top_performers(month=JUL)
    assert ("HQ", "Division-wise", "Zenith") in result["results"]
    assert [r["hq"] for r in result["results"][("HQ", "Division-wise", "Zenith")]["rankings"]] == ["Nowhere HQ"]


# --- top_bms_by_rxrs / BM in load_all_top_performers ------------------------
# BM ranking: Number of RXRs, using Coverage Summary's own already-computed
# "Total Rxrs" row for JUN (never JUL -- Coverage Summary deliberately
# never computes a July figure, see top_bms_by_rxrs's own docstring).
# Combines all three divisions into one flat pool (a BM belongs to exactly
# one division, unlike HQ).

def test_bm_ranked_descending_by_total_rxrs(monkeypatch):
    _use_divisions(monkeypatch, {"Xandra": [], "Onyx": [], "Guardians": []}, bm_blocks_by_division={
        "Xandra": [_bm_block("Low BM", total_rxrs_jun=5), _bm_block("High BM", total_rxrs_jun=50)],
        "Onyx": [_bm_block("Mid BM", total_rxrs_jun=20)], "Guardians": [],
    })
    result = top_performers.top_bms_by_rxrs()
    assert result["success"] is True
    assert result["month"] == "JUN"
    names = [r["name"] for r in result["rankings"]]
    assert names == ["High BM", "Mid BM", "Low BM"]
    totals = [r["total_rxrs"] for r in result["rankings"]]
    assert totals == sorted(totals, reverse=True)


def test_bm_never_uses_july_by_default():
    assert top_performers.top_bms_by_rxrs.__defaults__[0] == "JUN"


def test_bm_reads_existing_total_rxrs_value_not_recalculated(monkeypatch):
    """The whole point: total_rxrs in the ranking result must be exactly
    the ComputedBmBlock's own "Total Rxrs"[JUN] value, untouched."""
    block = _bm_block("Some BM", total_rxrs_jun=37)
    _use_divisions(monkeypatch, {"Xandra": [], "Onyx": [], "Guardians": []}, bm_blocks_by_division={
        "Xandra": [block], "Onyx": [], "Guardians": [],
    })
    result = top_performers.top_bms_by_rxrs()
    [row] = result["rankings"]
    assert row["total_rxrs"] == block.rows["Total Rxrs"]["JUN"] == 37


def test_bm_combines_all_divisions_into_one_flat_pool(monkeypatch):
    """Unlike HQ, a BM belongs to exactly one division -- no Corporate vs
    Division-wise distinction, just one ranked pool across all three."""
    _use_divisions(monkeypatch, {"Xandra": [], "Onyx": [], "Guardians": []}, bm_blocks_by_division={
        "Xandra": [_bm_block("X BM", total_rxrs_jun=10, division="Xandra")],
        "Onyx": [_bm_block("O BM", total_rxrs_jun=20, division="Onyx")],
        "Guardians": [_bm_block("G BM", total_rxrs_jun=30, division="Guardians")],
    })
    result = top_performers.top_bms_by_rxrs()
    assert [r["name"] for r in result["rankings"]] == ["G BM", "O BM", "X BM"]
    assert {r["division"] for r in result["rankings"]} == {"Xandra", "Onyx", "Guardians"}


def test_bm_top_10_limit_never_shows_an_11th(monkeypatch):
    blocks = [_bm_block(f"BM{i}", total_rxrs_jun=i) for i in range(1, 16)]  # 15 BMs
    _use_divisions(monkeypatch, {"Xandra": [], "Onyx": [], "Guardians": []}, bm_blocks_by_division={
        "Xandra": blocks, "Onyx": [], "Guardians": [],
    })
    result = top_performers.top_bms_by_rxrs()
    assert len(result["rankings"]) == 10
    ranks = [r["rank"] for r in result["rankings"]]
    assert ranks == list(range(1, 11))
    assert {r["name"] for r in result["rankings"]} == {f"BM{i}" for i in range(6, 16)}


def test_bm_missing_prerequisites_reports_error_not_partial_ranking(monkeypatch):
    monkeypatch.setattr(
        top_performers, "coverage_prerequisites_ready",
        lambda division: (False, ["coverage_avg_calls"]) if division == "Onyx" else (True, []),
    )
    result = top_performers.top_bms_by_rxrs()
    assert result["success"] is False
    assert result["rankings"] == []
    assert "Onyx" in result["errors"][0]


def test_bm_unknown_month_reports_error_not_exception():
    result = top_performers.top_bms_by_rxrs(month="XYZ")
    assert result["success"] is False
    assert result["rankings"] == []


def test_batch_load_includes_bm_ranking_alongside_hq(monkeypatch):
    """load_all_top_performers's one batch pass populates BOTH the HQ
    combinations and the BM ranking -- one Load click, everything."""
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Guntur", primary_jul=30.0, no_of_bm=3)], "Onyx": [], "Guardians": [],
    }, bm_blocks_by_division={
        "Xandra": [_bm_block("Top BM", total_rxrs_jun=99)], "Onyx": [], "Guardians": [],
    })
    result = top_performers.load_all_top_performers(month=JUL)
    bm_result = result["results"][("BM", "Number of RXRs", None)]
    assert bm_result["success"] is True
    assert bm_result["month"] == "JUN"
    assert [r["name"] for r in bm_result["rankings"]] == ["Top BM"]
    assert result["bm_month"] == "JUN"
    # HQ combinations are still present and correct alongside BM.
    assert result["results"][("HQ", "Division-wise", "Xandra")]["rankings"][0]["hq"] == "Guntur"


def test_batch_load_bm_not_ready_does_not_block_hq(monkeypatch):
    """A Coverage Summary readiness problem must not affect the HQ side --
    HQ_BUDGET stage and BM stage are independent pipelines within the
    same batch pass."""
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Guntur", primary_jul=30.0, no_of_bm=3)], "Onyx": [], "Guardians": [],
    })  # bm_blocks_by_division omitted -- Coverage stubbed "not ready"
    result = top_performers.load_all_top_performers(month=JUL)
    assert result["results"][("BM", "Number of RXRs", None)]["success"] is False
    assert result["results"][("HQ", "Division-wise", "Xandra")]["success"] is True


def test_batch_load_bm_progress_still_monotonic_and_reaches_100(monkeypatch):
    _use_divisions(monkeypatch, {
        "Xandra": [_block("Guntur", primary_jul=30.0, no_of_bm=3)], "Onyx": [], "Guardians": [],
    }, bm_blocks_by_division={
        "Xandra": [_bm_block("Top BM", total_rxrs_jun=99)], "Onyx": [], "Guardians": [],
    })
    updates = []
    top_performers.load_all_top_performers(month=JUL, report_progress=lambda pct, msg: updates.append((pct, msg)))
    percents = [pct for pct, _msg in updates]
    assert percents == sorted(percents)
    assert percents[-1] == 100
    assert all(0 <= p <= 100 for p in percents)
