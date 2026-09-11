"""Inventory Monitoring upload state: physical file retention for the two
sync slots (sales_report, inventory_report) + the recompute-then-evaluate
pipeline both the local upload path and the sync pull-apply path
(app/inventory_sync_service.py) share.

Mirrors app/review_upload_service.py's shape (one physical file per slot,
named by slot_id; one database.models.InventoryUploadSlot row) with one
deliberate difference: an invalid upload is never stored here at all --
Inventory's existing single-machine behavior already rejects an invalid
file outright with nothing retained (see ui/inventory_upload_page.py's
existing dialogs), and this sync slice doesn't change that.

Recompute rule (see docs/INVENTORY_SYNC_CONTEXT.md): thresholds are never a
standing cached value between Inventory Report actions. Every time an
Inventory Report is processed -- local upload OR sync pull -- thresholds
are regenerated fresh from whichever Sales Report is currently retained on
THIS machine, via recompute_thresholds_from_retained_sales(), before
evaluate_replenishment()/evaluate_cwh_stock() run. If no Sales Report is
currently retained, recompute is a no-op (matches today's existing
single-machine behavior unchanged -- see that report's §3: this produces a
silent zero-evaluated result, not an error). Blocking that specific case is
the sync PULL path's job (app/inventory_sync_service.py's gate), not this
module's.
"""

import shutil
from pathlib import Path

from loguru import logger

from app.cwh_service import evaluate_cwh_stock
from app.excel_validation import load_sales_report, validate_inventory_report
from app.replenishment_service import evaluate_replenishment
from app.threshold_service import generate_thresholds_from_sales
from app.config import INVENTORY_UPLOADS_DIR
from database.connection import get_session, utcnow
from database.models import InventoryUploadSlot

SALES_REPORT_SLOT = "sales_report"
INVENTORY_REPORT_SLOT = "inventory_report"
# Order matters: sales_report must always be applied/considered before
# inventory_report, since inventory_report's own processing recomputes
# thresholds from whichever Sales Report is currently retained -- see
# app/inventory_sync_service.py's apply_pending_updates, which orders
# against this tuple rather than trusting caller-supplied order.
INVENTORY_SLOTS = (SALES_REPORT_SLOT, INVENTORY_REPORT_SLOT)

SLOT_LABELS = {SALES_REPORT_SLOT: "Sales Report", INVENTORY_REPORT_SLOT: "Inventory Report"}


# --- Local retention -----------------------------------------------------

def _stored_path_for(slot_id: str, source_extension: str) -> Path:
    return INVENTORY_UPLOADS_DIR / f"{slot_id}{source_extension}"


def _clear_stale_copies(slot_id: str) -> None:
    """Remove any previously stored file for this slot under a DIFFERENT
    extension than the new upload -- same reasoning as
    app/review_upload_service.py's own _clear_stale_copies."""
    for existing in INVENTORY_UPLOADS_DIR.glob(f"{slot_id}.*"):
        try:
            existing.unlink()
        except OSError as exc:
            logger.warning(f"Inventory upload '{slot_id}': could not remove stale copy {existing}: {exc}")


def store_inventory_upload(slot_id: str, source_path: str) -> Path:
    """Copy `source_path` into this slot's own retained storage. Caller's
    responsibility to have already confirmed the file is valid -- this
    function does not validate, it only retains (see module docstring on
    why an invalid file is never stored)."""
    extension = Path(source_path).suffix.lower()
    _clear_stale_copies(slot_id)
    stored_path = _stored_path_for(slot_id, extension)
    shutil.copyfile(source_path, stored_path)

    session = get_session()
    try:
        row = session.query(InventoryUploadSlot).filter_by(slot_id=slot_id).first()
        if row is None:
            row = InventoryUploadSlot(slot_id=slot_id)
            session.add(row)
        row.filename = Path(source_path).name
        row.file_path = str(stored_path)
        row.uploaded_at = utcnow()
        # Set True the moment a local upload is stored, False the moment a
        # sync push succeeds -- same rule as ReviewFileSlot.only_on_this_machine
        # (see database/models.py), flipped back to False by
        # app.inventory_sync_service.upload_and_sync() only after the
        # manifest insert actually succeeds.
        row.only_on_this_machine = True
        session.commit()
    finally:
        session.close()

    logger.info(f"Inventory upload '{slot_id}': retained '{Path(source_path).name}' at {stored_path}")
    return stored_path


def _empty_state(slot_id: str) -> dict:
    return {
        "slot_id": slot_id,
        "uploaded": False,
        "filename": None,
        "file_path": None,
        "uploaded_at": None,
        "uploaded_by": None,
        "only_on_this_machine": False,
        "thresholds_generated_at": None,
    }


def _row_to_state(row: InventoryUploadSlot) -> dict:
    return {
        "slot_id": row.slot_id,
        "uploaded": bool(row.file_path),
        "filename": row.filename,
        "file_path": row.file_path,
        "uploaded_at": row.uploaded_at,
        "uploaded_by": row.uploaded_by,
        "only_on_this_machine": bool(row.only_on_this_machine),
        "thresholds_generated_at": row.thresholds_generated_at,
    }


def remove_inventory_file(slot_id: str) -> None:
    """Remove the retained file + slot state for `slot_id` -- LOCAL ONLY,
    byte-for-byte the same behavior as app.review_upload_service.
    remove_review_file: does not touch sync_manifest or Storage (no
    manifest/Storage history is ever deleted, per docs/SYNC_DESIGN.md's
    append-only invariant), and does not cascade into InventoryThreshold/
    InventoryReplenishment/CwhStock -- removing a slot only empties this
    machine's own display of it, so a new file can be uploaded or pulled
    into it. If the removed slot was sales_report, the next Inventory
    Report action's recompute (see recompute_thresholds_from_retained_sales)
    simply finds nothing retained and no-ops, exactly as it already does
    when nothing was ever uploaded."""
    session = get_session()
    try:
        row = session.query(InventoryUploadSlot).filter_by(slot_id=slot_id).first()
        if row is not None:
            session.delete(row)
            session.commit()
    finally:
        session.close()

    _clear_stale_copies(slot_id)
    logger.info(f"Inventory upload '{slot_id}': file removed")


def get_slot_state(slot_id: str) -> dict:
    session = get_session()
    try:
        row = session.query(InventoryUploadSlot).filter_by(slot_id=slot_id).first()
        return _row_to_state(row) if row else _empty_state(slot_id)
    finally:
        session.close()


def get_all_slot_states() -> dict:
    """{slot_id: state_dict} for both Inventory sync slots -- including a
    slot with no row yet (never uploaded/pulled)."""
    session = get_session()
    try:
        rows = {row.slot_id: row for row in session.query(InventoryUploadSlot).all()}
    finally:
        session.close()
    return {
        slot_id: (_row_to_state(rows[slot_id]) if slot_id in rows else _empty_state(slot_id))
        for slot_id in INVENTORY_SLOTS
    }


def has_sales_report_retained() -> bool:
    """True if a Sales Report file currently exists in local retention --
    the recompute rule's actual input, and the sync pull gate's own check
    (app/inventory_sync_service.py). Deliberately checks the RETAINED
    file's presence, not "has this machine ever pulled one" specifically --
    a machine that uploaded its own Sales Report locally satisfies this
    exactly the same way a machine that pulled one does; both leave the
    same retained file behind, which is all recompute actually needs."""
    state = get_slot_state(SALES_REPORT_SLOT)
    return bool(state["file_path"] and Path(state["file_path"]).exists())


def _mark_thresholds_generated() -> None:
    session = get_session()
    try:
        row = session.query(InventoryUploadSlot).filter_by(slot_id=SALES_REPORT_SLOT).first()
        if row is None:
            row = InventoryUploadSlot(slot_id=SALES_REPORT_SLOT)
            session.add(row)
        row.thresholds_generated_at = utcnow()
        session.commit()
    finally:
        session.close()


# --- Recompute-then-evaluate pipeline -------------------------------------

def recompute_thresholds_from_retained_sales() -> dict | None:
    """Re-parses the CURRENTLY RETAINED Sales Report file (if any) and
    regenerates every InventoryThreshold from it fresh. Returns None (no-op)
    if no Sales Report is currently retained, or if the retained file can no
    longer be validated -- either way, InventoryThreshold is left exactly as
    it already was; this never clears thresholds just because the retained
    file is temporarily unreadable."""
    state = get_slot_state(SALES_REPORT_SLOT)
    file_path = state["file_path"]
    if not file_path or not Path(file_path).exists():
        return None

    load_result = load_sales_report(file_path)
    if not load_result["success"] or load_result["error"] is not None:
        logger.warning(
            f"Inventory recompute: retained Sales Report at '{file_path}' failed to re-validate "
            f"-- thresholds left unchanged. error={load_result['error']!r}, "
            f"missing_columns={load_result['missing_columns']}"
        )
        return None

    stats = generate_thresholds_from_sales(load_result["df"])
    _mark_thresholds_generated()
    return stats


def apply_sales_report(path: str) -> dict:
    """Load+validate the Sales Report at `path`, then regenerate every
    InventoryThreshold from it. Returns the load_sales_report() dict plus
    `threshold_stats` (None if load failed)."""
    load_result = load_sales_report(path)
    if not load_result["success"] or load_result["error"] is not None:
        return {**load_result, "threshold_stats": None}
    stats = generate_thresholds_from_sales(load_result["df"])
    _mark_thresholds_generated()
    return {**load_result, "threshold_stats": stats}


def apply_inventory_report(path: str) -> dict:
    """Load+validate the Inventory Report at `path`, recompute thresholds
    fresh from whichever Sales Report is currently retained (see
    recompute_thresholds_from_retained_sales -- a no-op, not an error, if
    none is retained), THEN evaluate_replenishment() and
    evaluate_cwh_stock(), in that order. Returns the validate_inventory_report()
    dict plus `replenishment_stats`/`cwh_stats` (both None if load failed).

    evaluate_cwh_stock() is guarded exactly like the previous UI-layer code
    (ui/inventory_upload_page.py, pre-sync) guarded it: additive only, a
    bug there must never take down the replenishment result the caller is
    waiting on."""
    load_result = validate_inventory_report(path)
    if not load_result["success"] or load_result["error"] is not None:
        return {**load_result, "replenishment_stats": None, "cwh_stats": None}

    recompute_thresholds_from_retained_sales()

    replenishment_stats = evaluate_replenishment(load_result["df"])
    try:
        cwh_stats = evaluate_cwh_stock(load_result["df"])
    except Exception as exc:
        logger.error(f"Failed to evaluate Ahmedabad CWH stock: {exc}")
        cwh_stats = None
    return {**load_result, "replenishment_stats": replenishment_stats, "cwh_stats": cwh_stats}


PROCESS_FN = {SALES_REPORT_SLOT: apply_sales_report, INVENTORY_REPORT_SLOT: apply_inventory_report}
