"""Excess Inventory: the OPPOSITE side of app/replenishment_service.py's own
"falls short of threshold" filter. Reads the exact same InventoryReplenishment
table replenishment_service.evaluate_replenishment() already populates at
Inventory Report upload time -- no new upload step, no new evaluation pass,
no duplicated stock/threshold calculation. This module is a pure read +
derived-display layer, the same relationship app/cwh_service.py already has
to app/threshold_service.py.

Excess Quantity = Effective Available Stock - packed_threshold (always > 0
for a row this module returns -- see get_excess_inventory()'s own filter; a
row sitting exactly at threshold is not excess).
Excess % = Effective Available Stock / packed_threshold * 100.

Transfer Candidate: Effective Available Stock >= Transfer Candidate
Multiplier (app.inventory_parameters_service, configurable, default 2.0) x
Previous Month Sales. Purely a display flag for the UI's row highlight --
never stored, never affects excess_quantity/status/any InventoryReplenishment
column.
"""

from app.inventory_parameters_service import get_excess_transfer_candidate_multiplier
from app.threshold_service import format_threshold_display, get_thresholds_lookup, normalize_branch_match_key
from database.connection import get_config_session
from database.models import InventoryReplenishment

STATUS_TRANSFER_CANDIDATE = "Transfer Candidate"
STATUS_EXCESS = "Excess"

EXCESS_INVENTORY_REPORT_COLUMNS = (
    "division",
    "branch",
    "item_code",
    "item_name",
    "effective_stock",
    "previous_month_sales_display",
    "threshold_display",
    "excess_quantity_display",
    "excess_percent_display",
    "status",
)
EXCESS_INVENTORY_REPORT_HEADINGS = {
    "division": "Division",
    "branch": "CFA",
    "item_code": "SKU Code",
    "item_name": "SKU Name",
    "effective_stock": "Current Stock",
    "previous_month_sales_display": "Previous Month Sales",
    "threshold_display": "Threshold",
    "excess_quantity_display": "Excess Quantity",
    "excess_percent_display": "Excess %",
    "status": "Status",
}


def _format_quantity(value: float) -> str:
    """'150' for a whole number, '150.5' otherwise -- same convention as
    app/threshold_service._format_quantity / app/replenishment_service._format_quantity
    (kept as a small local copy rather than importing a private helper, same
    precedent those two modules already set)."""
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def _format_percent(value: float) -> str:
    return f"{value:.0f}%" if value == int(value) else f"{value:.1f}%"


def get_excess_inventory() -> list[dict]:
    """Every currently evaluated (branch, item) whose Effective Available
    Stock exceeds its own packed_threshold -- genuine excess, not merely
    "Healthy" (Healthy also includes stock sitting exactly at threshold,
    which has zero excess). Reads InventoryReplenishment fresh on every
    call, same convention as get_replenishment_required(); never re-runs
    evaluate_replenishment() or writes anything.

    previous_month_sales, packing, and packed_threshold are read LIVE from
    the current InventoryThreshold via get_thresholds_lookup() -- same
    freshness rationale as get_replenishment_required()'s identical
    pattern (see that function's own docstring): InventoryReplenishment's
    stored columns are a snapshot from whenever evaluate_replenishment()
    last ran, and can go stale if InventoryThreshold is corrected
    afterward without a matching new Inventory Report upload. Falls back
    to this row's own stored values only if no matching threshold record
    exists at all.

    transfer_candidate is read fresh against the current Transfer
    Candidate Multiplier on every call, never stored (see module
    docstring)."""
    thresholds = get_thresholds_lookup()
    multiplier = get_excess_transfer_candidate_multiplier()

    session = get_config_session()
    try:
        rows = (
            session.query(InventoryReplenishment)
            .order_by(InventoryReplenishment.branch_location, InventoryReplenishment.item_name)
            .all()
        )
        results = []
        for row in rows:
            cfa_key = normalize_branch_match_key(row.branch_location)
            threshold_record = thresholds.get((cfa_key, row.item_key))
            previous_month_sales = threshold_record["previous_month_sales"] if threshold_record else 0.0
            packing = threshold_record["packing"] if threshold_record else row.packing
            packed_threshold = threshold_record["packed_threshold"] if threshold_record else row.packed_threshold

            excess_quantity = row.effective_available_stock - packed_threshold
            if excess_quantity <= 0:
                continue

            excess_percent = (row.effective_available_stock / packed_threshold * 100) if packed_threshold > 0 else 0.0
            transfer_candidate = row.effective_available_stock >= multiplier * previous_month_sales

            results.append(
                {
                    "division": row.division or "",
                    "branch": row.branch_location,
                    "item_code": row.item_code or "",
                    "item_name": row.item_name,
                    "effective_stock": row.effective_available_stock,
                    "previous_month_sales": previous_month_sales,
                    "previous_month_sales_display": _format_quantity(previous_month_sales),
                    "threshold_display": format_threshold_display(packed_threshold, packing),
                    "excess_quantity": excess_quantity,
                    "excess_quantity_display": _format_quantity(excess_quantity),
                    "excess_percent": excess_percent,
                    "excess_percent_display": _format_percent(excess_percent),
                    "transfer_candidate": transfer_candidate,
                    "status": STATUS_TRANSFER_CANDIDATE if transfer_candidate else STATUS_EXCESS,
                }
            )
        return results
    finally:
        session.close()


def get_excess_inventory_summary() -> dict:
    """{total_excess, transfer_candidates} -- same {..._summary} shape
    convention as get_replenishment_summary()/get_cwh_summary()."""
    rows = get_excess_inventory()
    return {
        "total_excess": len(rows),
        "transfer_candidates": sum(1 for r in rows if r["transfer_candidate"]),
    }
