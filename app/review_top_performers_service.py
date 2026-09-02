"""ALL TOP PERFORMERS -- Review System's cross-division ranking layer,
built ON TOP OF the existing Opus Summary computation
(app.review_opus_service), never a parallel Primary Sales/BM-count
pipeline of its own.

First ranking: TOP 10 HQs -- CORPORATE LEVEL. "Corporate" means combining
all three divisions (Xandra/Onyx/Guardians) at the HQ level. An HQ present
in more than one division has its Primary Sales AND its BM count SUMMED
across those divisions BEFORE computing YPM (Yield Per Month) --
    corporate YPM = (sum of that HQ's own PRIMARY across every division
                      it appears in) / (sum of that HQ's own no_of_bm
                      across those same divisions)
never by adding the divisions' own YPM values together, and never by
averaging them. An HQ present in only one division just uses that
division's own PRIMARY/no_of_bm untouched -- the same formula, a
one-division sum is a no-op.

Pinned to JULY (OPUS_REPORT_MONTHS[-1], the current reporting window's
last month) per explicit instruction -- one month's ranking, not one per
month yet. Extending to another month later is passing a different
`month` argument to top_hqs_corporate(), never a redesign; this module
never hardcodes "JUL" as a literal disconnected from
app.review_opus_service.OPUS_REPORT_MONTHS.

Reuses app.review_opus_service._compute_opus_blocks() -- the SAME Annual
Targets / Primary Sales / Secondary Sales load-and-calculate pipeline
generate_opus_summary()'s own combined workbook uses, so this ranking and
the Opus Summary workbook can never disagree about a given HQ's own
PRIMARY or no_of_bm. Never reloads the Primary Sales source file itself
(that file's own read is cached inside review_opus_service by mtime,
independent of division -- see that module's own _SOURCE_DF_CACHE
docstring, so calling this for all three divisions costs one real read,
not three). An UNRESOLVED HQ block (no Region/HQ mapping to that
division's Annual Targets sheet -- see ComputedHqBlock's own docstring)
contributes nothing to any HQ's combined totals, same as it contributes
nothing to that division's own Opus Summary workbook.

Does NOT modify Opus Summary's own calculations, formatting, or output --
every function here only reads already-computed ComputedHqBlock rows and
ranks; no HQ's own PRIMARY/no_of_bm is ever recomputed or altered here.

Second ranking: TOP 10 BMs -- NUMBER OF RXRS. Reuses
app.review_coverage_service._compute_coverage_blocks() the same way --
reads each ComputedBmBlock's own already-computed "Total Rxrs" row rather
than counting anything itself, and combines all three divisions' BMs into
one flat pool (BMs, unlike HQs, only ever belong to a single division, so
there is no "Corporate vs Division-wise" distinction to make here).

Pinned to COVERAGE_REPORT_MONTHS[-1] ("JUN") by default, NOT July: Coverage
Summary deliberately never computes a July figure at all -- the real
Visits & Support source files have no July monthly support-value column
(see review_coverage_service's own module docstring, "Months: Apr/May/Jun
only") -- so "July Total Rxrs" does not exist to reuse. Using the latest
month Coverage Summary actually computes was an explicit product decision
(2026-08-24), not a guess; never hardcode "JUN" as a literal disconnected
from COVERAGE_REPORT_MONTHS.
"""

from app.review_coverage_service import COVERAGE_REPORT_MONTHS
from app.review_coverage_service import DIVISIONS as COVERAGE_DIVISIONS
from app.review_coverage_service import _compute_coverage_blocks, coverage_prerequisites_ready
from app.review_opus_mapping import OPUS_HQ_BLOCKS_BY_DIVISION
from app.review_opus_service import (
    DIVISIONS,
    OPUS_REPORT_MONTHS,
    _compute_opus_blocks,
    _norm,
    opus_prerequisites_ready,
    recompute_formula_rows,
)

TOP_N = 10


def top_hqs_corporate(month: str = OPUS_REPORT_MONTHS[-1], blocks_by_division: dict | None = None) -> dict:
    """Ranks every HQ across all three divisions by CORPORATE YPM for
    `month` (default: the current reporting window's last month) and
    returns the Top TOP_N, highest YPM first.

    `blocks_by_division`: optional {division: [ComputedHqBlock, ...]}. When
    given (by load_all_top_performers's own batch pass, which computes
    every division's blocks exactly once), those already-computed blocks
    are combined directly instead of this function calling
    _compute_opus_blocks itself -- so a caller that already has every
    division's blocks doesn't pay for them a second time here. When
    omitted, behaves exactly as before (computes them itself).

    Never raises for expected failure modes (a division's Opus mapping/
    source data not ready) -- reports them in the returned dict instead,
    same contract as app.review_opus_service.generate_opus_summary.

    Returns:
        {
            "success": bool,
            "month": str,
            "rankings": [
                {"rank": int, "hq": str, "corporate_ypm": float,
                 "primary": float, "bm_count": int, "divisions": [str, ...]},
                ...  # at most TOP_N entries
            ],
            "errors": [str],
        }
    """
    if month not in OPUS_REPORT_MONTHS:
        return {"success": False, "month": month, "rankings": [],
                "errors": [f"Unknown month {month!r} -- expected one of {OPUS_REPORT_MONTHS}."]}

    if blocks_by_division is None:
        missing_prereqs = []
        for division in DIVISIONS:
            if OPUS_HQ_BLOCKS_BY_DIVISION.get(division) is None:
                missing_prereqs.append(f"{division}: no Region/HQ mapping has been built yet")
                continue
            ready, missing = opus_prerequisites_ready(division)
            if not ready:
                missing_prereqs.append(f"{division}: {', '.join(missing)}")
        if missing_prereqs:
            return {"success": False, "month": month, "rankings": [],
                    "errors": [f"Opus Summary source data isn't ready for every division: {'; '.join(missing_prereqs)}"]}

        try:
            blocks_by_division = {division: _compute_opus_blocks(division) for division in DIVISIONS}
        except Exception as exc:
            return {"success": False, "month": month, "rankings": [], "errors": [f"Ranking failed: {exc!r}"]}

    # {normalized HQ name: {"hq": display name, "primary": float,
    #  "bm_count": int, "divisions": set of division names}}
    combined: dict = {}

    for division in DIVISIONS:
        for block in blocks_by_division.get(division) or []:
            if block.unresolved or block.no_of_bm is None:
                continue
            primary = block.source_rows["PRIMARY"].get(month)
            if primary is None:
                continue
            key = _norm(block.hq)
            entry = combined.setdefault(
                key, {"hq": block.hq, "primary": 0.0, "bm_count": 0, "divisions": set()}
            )
            entry["primary"] += float(primary)
            entry["bm_count"] += int(block.no_of_bm)
            entry["divisions"].add(division)

    ranked = []
    for entry in combined.values():
        if entry["bm_count"] <= 0:
            continue  # no valid denominator -- excluded, never a division-by-zero guess
        corporate_ypm = entry["primary"] / entry["bm_count"]
        ranked.append({
            "hq": entry["hq"],
            "corporate_ypm": corporate_ypm,
            "primary": entry["primary"],
            "bm_count": entry["bm_count"],
            "divisions": sorted(entry["divisions"]),
        })

    ranked.sort(key=lambda r: r["corporate_ypm"], reverse=True)
    top = ranked[:TOP_N]
    for i, r in enumerate(top, start=1):
        r["rank"] = i

    return {"success": True, "month": month, "rankings": top, "errors": []}


def top_hqs_division(division: str, month: str = OPUS_REPORT_MONTHS[-1], blocks: list | None = None) -> dict:
    """Ranks HQs within a SINGLE division by that division's own existing
    YPM for `month` -- no cross-division combining, no recalculated
    Primary/BM-count arithmetic of its own. Reads
    app.review_opus_service.recompute_formula_rows()'s own "YPM (PRIMARY)"
    row per HQ block -- the exact same figure Opus Summary's own workbook
    shows for that division -- so this ranking and that division's Opus
    Summary output can never disagree about a given HQ's YPM.

    `blocks`: optional pre-computed [ComputedHqBlock, ...] for `division`
    (e.g. from load_all_top_performers's own batch pass) -- when given,
    skips calling _compute_opus_blocks itself. Prerequisite/mapping checks
    still run either way (cheap lookups, and they're what a caller with no
    precomputed blocks needs to decide whether computing them is even
    possible).

    Returns:
        {
            "success": bool,
            "month": str,
            "division": str,
            "rankings": [
                {"rank": int, "hq": str, "ypm": float,
                 "primary": float, "bm_count": int, "divisions": [division]},
                ...  # at most TOP_N entries
            ],
            "errors": [str],
        }
    """
    if month not in OPUS_REPORT_MONTHS:
        return {"success": False, "month": month, "division": division, "rankings": [],
                "errors": [f"Unknown month {month!r} -- expected one of {OPUS_REPORT_MONTHS}."]}
    if division not in DIVISIONS:
        return {"success": False, "month": month, "division": division, "rankings": [],
                "errors": [f"Unknown division {division!r} -- expected one of {DIVISIONS}."]}

    if blocks is None:
        if OPUS_HQ_BLOCKS_BY_DIVISION.get(division) is None:
            return {"success": False, "month": month, "division": division, "rankings": [],
                    "errors": [f"{division}: no Region/HQ mapping has been built yet"]}
        ready, missing = opus_prerequisites_ready(division)
        if not ready:
            return {"success": False, "month": month, "division": division, "rankings": [],
                    "errors": [f"{division}: {', '.join(missing)}"]}

        try:
            blocks = _compute_opus_blocks(division)
        except Exception as exc:
            return {"success": False, "month": month, "division": division, "rankings": [],
                    "errors": [f"Ranking failed: {exc!r}"]}

    ranked = []
    for block in blocks:
        if block.unresolved or not block.no_of_bm:
            continue
        ypm = recompute_formula_rows(block, OPUS_REPORT_MONTHS)["YPM (PRIMARY)"].get(month)
        if ypm is None:
            continue
        ranked.append({
            "hq": block.hq,
            "ypm": ypm,
            "primary": float(block.source_rows["PRIMARY"][month]),
            "bm_count": int(block.no_of_bm),
            "divisions": [division],
        })

    ranked.sort(key=lambda r: r["ypm"], reverse=True)
    top = ranked[:TOP_N]
    for i, r in enumerate(top, start=1):
        r["rank"] = i

    return {"success": True, "month": month, "division": division, "rankings": top, "errors": []}


def top_bms_by_rxrs(month: str = COVERAGE_REPORT_MONTHS[-1], blocks_by_division: dict | None = None) -> dict:
    """Ranks BMs across all three divisions by their existing "Total Rxrs"
    figure for `month`, descending, Top TOP_N. Reads
    app.review_coverage_service.ComputedBmBlock.rows["Total Rxrs"][month]
    directly -- the same figure Coverage Summary's own workbook shows for
    that BM/month -- never recounted here. All three divisions are
    combined into one flat pool (a BM belongs to exactly one division, so
    there is no Corporate/Division-wise distinction to make, unlike HQ).

    `blocks_by_division`: optional {division: [ComputedBmBlock, ...]} --
    same reuse pattern as top_hqs_corporate's own (see there for why);
    used by load_all_top_performers's batch pass.

    Returns:
        {
            "success": bool,
            "month": str,
            "rankings": [
                {"rank": int, "name": str, "emp_code": str,
                 "division": str, "hq": str, "total_rxrs": int},
                ...  # at most TOP_N entries
            ],
            "errors": [str],
        }
    """
    if month not in COVERAGE_REPORT_MONTHS:
        return {"success": False, "month": month, "rankings": [],
                "errors": [f"Unknown month {month!r} -- expected one of {COVERAGE_REPORT_MONTHS}."]}

    if blocks_by_division is None:
        missing_prereqs = []
        for division in COVERAGE_DIVISIONS:
            ready, missing = coverage_prerequisites_ready(division)
            if not ready:
                missing_prereqs.append(f"{division}: {', '.join(missing)}")
        if missing_prereqs:
            return {"success": False, "month": month, "rankings": [],
                    "errors": [f"Coverage Summary source data isn't ready for every division: {'; '.join(missing_prereqs)}"]}
        try:
            blocks_by_division = {division: _compute_coverage_blocks(division) for division in COVERAGE_DIVISIONS}
        except Exception as exc:
            return {"success": False, "month": month, "rankings": [], "errors": [f"Ranking failed: {exc!r}"]}

    ranked = []
    for division in COVERAGE_DIVISIONS:
        for block in blocks_by_division.get(division) or []:
            total_rxrs = block.rows.get("Total Rxrs", {}).get(month)
            if total_rxrs is None:
                continue
            ranked.append({
                "name": block.name, "emp_code": block.emp_code,
                "division": division, "hq": block.hq, "total_rxrs": int(total_rxrs),
            })

    ranked.sort(key=lambda r: r["total_rxrs"], reverse=True)
    top = ranked[:TOP_N]
    for i, r in enumerate(top, start=1):
        r["rank"] = i

    return {"success": True, "month": month, "rankings": top, "errors": []}


# --- Unified batch load ------------------------------------------------------
# One Load click must populate EVERY currently supported combination at
# once (see the module docstring at the call site in ui.review_top_performers_page
# for why: a page with one shared result container and a separate fetch
# per combination is how one combination's numbers end up rendered under
# another combination's controls -- whichever fetch's background thread
# happens to finish last wins the shared container, regardless of which
# combination is currently selected on screen. The fix is architectural,
# not a bug in the ranking math: never fetch per-combination again: fetch
# once for everything, keyed, and only ever READ from that cache when the
# user switches controls.

def available_hq_combinations() -> list:
    """Every HQ-level (scope, division) combination load_all_top_performers
    will compute, discovered from DIVISIONS -- never a separately
    hand-typed list, so a division added there is picked up automatically.
    ("HQ" is the only ranking level today; this returns the identity keys
    used by load_all_top_performers's own `results` dict, one entry per
    combination -- (level, scope, division), division is None for
    Corporate.)"""
    combos = [("HQ", "Corporate", None)]
    combos.extend(("HQ", "Division-wise", division) for division in DIVISIONS)
    return combos


def load_all_top_performers(
    month: str = OPUS_REPORT_MONTHS[-1], bm_month: str = COVERAGE_REPORT_MONTHS[-1], report_progress=None,
) -> dict:
    """One batch pass that computes every combination currently supported
    on the All Top Performers page: available_hq_combinations() (built on
    Opus Summary) plus the one BM ranking, ("BM", "Number of RXRs", None)
    (built on Coverage Summary). Each source's own _compute_*_blocks() is
    called EXACTLY ONCE per division and its result reused for every
    ranking that needs it -- Opus's blocks feed both Division-wise and
    Corporate (see top_hqs_corporate's own docstring); Coverage's blocks
    feed the one BM ranking. Calling each ranking function independently
    per combination (the per-combination-Load UI this replaces) would
    compute a division's blocks more than once.

    A division that isn't ready (missing mapping/source files, or a
    computation error) is recorded in `errors` and excluded from whichever
    combination needs it, without stopping the other combinations from
    loading -- same contract for both Opus/HQ and Coverage/BM divisions.

    Returns:
        {
            "success": bool,   # True unless every combination failed
            "month": str,      # the Opus/HQ month
            "bm_month": str,   # the Coverage/BM month (see top_bms_by_rxrs
                                # for why this is never "JUL")
            "results": {
                (level, scope, division): <same shape top_hqs_corporate()/
                                            top_hqs_division()/
                                            top_bms_by_rxrs() return>,
                ...  # available_hq_combinations() plus
                     # ("BM", "Number of RXRs", None)
            },
            "errors": [str],  # collected per-division problems
        }
    """
    errors: list = []
    results: dict = {}

    # --- HQ (Opus Summary) -- 75% of the progress bar, the dominant real
    # cost (Primary/LY Primary Sales reads documented at 35s+92s in
    # app.review_opus_service, paid once and cached across divisions).
    HQ_BUDGET = 75.0
    blocks_by_division: dict = {}
    hq_divisions = list(DIVISIONS)
    hq_steps = len(hq_divisions) or 1
    for step, division in enumerate(hq_divisions):
        base = HQ_BUDGET * step / hq_steps
        span = HQ_BUDGET / hq_steps

        def _hq_progress(pct, message, base=base, span=span, division=division) -> None:
            if report_progress:
                report_progress(base + (pct / 100.0) * span, f"{division}: {message}")

        if OPUS_HQ_BLOCKS_BY_DIVISION.get(division) is None:
            errors.append(f"{division}: no Region/HQ mapping has been built yet")
            blocks_by_division[division] = None
            continue
        ready, missing = opus_prerequisites_ready(division)
        if not ready:
            errors.append(f"{division}: {', '.join(missing)}")
            blocks_by_division[division] = None
            continue
        try:
            blocks_by_division[division] = _compute_opus_blocks(division, report_progress=_hq_progress)
        except Exception as exc:
            errors.append(f"{division}: ranking failed -- {exc!r}")
            blocks_by_division[division] = None

    if report_progress:
        report_progress(HQ_BUDGET + 1, "Ranking Corporate...")
    missing_hq_divisions = [d for d in hq_divisions if blocks_by_division.get(d) is None]
    if missing_hq_divisions:
        results[("HQ", "Corporate", None)] = {
            "success": False, "month": month, "rankings": [],
            "errors": [f"Opus Summary source data isn't ready for every division: "
                       f"{', '.join(missing_hq_divisions)}"],
        }
    else:
        results[("HQ", "Corporate", None)] = top_hqs_corporate(month=month, blocks_by_division=blocks_by_division)

    if report_progress:
        report_progress(HQ_BUDGET + 3, "Ranking each division...")
    for division in hq_divisions:
        blocks = blocks_by_division.get(division)
        if blocks is None:
            results[("HQ", "Division-wise", division)] = {
                "success": False, "month": month, "division": division, "rankings": [],
                "errors": [e for e in errors if e.startswith(f"{division}:")] or [f"{division}: not ready"],
            }
        else:
            results[("HQ", "Division-wise", division)] = top_hqs_division(division, month=month, blocks=blocks)

    # --- BM (Coverage Summary) -- 15% of the progress bar (its Avg &
    # Calls / Visits & Support files run far smaller than Opus's Primary
    # Sales files -- tens of thousands of rows, not 80k-220k).
    BM_BASE, BM_BUDGET = HQ_BUDGET + 5, 15.0
    coverage_blocks_by_division: dict = {}
    coverage_divisions = list(COVERAGE_DIVISIONS)
    coverage_steps = len(coverage_divisions) or 1
    for step, division in enumerate(coverage_divisions):
        base = BM_BASE + BM_BUDGET * step / coverage_steps
        span = BM_BUDGET / coverage_steps

        def _bm_progress(pct, message, base=base, span=span, division=division) -> None:
            if report_progress:
                report_progress(base + (pct / 100.0) * span, f"{division}: {message}")

        ready, missing = coverage_prerequisites_ready(division)
        if not ready:
            errors.append(f"{division}: {', '.join(missing)}")
            coverage_blocks_by_division[division] = None
            continue
        try:
            coverage_blocks_by_division[division] = _compute_coverage_blocks(division, report_progress=_bm_progress)
        except Exception as exc:
            errors.append(f"{division}: BM ranking failed -- {exc!r}")
            coverage_blocks_by_division[division] = None

    if report_progress:
        report_progress(BM_BASE + BM_BUDGET + 2, "Ranking BMs...")
    missing_coverage_divisions = [d for d in coverage_divisions if coverage_blocks_by_division.get(d) is None]
    if missing_coverage_divisions:
        results[("BM", "Number of RXRs", None)] = {
            "success": False, "month": bm_month, "rankings": [],
            "errors": [f"Coverage Summary source data isn't ready for every division: "
                       f"{', '.join(missing_coverage_divisions)}"],
        }
    else:
        results[("BM", "Number of RXRs", None)] = top_bms_by_rxrs(
            month=bm_month, blocks_by_division=coverage_blocks_by_division,
        )

    if report_progress:
        report_progress(100, "Done.")

    return {
        "success": any(r["success"] for r in results.values()),
        "month": month, "bm_month": bm_month, "results": results, "errors": errors,
    }
