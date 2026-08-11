"""Generates and stores Inventory Monitoring replenishment thresholds from
an uploaded Previous Month Sales Report, in its current compact format:
Division, CFA, Item Name, Packing, Sales (see app/excel_validation.py's
SALES_REPORT_REQUIRED_COLUMNS). The report carries no item code or item
group at all, so thresholds are keyed by (CFA, Item Name) instead --
`branch_location` below holds the report's own "CFA" column value.

Scope: this module ONLY generates and stores thresholds. It does NOT
compare thresholds against inventory, does NOT generate replenishment
alerts, and does NOT calculate shortages -- that's a later step (see
app/replenishment_service.py).

The uploaded sales report is a transactional register (one row per
invoice line item), so many rows commonly share the same
(CFA, Item Name) -- that's the expected normal case, not an edge case,
and is why every group's Sales is summed rather than averaged or taken
as-is.

Matching key (see normalize_match_key): thresholds are looked up by
`(branch_key, item_key)` -- normalized, case/whitespace-insensitive forms
of CFA/Item Name -- not by the raw display strings. The Sales Report and
the Inventory Report are two independently-typed files that routinely
spell "the same" CFA or item slightly differently (case, doubled spaces);
exact-string matching silently drops every such row, which is the root
cause behind the Inventory Dashboard showing "0 Products Evaluated" even
after a successful upload. branch_location/item_name keep their original
spelling for display; branch_key/item_key are used for every lookup and
upsert, in this module and in app/replenishment_service.py.

Ahmedabad CWH (Central Warehouse) exclusion: Ahmedabad CWH is the parent
warehouse that feeds every actual CFA, not a CFA itself, and is excluded
entirely from threshold generation -- see is_ahmedabad_cwh_branch below.
Its sales rows are dropped before grouping (never counted toward any
CFA's previous_month_sales) and no threshold row is ever created for it.
Its raw data is still imported/validated normally -- only this
downstream calculation excludes it; a future warehouse module will use
the same raw import for CWH-specific logic.

Two threshold values are always computed and stored:
- raw_threshold = previous_month_sales * the current Inventory Threshold
  Multiplier (app/inventory_parameters_service.py), unrounded.
- packed_threshold = raw_threshold rounded UP to the nearest multiple of
  the item's Packing quantity -- never down, so an order always covers at
  least the raw threshold's worth of stock. Replenishment
  (app/replenishment_service.py) always compares stock against
  packed_threshold; raw_threshold is kept only so the Threshold Display
  Mode setting (Raw Quantity vs Packs) can show either without
  recomputing.
"""

import math
import re
from datetime import datetime

import pandas as pd
from loguru import logger

from app.inventory_parameters_service import DISPLAY_MODE_PACKS, get_threshold_display_mode, get_threshold_multiplier
from database.connection import get_config_session
from database.models import InventoryThreshold


def normalize_match_key(value) -> str:
    """Case/whitespace-insensitive form of an item name, used ONLY for
    matching/grouping -- never for display, which always uses the
    original spelling/casing as uploaded (same convention as
    app/excel_validation.py's _normalize_header, applied here to data
    values instead of column headers: NBSP-safe, whitespace-collapsed,
    lowercased).

    Two independently-authored Excel files (or even different rows within
    the same Sales Report) routinely differ in case and internal spacing
    for what is obviously the same product -- e.g. "Widget 30-Pack" vs
    "Widget  30-Pack" (doubled space). Without normalizing before
    comparing, every such difference silently drops the affected rows
    from evaluation with no error at all -- confirmed to be one root
    cause of the Inventory Dashboard showing "0 Products Evaluated" even
    after a successful upload.

    Deliberately does NOT strip parenthetical text -- unlike
    normalize_branch_match_key below, an item name's parenthetical
    content is usually part of its real identity (e.g. "VITACORE C PLUS
    (10'S)" is a different, distinct pack-size variant, not decoration to
    discard)."""
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = text.strip().lower()
    return re.sub(r"\s+", " ", text)


def normalize_branch_match_key(value) -> str:
    """Case/whitespace-insensitive CFA identifier extracted from a branch
    name, used ONLY for matching a branch against a threshold's CFA --
    never for display. The Inventory Report's own BranchLocation column
    is more granular than the Sales Report's CFA: it commonly appends a
    location descriptor and code in parentheses to the same CFA name --
    e.g. "RAIPUR (HEAD OFFICE) ( SRP )" or "AHMEDABAD (C&F) ( SFP )" --
    and multiple such physical stock locations legitimately belong to,
    and must be evaluated against, the SAME CFA-level threshold
    ("RAIPUR", "AHMEDABAD"). Confirmed against real production data:
    every failing BranchLocation's text before its first "(" matched a
    real CFA name from the Sales Report exactly -- this, not case
    differences, was the actual root cause of "0 Products Evaluated" on
    the real Inventory Report tested.

    Only the text before the first "(" is kept, then run through the same
    normalization as normalize_match_key() above. Safe to call on a Sales
    Report's own CFA value too -- one with no "(" at all passes through
    unchanged (aside from the usual case/whitespace normalization)."""
    if value is None:
        return ""
    return normalize_match_key(str(value).split("(")[0])


_AHMEDABAD_CWH_PATTERN = re.compile(
    r"ahmedabad.*\bcwh\b|ahmedabad.*\bcentral\s+warehouse\b|ahmedabad.*\bhead\s+office\b"
)


def is_ahmedabad_cwh_branch(value) -> bool:
    """True if `value` (a raw CFA/BranchLocation cell -- the Sales
    Report's CFA column or the Inventory Report's BranchLocation) refers
    to Ahmedabad CWH (Central Warehouse), the parent warehouse that feeds
    every actual CFA and must be excluded from CFA-level threshold,
    replenishment, and comparison calculations entirely (Phase 1 of the
    Inventory upgrade -- see V2_MIGRATION_LOG.md).

    CONFIRMED against the real production Inventory Report
    (STOCKSTATEMENT_*.csv, user-confirmed 2026-07-27): Ahmedabad is the
    only city with two distinct rows sharing one branch code ("SFP") --
    "AHMEDABAD (C&F) ( SFP )" (the real Ahmedabad CFA) and "AHMEDABAD
    (HEAD OFFICE) ( SFP )" (the Central Warehouse itself). Every OTHER
    city has exactly one row, always using "(HEAD OFFICE)" as ITS real
    CFA's own location suffix (e.g. "RAIPUR (HEAD OFFICE) ( SRP )") --
    "head office" is therefore only a CWH signal when paired with
    "ahmedabad" specifically; it must never be matched on its own, or
    every other city's only real CFA row would be wrongly excluded too.

    Prior to this confirmation, "AHMEDABAD (HEAD OFFICE)" was NOT
    matched and was being silently merged into the Ahmedabad CFA's own
    threshold/replenishment comparison alongside "AHMEDABAD (C&F)" --
    a real bug, not by design; fixed here.

    Deliberately checks the RAW value, before normalize_branch_match_key's
    "text before first '('" truncation -- truncating first would collapse
    "AHMEDABAD (HEAD OFFICE)" to the same "ahmedabad" key as "AHMEDABAD
    (C&F)" and make the two indistinguishable. Matches "cwh" or "central
    warehouse" as well (kept for forward compatibility, in case a future
    report ever renames the location to include one of those explicitly),
    case-insensitively, anywhere after "ahmedabad". Does NOT match
    "AHMEDABAD (C&F)" (the real Ahmedabad CFA), nor any other city's own
    "(HEAD OFFICE)" row."""
    if value is None:
        return False
    text = str(value).replace("\xa0", " ").strip().lower()
    return bool(_AHMEDABAD_CWH_PATTERN.search(text))


_PACKING_SEPARATOR_PATTERN = re.compile(r"[*×xX]")

# Hard business rule (temporary override, effective 2026-07-28 -- explicit
# instruction, in force until the user says otherwise): the current real
# dataset has ONLY two genuine pack sizes, 10 and 72 -- nothing else. For
# multiplication-notation cells ("N*10", "1*72", etc.), if EITHER of these
# two values appears anywhere among the split parts, that value wins,
# regardless of which position it's in or what the other part is ("1*10",
# "10*10", "5*10" all -> 10; "1*72" -> 72). This exists specifically
# because the pre-Milestone-50 "multiply the parts together" bug's
# artifact (e.g. 100, from "10*10") can still be sitting in
# already-uploaded data being re-read, and the last-number rule alone
# isn't a strong enough guarantee against every possible stray notation
# the real files might contain. Does NOT touch a plain, separator-free
# cell (e.g. "30") -- there is no multiplication artifact to correct
# there, so it is only flagged, never silently coerced.
_HARD_RULE_PACKING_SIZES = (10.0, 72.0)


def _parse_packing(value) -> float:
    """Parse one Packing cell into a single numeric pack size -- the
    number of SALEABLE UNITS in one pack, which is what threshold and
    replenishment rounding must use.

    Confirmed against a real Previous Month Sales Report: Packing cells
    routinely use a "STRIPS/BOXES/CARTONS * UNITS-PER-STRIP" notation
    (e.g. "10*10", "1*10", "5×12", "20x15"), never a plain number, when
    they use a separator at all. The FIRST number is only how many
    strips/boxes/cartons make up the outer pack; the SECOND (LAST)
    number is the actual saleable-unit pack size that rounding must use
    -- e.g. "10*10" means 10 strips of 10 units each, so the correct
    packing size is 10, NOT 100.

    Milestone 50 fix: this used to MULTIPLY the parts together (e.g.
    "10*10" -> 100), which silently inflated the pack size used for
    every threshold/replenishment/deficit rounding calculation for any
    item using this notation. It now takes ONLY the last numeric value
    after splitting on any of "*", "×", or "x"/"X" (case-insensitive) --
    "10*10" -> 10, "1*10" -> 10, "5×12" -> 12, "20x15" -> 15.

    Milestone 52 hard rule: for a multiplication-notation cell, if 10 or
    72 (see _HARD_RULE_PACKING_SIZES) appears ANYWHERE among the split
    parts, that value is used directly -- overriding the plain
    last-number result if they'd otherwise disagree. A plain,
    separator-free cell is never touched by this rule.

    `pd.to_numeric("10*10", errors="coerce")` returns NaN for a string
    like that, and NaN survives every downstream step to become 0 if not
    handled here -- this was the original, still-relevant root cause of
    every threshold showing "Packing = 0" before this parser existed.

    Tries a plain number first (a cell with no separator at all, e.g.
    "72", is returned unchanged), then the last-number rule above.
    Returns NaN, never 0, if neither parses -- 0 must only ever come from
    the caller's own explicit, visible fallback (see
    generate_thresholds_from_sales), never be silently manufactured
    inside this parser."""
    if value is None:
        return float("nan")
    text = str(value).strip()
    if not text:
        return float("nan")

    direct = pd.to_numeric(text, errors="coerce")
    if pd.notna(direct):
        if direct not in _HARD_RULE_PACKING_SIZES:
            logger.warning(
                f"Packing value {value!r} parsed to {float(direct)}, outside the current "
                f"hard-rule permitted sizes {_HARD_RULE_PACKING_SIZES} -- using it as-is "
                "(no multiplication notation present to correct against)."
            )
        return float(direct)

    parts = _PACKING_SEPARATOR_PATTERN.split(text)
    if len(parts) > 1:
        numeric_parts = [pd.to_numeric(p.strip(), errors="coerce") for p in parts]
        for allowed in _HARD_RULE_PACKING_SIZES:
            if any(pd.notna(p) and float(p) == allowed for p in numeric_parts):
                return allowed

        last_value = pd.to_numeric(parts[-1].strip(), errors="coerce")
        if pd.notna(last_value):
            logger.warning(
                f"Packing value {value!r} parsed to {float(last_value)} (last number), outside "
                f"the current hard-rule permitted sizes {_HARD_RULE_PACKING_SIZES} -- neither "
                "10 nor 72 was found among its parts."
            )
            return float(last_value)

    logger.warning(f"Could not parse Packing value {value!r} as a number or N*M notation")
    return float("nan")


def _packed_threshold(raw_threshold: float, packing: float) -> float:
    """Round `raw_threshold` UP to the nearest multiple of `packing`.
    Falls back to the raw threshold, unrounded, if packing is
    missing/zero/invalid -- there is no meaningful "nearest multiple of
    nothing"."""
    if not packing or packing <= 0:
        return raw_threshold
    return math.ceil(raw_threshold / packing) * packing


def round_up_to_pack(value: float, packing: float | None) -> float:
    """Round `value` UP to the nearest multiple of `packing` -- never
    down. Falls back to `value` unrounded if packing is missing/zero/
    invalid. Identical rule to _packed_threshold() above (used for
    InventoryThreshold.packed_threshold), exposed here as a public,
    reusable function since Milestone 45's format_deficit_display() and
    Milestone 46's CWH-availability shortage comparison
    (app/replenishment_service.py) both need the same NUMERIC rounded
    value, not just a formatted display string."""
    if not packing or packing <= 0:
        return value
    return math.ceil(value / packing) * packing


def _format_quantity(value: float) -> str:
    """'180' for a whole number, '180.5' otherwise -- thresholds are
    always whole units after packed-threshold rounding, but a raw
    (unrounded) threshold may not be."""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def format_threshold_display(packed_threshold: float, packing: float | None) -> str:
    """Format a packed threshold for display according to the current
    Threshold Display Mode (see app/inventory_parameters_service.py) --
    e.g. "180" in Raw Quantity mode, "180 (6 Packs)" in Packs mode. Falls
    back to the plain quantity if packing is missing/zero/invalid, since a
    pack count is meaningless without a valid pack size. Purely a display
    concern -- every replenishment calculation always uses the numeric
    packed_threshold directly, never this formatted string."""
    quantity_text = _format_quantity(packed_threshold)
    if get_threshold_display_mode() != DISPLAY_MODE_PACKS or not packing or packing <= 0:
        return quantity_text
    packs = round(packed_threshold / packing)
    return f"{quantity_text} ({packs} Pack{'' if packs == 1 else 's'})"


def format_deficit_display(stock_deficit: float, packing: float | None) -> str:
    """Format a Replenishment page Stock Deficit/Replenishment Quantity
    value for display, applying the EXACT same pack-rounding rule
    format_threshold_display() applies to packed_threshold -- round UP to
    the nearest multiple of `packing` (never down; see _packed_threshold),
    then format per the current Threshold Display Mode (the same toggle
    already used for Threshold, now extended to this page too): e.g.
    "180" in Raw Quantity mode, "180 (6 Packs)" in Packs mode, for a raw
    deficit of 175 and packing of 30.

    Always rounds, in BOTH display modes -- Raw Quantity mode shows the
    already-rounded plain number, it does not mean "show the unrounded raw
    deficit." Falls back to the plain (unrounded) quantity if packing is
    missing/zero/invalid, same as format_threshold_display(). A healthy
    row's stock_deficit is always 0, which rounds to 0 regardless of
    packing -- this function is only ever meaningfully exercised for rows
    actually requiring replenishment.

    Purely a display concern -- the underlying stored stock_deficit value
    (and the replenishment_required/healthy decision it's derived from)
    is completely unchanged; only what's shown here differs."""
    packed_deficit = round_up_to_pack(stock_deficit, packing)
    quantity_text = _format_quantity(packed_deficit)
    if get_threshold_display_mode() != DISPLAY_MODE_PACKS or not packing or packing <= 0:
        return quantity_text
    packs = round(packed_deficit / packing)
    return f"{quantity_text} ({packs} Pack{'' if packs == 1 else 's'})"


def generate_thresholds_from_sales(df: pd.DataFrame) -> dict:
    """Group the validated Sales DataFrame by normalized (CFA, Item Name)
    -- see normalize_match_key -- sum Sales per group as "previous month
    sales", and upsert one InventoryThreshold row per group with
    raw_threshold and packed_threshold (see module docstring).

    Division and Packing are carried through from the report as-is (the
    first NON-BLANK value seen per group -- expected to be constant per
    Item Name, same assumption the old Item Group/Item Name pairing
    made). Packing is deliberately NOT filled to 0 until AFTER this
    "first non-blank" lookup: pandas' groupby "first" already skips NaN
    to find a real value elsewhere in the group, but only if the column
    still has genuine NaN for blank cells when grouped -- filling blanks
    to 0 beforehand would defeat that skip and let a blank row's now-0
    value win purely because it happened to come first, even when the
    same item has a real Packing value on another row.

    Packing itself is parsed with _parse_packing, not a bare
    pd.to_numeric() -- confirmed against a real Previous Month Sales
    Report, every Packing cell uses "N*M" notation (e.g. "10*10"), which
    pd.to_numeric() alone cannot read at all (returns NaN for every row).
    _parse_packing takes only the LAST number (the actual saleable-unit
    pack size, e.g. "10*10" -> 10, not 100 -- see Milestone 50).

    Full-replacement architecture (supersedes Milestone 49's
    "full-refresh, never delete" design -- that design recalculated every
    combination's threshold numbers against the current multiplier but
    left a combination ABSENT from this upload's own data sitting in the
    table forever with its stale descriptive/sales data, which is exactly
    the bug this now fixes): a Previous Month Sales Report upload is a
    complete monthly snapshot, so EVERY existing InventoryThreshold row is
    deleted first, then one fresh row is inserted per (branch_key,
    item_key) combination found in THIS upload -- nothing else survives.
    A product dropped from this month's report (discontinued, renamed,
    or simply omitted) no longer has a threshold row at all, instead of
    lingering with last month's numbers.

    Returns: {rows_processed, unique_combinations, created,
    excluded_cwh_rows}
    """
    rows_processed = len(df)
    multiplier = get_threshold_multiplier()

    working = df.copy()
    working["CFA"] = working["CFA"].astype(str).str.strip()
    working["Item Name"] = working["Item Name"].astype(str).str.strip()

    # Ahmedabad CWH is the parent warehouse, not a CFA -- exclude its rows
    # from threshold calculation entirely, before grouping/summing, so its
    # sales never inflate any real CFA's previous_month_sales and no
    # threshold row is ever generated for it (see is_ahmedabad_cwh_branch).
    cwh_mask = working["CFA"].map(is_ahmedabad_cwh_branch)
    excluded_cwh_rows = int(cwh_mask.sum())
    if excluded_cwh_rows:
        logger.info(
            f"Excluding {excluded_cwh_rows} Ahmedabad CWH sales row(s) from threshold "
            "calculation -- CWH is the parent warehouse, not a CFA"
        )
        working = working[~cwh_mask]

    working["Sales"] = pd.to_numeric(working["Sales"], errors="coerce").fillna(0)
    # _parse_packing (not pd.to_numeric) -- real Packing cells use "N*M"
    # notation, see docstring above. NOT filled to 0 here -- filled only
    # after grouping, on the final aggregated value (below), so a
    # genuinely blank/unparsed cell never masks a real value elsewhere in
    # its group.
    working["Packing"] = working["Packing"].map(_parse_packing)

    working["_branch_key"] = working["CFA"].map(normalize_branch_match_key)
    working["_item_key"] = working["Item Name"].map(normalize_match_key)

    grouped = working.groupby(["_branch_key", "_item_key"], as_index=False).agg(
        branch_location=("CFA", "first"),
        item_name=("Item Name", "first"),
        division=("Division", "first"),
        packing=("Packing", "first"),
        previous_month_sales=("Sales", "sum"),
    )
    grouped["packing"] = grouped["packing"].fillna(0)

    # Fresh, this-upload data per (branch_key, item_key) -- the ONLY
    # combinations that will exist in InventoryThreshold once this upload
    # finishes processing.
    fresh_by_key: dict[tuple, dict] = {}
    for _, row in grouped.iterrows():
        fresh_by_key[(row["_branch_key"], row["_item_key"])] = {
            "branch_location": row["branch_location"],
            "item_name": row["item_name"],
            "division": row["division"],
            "packing": float(row["packing"]),
            "previous_month_sales": float(row["previous_month_sales"]),
        }

    created = 0
    now = datetime.now()

    session = get_config_session()
    try:
        # Full replacement: a Previous Month Sales Report upload is a
        # complete monthly snapshot, not an incremental delta -- delete
        # every existing threshold row (including any stale Ahmedabad CWH
        # rows from a pre-exclusion upload) before inserting fresh ones,
        # so a product this upload doesn't mention no longer has a
        # threshold row at all instead of lingering with last month's data.
        deleted = session.query(InventoryThreshold).delete()
        if deleted:
            logger.info(f"Cleared {deleted} existing threshold row(s) before rebuilding from this upload")

        for (branch_key, item_key), fresh in fresh_by_key.items():
            raw_threshold = fresh["previous_month_sales"] * multiplier
            packed_threshold = _packed_threshold(raw_threshold, fresh["packing"])
            session.add(
                InventoryThreshold(
                    branch_location=fresh["branch_location"],
                    branch_key=branch_key,
                    division=fresh["division"],
                    item_name=fresh["item_name"],
                    item_key=item_key,
                    packing=fresh["packing"],
                    previous_month_sales=fresh["previous_month_sales"],
                    raw_threshold=raw_threshold,
                    packed_threshold=packed_threshold,
                    last_updated=now,
                )
            )
            created += 1
        session.commit()
    finally:
        session.close()

    unique_combinations = len(fresh_by_key)
    logger.info(
        f"Generated thresholds from {rows_processed} sales row(s) using CFA Threshold Multiplier="
        f"{multiplier}: {unique_combinations} unique CFA+item combination(s) in this upload "
        f"({created} created), {excluded_cwh_rows} Ahmedabad CWH row(s) excluded"
    )

    return {
        "rows_processed": rows_processed,
        "unique_combinations": unique_combinations,
        "created": created,
        "excluded_cwh_rows": excluded_cwh_rows,
    }


def get_all_thresholds() -> list[dict]:
    """All threshold records, shaped for the Thresholds page's table:
    branch, division, item_name, packing, previous_month_sales,
    raw_threshold, packed_threshold, threshold_display (already formatted
    per the current Threshold Display Mode), last_updated (formatted as a
    date string)."""
    session = get_config_session()
    try:
        rows = session.query(InventoryThreshold).order_by(
            InventoryThreshold.branch_location, InventoryThreshold.item_name
        ).all()
        return [
            {
                "branch": row.branch_location,
                "division": row.division or "",
                "item_name": row.item_name,
                "packing": row.packing,
                "previous_month_sales": row.previous_month_sales,
                "raw_threshold": row.raw_threshold,
                "packed_threshold": row.packed_threshold,
                "threshold_display": format_threshold_display(row.packed_threshold, row.packing),
                "last_updated": row.last_updated.strftime("%Y-%m-%d") if row.last_updated else "",
            }
            for row in rows
        ]
    finally:
        session.close()


def get_thresholds_lookup() -> dict:
    """{(branch_key, item_key): {branch_location, item_name, division,
    packing, previous_month_sales, raw_threshold, packed_threshold}} --
    O(1) lookup for the Replenishment engine
    (app/replenishment_service.py), keyed by the normalized match key
    (see normalize_match_key), not the raw display strings. Read-only;
    does not touch threshold generation."""
    session = get_config_session()
    try:
        rows = session.query(InventoryThreshold).all()
        return {
            (row.branch_key, row.item_key): {
                "branch_location": row.branch_location,
                "item_name": row.item_name,
                "division": row.division,
                "packing": row.packing,
                "previous_month_sales": row.previous_month_sales,
                "raw_threshold": row.raw_threshold,
                "packed_threshold": row.packed_threshold,
            }
            for row in rows
        }
    finally:
        session.close()
