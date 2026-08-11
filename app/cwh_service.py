"""Ahmedabad CWH (Central Warehouse) analysis -- Phase 2 of the Inventory
upgrade, revised in Phase 3 to (a) read Current Stock directly from the
Ahmedabad CWH rows of the Current Inventory upload (Issue 1) and (b)
snapshot the CWH Threshold calculation at processing-run time using a
configurable multiplier, rather than recomputing it live (Issue 2). Phase
1 (see app/threshold_service.is_ahmedabad_cwh_branch) excluded Ahmedabad
CWH entirely from CFA-level threshold and replenishment calculations,
since it is the parent warehouse feeding every CFA, not a CFA itself --
that exclusion is unchanged by this module.

`evaluate_cwh_stock()` is the ONE "CWH processing run" -- called from the
same Inventory Report upload that feeds InventoryReplenishment (see
ui/inventory_upload_page.py). Each run:

1. Extracts Current Stock directly from the Ahmedabad CWH rows of THIS
   upload (the exact rows app/replenishment_service.py's
   evaluate_replenishment() excludes) -- never derived or estimated.
2. Re-sums Total Previous Month Sales (every real CFA combined) fresh
   from InventoryThreshold, for EVERY item currently known from any Sales
   Report -- not just items present in this particular CWH upload -- so
   an item with CFA demand but no CWH stock yet still gets evaluated.
3. Reads the CWH Threshold Multiplier ONCE, at this run's start (see
   app/inventory_parameters_service.get_cwh_threshold_multiplier()), and
   uses that single value for every item in this run.
4. Computes and STORES cwh_threshold/surplus_deficit/status into
   CwhStock, per item -- see database/models.py's CwhStock docstring for
   why this is stored rather than computed live: changing the multiplier
   afterward must never retroactively alter an already-generated
   snapshot, only the NEXT processing run.

`get_cwh_overview()` (the page's read side) does nothing but read
CwhStock back -- no calculation happens there anymore.

Does NOT touch InventoryThreshold or InventoryReplenishment, and does
NOT change generate_thresholds_from_sales() or evaluate_replenishment()
-- both continue behaving exactly as before this module was added.
"""

from datetime import datetime

import pandas as pd
from loguru import logger

from app.inventory_parameters_service import get_cwh_threshold_multiplier
from app.threshold_service import is_ahmedabad_cwh_branch, normalize_match_key
from database.connection import get_config_session
from database.models import CwhStock, InventoryThreshold

STATUS_HEALTHY = "Healthy"
STATUS_SHORTAGE = "Shortage"


def _format_number(value: float) -> str:
    """'180' for a whole number, '-45' / '0' otherwise -- same convention
    as app/threshold_service._format_quantity, but written locally since
    that helper is private to its own module and this needs to format
    negative surplus/deficit values too (thresholds are never negative,
    surplus/deficit routinely is)."""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def _cwh_status(physical_stock: float, cwh_threshold: float) -> tuple[str, str]:
    """(status_text, badge_kind) for one item -- two-level only (Healthy /
    Shortage), no intermediate "low stock" tier. No demand at all
    (cwh_threshold <= 0, i.e. no CFA sold this item last month) is always
    Healthy regardless of stock -- there is nothing to fall short of."""
    if cwh_threshold <= 0 or physical_stock >= cwh_threshold:
        return STATUS_HEALTHY, "success"
    return STATUS_SHORTAGE, "error"


def evaluate_cwh_stock(df: pd.DataFrame) -> dict:
    """The one CWH "processing run" -- called from the same Inventory
    Report upload that feeds app.replenishment_service.evaluate_replenishment
    (same validated DataFrame: BranchLocation, Item Code, Item Name,
    TotalQty, Transit Stock).

    Current Stock (closing_stock) is read DIRECTLY from this upload's
    Ahmedabad CWH rows (see is_ahmedabad_cwh_branch) -- the mirror image
    of evaluate_replenishment(), which keeps every OTHER row. Grouped by
    item_key (not branch -- there is exactly one CWH), summing
    TotalQty/Transit Stock per group in case the same item appears across
    multiple raw CWH spellings/rows in one upload.

    Full replacement: an Inventory Report upload is a complete monthly
    snapshot, not an incremental delta -- every existing CwhStock row is
    deleted before this run rebuilds the table. An item still carrying
    CFA demand (present in InventoryThreshold) but NOT mentioned at CWH
    in this particular upload still gets a row -- 0 physical stock is a
    real, current shortage, not something to skip -- but its stock is
    reset to 0 rather than carrying forward a previous run's now-stale
    figure. An item with neither CFA demand nor CWH stock in this upload
    at all (discontinued, or simply absent from both reports) gets no row.

    total_previous_month_sales/cwh_threshold/surplus_deficit/status are
    always computed fresh from the CURRENT InventoryThreshold table and
    the CWH Threshold Multiplier's CURRENT value (read once, at the top
    of this run). A later change to that multiplier has zero effect on
    this run's stored results; it only takes effect the NEXT time this
    function runs.

    Returns: {rows_processed, cwh_rows_found, unique_items_in_upload,
    total_items_evaluated, created, multiplier_used}
    """
    rows_processed = len(df)

    working = df.copy()
    working["BranchLocation"] = working["BranchLocation"].astype(str).str.strip()
    working["Item Name"] = working["Item Name"].astype(str).str.strip()

    cwh_mask = working["BranchLocation"].map(is_ahmedabad_cwh_branch)
    cwh_working = working[cwh_mask]
    cwh_rows_found = len(cwh_working)

    stock_by_item: dict[str, dict] = {}
    if cwh_rows_found:
        cwh_working = cwh_working.copy()
        cwh_working["TotalQty"] = pd.to_numeric(cwh_working["TotalQty"], errors="coerce").fillna(0)
        cwh_working["Transit Stock"] = pd.to_numeric(cwh_working["Transit Stock"], errors="coerce").fillna(0)
        cwh_working["_item_key"] = cwh_working["Item Name"].map(normalize_match_key)

        grouped_stock = cwh_working.groupby("_item_key", as_index=False).agg(
            item_name=("Item Name", "first"),
            item_code=("Item Code", "first"),
            closing_stock=("TotalQty", "sum"),
            transit_stock=("Transit Stock", "sum"),
        )
        for _, row in grouped_stock.iterrows():
            item_code = row["item_code"]
            item_code = str(item_code).strip() if item_code is not None and str(item_code).strip() != "" else None
            stock_by_item[row["_item_key"]] = {
                "item_name": row["item_name"],
                "item_code": item_code,
                "closing_stock": float(row["closing_stock"]),
                "transit_stock": float(row["transit_stock"]),
            }

    multiplier = get_cwh_threshold_multiplier()

    session = get_config_session()
    try:
        threshold_rows = session.query(InventoryThreshold).all()
        sales_by_item: dict[str, float] = {}
        item_name_by_item: dict[str, str] = {}
        for row in threshold_rows:
            sales_by_item[row.item_key] = sales_by_item.get(row.item_key, 0.0) + row.previous_month_sales
            item_name_by_item.setdefault(row.item_key, row.item_name)

        # Full replacement: every item still known (fresh CWH stock this
        # upload, or current CFA demand from InventoryThreshold) gets a
        # rebuilt row; an item present in neither gets none. Deliberately
        # does NOT union in the previously-stored CwhStock keys -- that
        # was what let a discontinued item's row linger forever.
        all_item_keys = set(stock_by_item) | set(sales_by_item)

        deleted = session.query(CwhStock).delete()
        if deleted:
            logger.info(f"Cleared {deleted} existing CWH stock row(s) before rebuilding from this upload")

        created = 0
        now = datetime.now()

        for item_key in all_item_keys:
            new_stock = stock_by_item.get(item_key)

            if new_stock is not None:
                item_name = new_stock["item_name"]
                item_code = new_stock["item_code"]
                closing_stock = new_stock["closing_stock"]
                transit_stock = new_stock["transit_stock"]
            else:
                # Not in this upload's CWH rows -- current CFA demand
                # alone doesn't tell us a stock figure, so it's reset to
                # 0 rather than carrying forward a prior run's snapshot.
                item_name = item_name_by_item.get(item_key, item_key)
                item_code = None
                closing_stock = 0.0
                transit_stock = 0.0

            total_previous_month_sales = sales_by_item.get(item_key, 0.0)
            cwh_threshold = total_previous_month_sales * multiplier
            surplus_deficit = closing_stock - cwh_threshold
            status, _ = _cwh_status(closing_stock, cwh_threshold)

            session.add(
                CwhStock(
                    item_name=item_name,
                    item_key=item_key,
                    item_code=item_code,
                    closing_stock=closing_stock,
                    transit_stock=transit_stock,
                    total_previous_month_sales=total_previous_month_sales,
                    cwh_threshold=cwh_threshold,
                    surplus_deficit=surplus_deficit,
                    status=status,
                    last_updated=now,
                )
            )
            created += 1
        session.commit()
    finally:
        session.close()

    unique_items_in_upload = len(stock_by_item)
    total_items_evaluated = len(all_item_keys)
    logger.info(
        f"evaluate_cwh_stock: {cwh_rows_found} Ahmedabad CWH row(s) in this upload -> "
        f"{unique_items_in_upload} unique item(s) with fresh stock; {total_items_evaluated} item(s) "
        f"total evaluated using multiplier={multiplier}; {created} created"
    )

    return {
        "rows_processed": rows_processed,
        "cwh_rows_found": cwh_rows_found,
        "unique_items_in_upload": unique_items_in_upload,
        "total_items_evaluated": total_items_evaluated,
        "created": created,
        "multiplier_used": multiplier,
    }


def get_cwh_overview() -> list[dict]:
    """One row per SKU for the Central Warehouse page -- a pure read of
    CwhStock (see database/models.py's docstring): every value shown was
    computed and stored by the most recent evaluate_cwh_stock() run, NOT
    recalculated here. Division is the one exception -- purely a
    display/filter attribute, not part of the threshold calculation, so
    it's still joined live from InventoryThreshold for convenience (it
    changes with the CFA/division mapping, never with the multiplier).
    """
    session = get_config_session()
    try:
        cwh_rows = session.query(CwhStock).all()
        threshold_rows = session.query(InventoryThreshold).all()
    finally:
        session.close()

    division_by_item: dict[str, str] = {}
    for row in threshold_rows:
        if row.division and row.item_key not in division_by_item:
            division_by_item[row.item_key] = row.division

    status_kind_by_status = {STATUS_HEALTHY: "success", STATUS_SHORTAGE: "error"}

    results = []
    for row in cwh_rows:
        results.append(
            {
                "item_name": row.item_name,
                "item_key": row.item_key,
                "item_code": row.item_code or "",
                "division": division_by_item.get(row.item_key, ""),
                "physical_stock": row.closing_stock,
                "physical_stock_display": _format_number(row.closing_stock),
                "transit_stock": row.transit_stock,
                "total_previous_month_sales": row.total_previous_month_sales,
                "total_previous_month_sales_display": _format_number(row.total_previous_month_sales),
                "cwh_threshold": row.cwh_threshold,
                "cwh_threshold_display": _format_number(row.cwh_threshold),
                "surplus_deficit": row.surplus_deficit,
                "surplus_deficit_display": _format_number(row.surplus_deficit),
                "status": row.status,
                "status_kind": status_kind_by_status.get(row.status, "neutral"),
            }
        )

    results.sort(key=lambda r: r["item_name"])
    return results


def get_cwh_stock_lookup() -> dict[str, float]:
    """{item_key: physical_stock (closing_stock)} for every item currently
    known at Ahmedabad CWH -- a lean read of CwhStock for callers that
    only need the physical-stock NUMBER itself, not the full per-item
    overview get_cwh_overview() builds (division, threshold, status,
    etc.). Added for app.replenishment_service's CWH-availability
    shortage check (Milestone 46) -- read-only; does not touch
    evaluate_cwh_stock(), get_cwh_overview(), get_cwh_summary(), or any
    CWH surplus/deficit/threshold calculation."""
    session = get_config_session()
    try:
        rows = session.query(CwhStock).all()
        return {row.item_key: row.closing_stock for row in rows}
    finally:
        session.close()


def get_cwh_summary() -> dict:
    """{total_skus, healthy, shortage, has_any_data} -- used by the
    Central Warehouse page to decide its empty-state message (no
    Inventory Report processed yet vs. genuinely zero matching rows)."""
    rows = get_cwh_overview()
    counts = {STATUS_HEALTHY: 0, STATUS_SHORTAGE: 0}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    return {
        "total_skus": len(rows),
        "healthy": counts[STATUS_HEALTHY],
        "shortage": counts[STATUS_SHORTAGE],
        "has_any_data": len(rows) > 0,
    }
