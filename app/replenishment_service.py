"""Evaluates an uploaded Inventory Report against the already-generated
Threshold Database (see app/threshold_service.py) to identify which
products require replenishment.

Scope: this module ONLY compares inventory against stored thresholds and
persists the evaluation. It does NOT recalculate thresholds (thresholds
are read-only input here, via app.threshold_service.get_thresholds_lookup),
does NOT send notifications, and does NOT trigger automatic replenishment
-- that's a later step.

Column note: the Inventory Report's "TotalQty" column represents Closing
Stock (per the report's own field, confirmed at spec time) -- it is a
different quantity than the Sales Report's own "Sales" column (quantity
sold), even though both used to share the same canonical "TotalQty"
column name before the Sales Report's format changed (see
app/excel_validation.py).

Matching key: thresholds are looked up by the normalized
(branch_key, item_key) pair (see app/threshold_service.normalize_match_key),
not the raw BranchLocation/Item Name strings. The Sales Report and this
Inventory Report are two independently-typed files that routinely spell
"the same" CFA or item slightly differently (case, extra spaces) --
matching on the raw strings silently drops every such row, which was the
confirmed root cause of the Inventory Dashboard showing "0 Products
Evaluated" even after a successful upload. `item_code` is still read from
the Inventory Report's own row (that upload format is unchanged) and
stored on the resulting InventoryReplenishment record, but purely as
informational display -- it plays no part in matching.

Item Group / Division note: the current (pivoted-by-CFA) Inventory Report
format has no Item Group column at all, and the Sales Report's new format
no longer has one either. Division is sourced from the matched threshold
record instead (which always carries it, from the Previous Month Sales
Report that generated it), not from the Inventory Report's own row. This
also means a row with no matching threshold never gets a division value
in the first place, which is fine: such rows are skipped before division
is ever read.

Replenishment is always decided against the matched threshold's
packed_threshold (rounded up to a whole number of packs), never its
raw_threshold -- see app/threshold_service.py's module docstring.

Ahmedabad CWH (Central Warehouse) exclusion: Ahmedabad CWH is the parent
warehouse, not a CFA, and is excluded entirely from replenishment
evaluation -- see app.threshold_service.is_ahmedabad_cwh_branch. Its rows
are dropped before threshold lookup even runs, so it never factors into
any CFA demand/comparison decision. Its raw data is still
imported/validated normally -- only this downstream calculation excludes
it.
"""

from datetime import datetime

import pandas as pd
from loguru import logger

from app.cwh_service import get_cwh_stock_lookup
from app.threshold_service import (
    format_deficit_display,
    get_thresholds_lookup,
    is_ahmedabad_cwh_branch,
    normalize_branch_match_key,
    normalize_match_key,
    round_up_to_pack,
)
from database.connection import get_config_session
from database.models import InventoryReplenishment

STATUS_REPLENISHMENT_REQUIRED = "Replenishment Required"
STATUS_HEALTHY = "Healthy"

# The Replenishment report's column shape -- moved here from
# ui/inventory_replenishment_page.py (Milestone 56) so it lives at the
# same layer as the data itself, importable by BOTH the manual Export
# button (ui/inventory_replenishment_page.py) and the Automated Email
# System (app/inventory_notification_service.py) without either importing
# from the other -- there is exactly one definition of "what a
# Replenishment report row looks like," used everywhere a Replenishment
# report is produced, per the app's "only ONE export implementation"
# requirement. `WIDTHS` (pixel column widths) stays UI-only, in
# ui/inventory_replenishment_page.py, since it's a display-only concern
# the email attachment has no use for.
REPLENISHMENT_REPORT_COLUMNS = (
    "branch",
    "division",
    "item_code",
    "item_name",
    "closing_stock",
    "transit_stock",
    "effective_stock",
    "previous_month_sales_display",
    "deficit_display",
)
REPLENISHMENT_REPORT_HEADINGS = {
    "branch": "CFA",
    "division": "Division",
    "item_code": "Item Code",
    "item_name": "Item Name",
    "closing_stock": "Closing Stock",
    "transit_stock": "Transit Stock",
    "effective_stock": "Effective Available Stock",
    "previous_month_sales_display": "Previous Month Sales",
    "deficit_display": "Deficit",
}

# The CWH-shortage row highlight's colors, as plain hex strings (no
# leading '#', openpyxl's own convention) -- kept in sync BY HAND with
# ui.theme.Color.CRITICAL_ROW_BG ("FFC7CE") / CRITICAL_ROW_TEXT
# ("9C0006"). Deliberately NOT imported from ui.theme here: app/ is the
# business-logic layer and must not depend on a "ui" package (which would
# also be the wrong direction for a future headless/scheduled report
# sender with no Tk UI running at all -- see
# app/inventory_notification_service.py's module docstring). These two
# small, rarely-changing color codes are the one deliberately duplicated
# exception to the "single source of truth" rule above; everything else
# (columns, headings, the actual Excel-writing code) has exactly one
# definition.
REPLENISHMENT_REPORT_SHORTAGE_FILL = "FFC7CE"
REPLENISHMENT_REPORT_SHORTAGE_TEXT = "9C0006"


def _format_quantity(value: float) -> str:
    """'150' for a whole number, '150.5' otherwise -- same convention as
    app/threshold_service._format_quantity (kept as a small local copy
    rather than importing that private helper, matching the precedent
    already set by app/cwh_service.py's own local _format_number)."""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def evaluate_replenishment(df: pd.DataFrame) -> dict:
    """For every row in the validated Inventory Report DataFrame, look up
    its threshold by the normalized (branch_key, item_key) pair and
    compare Effective Available Stock (Closing Stock + Transit Stock)
    against the matched packed_threshold. Rows with no matching threshold
    are skipped (logged, never raised).

    Full replacement: an Inventory Report upload is a complete monthly
    snapshot, not an incremental delta -- every existing
    InventoryReplenishment row is deleted before this upload's rows are
    evaluated and inserted, so a product this upload doesn't mention (or
    that no longer has a matching threshold) no longer has a
    replenishment row at all, instead of lingering with a previous
    upload's stale closing/transit stock.

    Returns: {rows_processed, evaluated, skipped_no_threshold,
    replenishment_required, healthy}
    """
    rows_processed = len(df)

    working = df.copy()
    working["BranchLocation"] = working["BranchLocation"].astype(str).str.strip()
    working["Item Name"] = working["Item Name"].astype(str).str.strip()

    # Ahmedabad CWH is the parent warehouse, not a CFA -- exclude its rows
    # from replenishment evaluation entirely, before any threshold lookup
    # or demand/comparison logic runs against them (see
    # app.threshold_service.is_ahmedabad_cwh_branch).
    cwh_mask = working["BranchLocation"].map(is_ahmedabad_cwh_branch)
    excluded_cwh_rows = int(cwh_mask.sum())
    if excluded_cwh_rows:
        logger.info(
            f"Excluding {excluded_cwh_rows} Ahmedabad CWH inventory row(s) from replenishment "
            "evaluation -- CWH is the parent warehouse, not a CFA"
        )
        working = working[~cwh_mask]

    working["TotalQty"] = pd.to_numeric(working["TotalQty"], errors="coerce").fillna(0)
    working["Transit Stock"] = pd.to_numeric(working["Transit Stock"], errors="coerce").fillna(0)

    thresholds = get_thresholds_lookup()

    skipped_no_threshold = 0
    replenishment_required = 0
    healthy = 0
    now = datetime.now()
    rows_by_key: dict[tuple, InventoryReplenishment] = {}

    session = get_config_session()
    try:
        # Full replacement: delete every existing replenishment row
        # (including any stale Ahmedabad CWH rows from a pre-exclusion
        # upload) before rebuilding from this upload -- see docstring.
        deleted = session.query(InventoryReplenishment).delete()
        if deleted:
            logger.info(f"Cleared {deleted} existing replenishment row(s) before rebuilding from this upload")

        for _, row in working.iterrows():
            branch_location = row["BranchLocation"]
            item_name = row["Item Name"]
            item_code = row.get("Item Code")
            item_code = str(item_code).strip() if item_code is not None and str(item_code).strip() != "" else None

            item_key = normalize_match_key(item_name)
            # Two distinct keys, deliberately not the same value:
            # - cfa_key collapses sub-locations under the same CFA (see
            #   normalize_branch_match_key) purely to find the right
            #   CFA-level threshold to compare against.
            # - branch_key (below, InventoryReplenishment's own identity
            #   key) does NOT collapse sub-locations -- "AHMEDABAD (C&F)"
            #   and "AHMEDABAD (HEAD OFFICE)" are different physical
            #   warehouses with independently tracked stock, and must stay
            #   distinct rows here even though they share one threshold.
            cfa_key = normalize_branch_match_key(branch_location)
            branch_key = normalize_match_key(branch_location)

            threshold_record = thresholds.get((cfa_key, item_key))
            if threshold_record is None:
                logger.warning(
                    f"No threshold found for BranchLocation='{branch_location}', "
                    f"Item Name='{item_name}' — skipping row"
                )
                skipped_no_threshold += 1
                continue

            closing_stock = float(row["TotalQty"])  # Inventory Report's TotalQty = Closing Stock
            transit_stock = float(row["Transit Stock"])
            effective_available_stock = closing_stock + transit_stock
            raw_threshold = threshold_record["raw_threshold"]
            packed_threshold = threshold_record["packed_threshold"]

            if effective_available_stock < packed_threshold:
                status = STATUS_REPLENISHMENT_REQUIRED
                stock_deficit = packed_threshold - effective_available_stock
                replenishment_required += 1
            else:
                status = STATUS_HEALTHY
                stock_deficit = 0.0
                healthy += 1

            division = threshold_record["division"]
            packing = threshold_record["packing"]

            # Keyed by (branch_key, item_key), last row for a given key
            # wins if this upload happens to repeat one -- the table was
            # just cleared above, so this is a plain insert, not an
            # upsert; the dict (rather than adding straight away) is only
            # to survive an in-upload duplicate without violating the
            # branch_key+item_key unique constraint.
            rows_by_key[(branch_key, item_key)] = InventoryReplenishment(
                branch_location=branch_location,
                branch_key=branch_key,
                division=division,
                item_code=item_code,
                item_name=item_name,
                item_key=item_key,
                packing=packing,
                closing_stock=closing_stock,
                transit_stock=transit_stock,
                effective_available_stock=effective_available_stock,
                raw_threshold=raw_threshold,
                packed_threshold=packed_threshold,
                stock_deficit=stock_deficit,
                status=status,
                last_updated=now,
            )

        session.add_all(rows_by_key.values())
        session.commit()
    finally:
        session.close()

    evaluated = replenishment_required + healthy
    logger.info(
        f"Evaluated replenishment from {rows_processed} inventory row(s): {evaluated} evaluated, "
        f"{skipped_no_threshold} skipped (no threshold), {replenishment_required} requiring "
        f"replenishment, {healthy} healthy, {excluded_cwh_rows} Ahmedabad CWH row(s) excluded"
    )

    return {
        "rows_processed": rows_processed,
        "evaluated": evaluated,
        "skipped_no_threshold": skipped_no_threshold,
        "replenishment_required": replenishment_required,
        "healthy": healthy,
        "excluded_cwh_rows": excluded_cwh_rows,
    }


def get_replenishment_required() -> list[dict]:
    """Only products currently requiring replenishment, shaped for the
    Replenishment page's table.

    deficit_display is the Stock Deficit / Replenishment Quantity, rounded
    UP to the nearest multiple of packing (never down -- see
    app.threshold_service.format_deficit_display) and formatted per the
    current Threshold Display Mode, same toggle the Thresholds page's own
    threshold_display already uses. The underlying stored stock_deficit
    value (and the replenishment decision itself) is unchanged -- this is
    a display-only computation, done fresh on every read, never stored.

    Packing used for that rounding (Milestone 51 fix) is read LIVE from
    the current InventoryThreshold record via get_thresholds_lookup() --
    the exact same lookup previous_month_sales already used -- NOT from
    this row's own stored `packing` column. InventoryReplenishment.packing
    is only a snapshot, copied once by evaluate_replenishment() at the
    moment it last ran; it goes stale the instant a later Sales Report
    reprocessing corrects InventoryThreshold.packing (e.g. a packing
    parser fix) without a matching new Inventory Report upload to refresh
    this table's own copy. Before this fix, the Thresholds page (always
    live) and the Replenishment page (frozen at last evaluation) could
    disagree on the same item's packing for exactly that reason -- not
    because of a second parser or a bypassed code path, but because this
    one field was accidentally read from a stale copy while
    previous_month_sales, right next to it, was already read fresh. Falls
    back to the row's own stored `packing` only if no matching threshold
    record exists at all (mirrors previous_month_sales' identical
    fallback below).

    previous_month_sales/previous_month_sales_display come from the SAME
    Previous Month Sales dataset threshold generation itself reads (see
    app.threshold_service.get_thresholds_lookup) -- looked up by the exact
    same (cfa_key, item_key) pair evaluate_replenishment() used to find
    this row's threshold in the first place, so it's always the same
    number that actually drove this row's threshold, never re-derived or
    estimated. 0/"0" if no matching threshold entry is found (should not
    normally happen for a row that made it into this table at all, since
    evaluate_replenishment() itself requires a matching threshold to
    create the row -- see that function's skipped_no_threshold handling).

    threshold_display and status are deliberately no longer returned here
    -- the Replenishment page dropped both columns as no longer needed;
    status is still the row's own filter (STATUS_REPLENISHMENT_REQUIRED),
    just not surfaced as a display column anymore.

    cwh_shortage (Milestone 46): True when this row's Required Replenishment
    Quantity (the same pack-rounded deficit shown in deficit_display, as a
    number -- see app.threshold_service.round_up_to_pack) exceeds the
    Current Physical Stock of the SAME item_key at Ahmedabad CWH (see
    app.cwh_service.get_cwh_stock_lookup, a lean read of the real closing
    stock CWH rows already carry from the Current Inventory upload -- not
    CWH's own surplus/deficit/threshold, which are never touched here).
    Every row is compared only against its own item_key's CWH stock; an
    item_key with no CWH stock at all is treated as 0 physical stock
    available (a real shortage, not skipped). This flag is purely a visual
    availability warning for the UI to highlight -- it does not change
    stock_deficit, deficit_display, status, or the replenishment decision
    itself in any way.
    """
    thresholds = get_thresholds_lookup()
    cwh_stock_lookup = get_cwh_stock_lookup()

    session = get_config_session()
    try:
        rows = (
            session.query(InventoryReplenishment)
            .filter_by(status=STATUS_REPLENISHMENT_REQUIRED)
            .order_by(InventoryReplenishment.branch_location, InventoryReplenishment.item_name)
            .all()
        )
        results = []
        for row in rows:
            cfa_key = normalize_branch_match_key(row.branch_location)
            threshold_record = thresholds.get((cfa_key, row.item_key))
            previous_month_sales = threshold_record["previous_month_sales"] if threshold_record else 0.0
            # Milestone 51: packing must be read live from the SAME
            # InventoryThreshold record previous_month_sales already comes
            # from, not from this row's own stored `packing` column --
            # that column is a snapshot copied once, at the moment
            # evaluate_replenishment() last ran (see that function), and
            # goes stale the instant a later Sales Report reprocessing
            # corrects InventoryThreshold.packing (e.g. the Milestone 50
            # parser fix) without a matching new Inventory Report upload
            # to refresh this table's own copy. Falls back to the row's
            # own stored value only if no matching threshold exists at all
            # (should not normally happen -- see get_replenishment_required's
            # docstring on previous_month_sales' identical fallback).
            packing = threshold_record["packing"] if threshold_record else row.packing

            required_quantity = round_up_to_pack(row.stock_deficit, packing)
            cwh_physical_stock = cwh_stock_lookup.get(row.item_key, 0.0)
            cwh_shortage = cwh_physical_stock < required_quantity

            results.append(
                {
                    "branch": row.branch_location,
                    "division": row.division or "",
                    "item_code": row.item_code or "",
                    "item_name": row.item_name,
                    "closing_stock": row.closing_stock,
                    "transit_stock": row.transit_stock,
                    "effective_stock": row.effective_available_stock,
                    "previous_month_sales": previous_month_sales,
                    "previous_month_sales_display": _format_quantity(previous_month_sales),
                    "stock_deficit": row.stock_deficit,
                    "deficit_display": format_deficit_display(row.stock_deficit, packing),
                    "status": row.status,
                    "cwh_physical_stock": cwh_physical_stock,
                    "cwh_shortage": cwh_shortage,
                }
            )
        return results
    finally:
        session.close()


def get_replenishment_summary() -> dict:
    """{total_evaluated, requiring_replenishment, healthy} against the
    full persisted inventory_replenishment table -- reflects the latest
    known state per (branch, item), not just the most recent upload."""
    session = get_config_session()
    try:
        total_evaluated = session.query(InventoryReplenishment).count()
        requiring_replenishment = (
            session.query(InventoryReplenishment).filter_by(status=STATUS_REPLENISHMENT_REQUIRED).count()
        )
        healthy = session.query(InventoryReplenishment).filter_by(status=STATUS_HEALTHY).count()
        return {
            "total_evaluated": total_evaluated,
            "requiring_replenishment": requiring_replenishment,
            "healthy": healthy,
        }
    finally:
        session.close()
