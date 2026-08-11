"""Cloud sync for Inventory Monitoring's computed state --
inventory_thresholds and inventory_replenishment. Version 2.0, Inventory
cloud migration, Milestone 23, unified onto the app-wide Last-Modified-Wins
rule in Milestone 35 (see app/sync_service.reconcile_rows()).

(branch_key, item_key) is the natural key locally (see
database/models.py's UniqueConstraint) and the Postgres primary key --
both tables already have a reliable local "last modified" timestamp
(`last_updated`, bumped on every upsert by app/threshold_service.py and
app/replenishment_service.py), so no cloud_id or extra bookkeeping columns
are needed. sync_thresholds()/sync_replenishment() compare each local row
against its cloud counterpart by last_updated and push or pull whichever
side is newer, per row -- replacing the old "always push the full local
table" / "always pull the full cloud table" behavior, which had no actual
conflict resolution (whichever action ran most recently silently won,
even if it was stale).

Inventory has no Developer Mode concept of its own (see
database/models.py's InventoryThreshold/InventoryReplenishment
docstrings) -- unlike Path Validator's sync modules, there is no
is_developer_mode() guard here; Inventory data is always "the" data,
regardless of mode.
"""

from datetime import datetime

from loguru import logger

from app.sync_service import delete_rows, push_rows, reconcile_rows
from database.connection import get_config_session
from database.models import InventoryReplenishment, InventoryThreshold

THRESHOLDS_TABLE = "inventory_thresholds"
REPLENISHMENT_TABLE = "inventory_replenishment"
ON_CONFLICT = "branch_key,item_key"

_THRESHOLD_COLUMNS = (
    "branch_key",
    "item_key",
    "branch_location",
    "division",
    "item_name",
    "packing",
    "previous_month_sales",
    "raw_threshold",
    "packed_threshold",
)
_REPLENISHMENT_COLUMNS = (
    "branch_key",
    "item_key",
    "branch_location",
    "division",
    "item_code",
    "item_name",
    "packing",
    "closing_stock",
    "transit_stock",
    "effective_available_stock",
    "raw_threshold",
    "packed_threshold",
    "stock_deficit",
    "status",
)
_KEY_COLUMNS = ("branch_key", "item_key")

# The cloud column these two tables actually store their "last modified"
# timestamp in -- deliberately NOT "updated_at" (Supabase maintains its own
# column by that name on these tables, and comparing against it instead of
# ours is exactly the defect Milestone 53 fixed; see _sync_table).
_UPDATED_AT_COLUMN = "last_updated"


def _parse_ts(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _serialize(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _sync_table(model, columns: tuple, table: str) -> bool:
    """The shared Last-Modified-Wins reconciliation body for both
    Inventory tables -- identical shape, only the model/columns/table
    differ, so this is written once and called twice below rather than
    duplicated."""
    session = get_config_session()
    try:
        rows = session.query(model).all()
        local_rows = []
        for row in rows:
            item = {column: _serialize(getattr(row, column)) for column in columns}
            # Keyed under the cloud column's REAL name, not "updated_at".
            # These two tables store their timestamp in a column called
            # `last_updated` (mirroring the local model); the cloud rows
            # ALSO carry a separate, Supabase-managed `updated_at`. Naming
            # the local key "updated_at" and asking reconcile_rows() to
            # compare on that made it compare our local last_updated against
            # Supabase's own server-side updated_at -- a UTC "just now"
            # stamp that is newer than any local time, on every row, forever.
            # Every matched row was therefore pulled and the local copy
            # overwritten, no matter how recently it had been corrected
            # locally, which is what silently reverted corrected Inventory
            # packing values back to a stale snapshot (Milestone 53).
            item[_UPDATED_AT_COLUMN] = row.last_updated
            local_rows.append(item)
    finally:
        session.close()

    plan = reconcile_rows(
        table, local_rows, key_columns=list(_KEY_COLUMNS), updated_at_column=_UPDATED_AT_COLUMN
    )
    if not plan.success:
        logger.warning(f"Sync: failed to reconcile table={table!r}: {plan.error_message}")
        return False

    changed = False

    if plan.to_push:
        # No column renaming needed any more -- the local rows already carry
        # the timestamp under the cloud column's own name; it only needs
        # serializing from datetime to an ISO string for transport.
        payload = []
        for row in plan.to_push:
            item = dict(row)
            item[_UPDATED_AT_COLUMN] = _serialize(row[_UPDATED_AT_COLUMN])
            payload.append(item)
        result = push_rows(table, payload, on_conflict=ON_CONFLICT)
        if not result.success:
            logger.warning(f"Sync: failed to push {len(payload)} row(s) to table={table!r}: {result.error_message}")
        else:
            logger.info(f"Sync: pushed {len(payload)} row(s) to table={table!r}")

    if plan.to_pull:
        session = get_config_session()
        try:
            for cloud_row in plan.to_pull:
                existing = (
                    session.query(model)
                    .filter_by(branch_key=cloud_row["branch_key"], item_key=cloud_row["item_key"])
                    .first()
                )
                if existing is None:
                    existing = model(branch_key=cloud_row["branch_key"], item_key=cloud_row["item_key"])
                    session.add(existing)
                for column in columns:
                    if column in _KEY_COLUMNS:
                        continue
                    setattr(existing, column, cloud_row.get(column))
                existing.last_updated = _parse_ts(cloud_row.get("last_updated")) or datetime.now()
            session.commit()
        finally:
            session.close()
        logger.info(f"Sync: pulled {len(plan.to_pull)} row(s) from table={table!r}")
        changed = True

    return changed


def sync_thresholds() -> bool:
    """Last-Modified-Wins reconciliation for every InventoryThreshold row,
    keyed by (branch_key, item_key). The one function both the module-wide
    Refresh action and the immediate sync right after a Sales Report
    upload call -- no separate push-only/pull-only pair."""
    return _sync_table(InventoryThreshold, _THRESHOLD_COLUMNS, THRESHOLDS_TABLE)


def sync_replenishment() -> bool:
    """Last-Modified-Wins reconciliation for every InventoryReplenishment
    row, keyed by (branch_key, item_key). Mirrors sync_thresholds()
    exactly."""
    return _sync_table(InventoryReplenishment, _REPLENISHMENT_COLUMNS, REPLENISHMENT_TABLE)


def _push_table_full_replace(model, columns: tuple, table: str) -> bool:
    """Clears `table` in the cloud, then pushes every current local row --
    for a caller that just did a local full-table delete+rebuild (see
    app/threshold_service.generate_thresholds_from_sales() and
    app/replenishment_service.evaluate_replenishment()) and needs the
    cloud to end up an exact mirror of that fresh local state.

    Deliberately NOT `_sync_table`'s Last-Modified-Wins reconcile: that
    rule has no concept of deletion (see app/sync_service.reconcile_rows'
    own docstring) -- a row just deleted locally has no local counterpart
    on the next reconcile and gets PULLED BACK from the cloud if a cloud
    copy still exists there, silently re-introducing the exact
    stale-row-accumulation bug the local full-replace fix exists to
    remove. Deleting the cloud table before pushing (the documented
    `app.sync_service.delete_rows()` pattern for "local behavior is
    delete-all + rebuild/reinsert") avoids that round-trip entirely."""
    session = get_config_session()
    try:
        rows = session.query(model).all()
        payload = []
        for row in rows:
            item = {column: _serialize(getattr(row, column)) for column in columns}
            item[_UPDATED_AT_COLUMN] = _serialize(row.last_updated)
            payload.append(item)
    finally:
        session.close()

    delete_result = delete_rows(table)
    if not delete_result.success:
        logger.warning(f"Sync: failed to clear table={table!r} before full-replace push: {delete_result.error_message}")
        return False

    result = push_rows(table, payload, on_conflict=ON_CONFLICT)
    if not result.success:
        logger.warning(f"Sync: failed to push {len(payload)} row(s) to table={table!r}: {result.error_message}")
        return False

    logger.info(f"Sync: full-replace pushed {len(payload)} row(s) to table={table!r}")
    return True


def push_thresholds_full_replace() -> bool:
    """Full-replace cloud push for InventoryThreshold, called right after
    a Previous Month Sales Report upload (see ui/sales_upload_page.py) --
    that upload just replaced the entire local table with a fresh
    snapshot, so the cloud must be cleared and re-pushed to match, not
    reconciled row-by-row (see _push_table_full_replace). The module-wide
    Refresh button continues to use sync_thresholds()'s ordinary
    Last-Modified-Wins reconciliation -- unrelated to a fresh upload just
    having happened, so it is left unchanged."""
    return _push_table_full_replace(InventoryThreshold, _THRESHOLD_COLUMNS, THRESHOLDS_TABLE)


def push_replenishment_full_replace() -> bool:
    """Full-replace cloud push for InventoryReplenishment, called right
    after an Inventory Report upload (see ui/inventory_upload_page.py).
    Mirrors push_thresholds_full_replace() exactly."""
    return _push_table_full_replace(InventoryReplenishment, _REPLENISHMENT_COLUMNS, REPLENISHMENT_TABLE)
