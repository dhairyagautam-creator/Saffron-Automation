"""Work Distribution upload retention -- Phase 1 of Work Distribution sync
(see docs -- "files are truth, rows are a disposable local cache": a
future sync phase recomputes locally from a retained source file, never
trusting a computed value from another machine).

Mirrors app/inventory_upload_service.py's local-retention shape exactly
(one physical file per slot, named by slot_id; one
database.models.WorkDistributionUploadSlot row), scaled from Inventory's
2 slots to 9: RGD Coverage, ABM, and RBM each upload independently per
division (Onyx/Guardians/Xandra -- see app.workbook_connections.WORKBOOK_NAMES,
the same fixed 3-division set used everywhere else in this app), so there
are 9 independent (report_type, division) slots, not one combined upload.

Retention only -- no manifest, no Storage bucket, no sync/pull/gate logic.
Wired alongside (never replacing) the existing RGD/ABM/RBM parse+process
calls in ui/work_distribution_upload_page.py; none of that existing
processing/calculation logic is touched by this module.
"""

import shutil
from pathlib import Path

from loguru import logger

from app.config import WORK_DISTRIBUTION_UPLOADS_DIR
from app.workbook_connections import WORKBOOK_NAMES
from database.connection import get_session, utcnow
from database.models import WorkDistributionUploadSlot

RGD = "rgd"
ABM = "abm"
RBM = "rbm"
REPORT_TYPES = (RGD, ABM, RBM)

REPORT_TYPE_LABELS = {
    RGD: "RGD Coverage",
    ABM: "Manager Work Allocation (ABM)",
    RBM: "Manager Work Allocation (RBM)",
}

DIVISIONS = WORKBOOK_NAMES  # ("Onyx", "Guardians", "Xandra")


def slot_id_for(report_type: str, division: str) -> str:
    """The one place a (report_type, division) pair becomes a slot_id --
    e.g. ("rgd", "Onyx") -> "rgd_onyx". Every retention function below
    takes report_type/division separately and calls this internally,
    rather than callers building the string themselves."""
    return f"{report_type}_{division.lower()}"


def _stored_path_for(slot_id: str, source_extension: str) -> Path:
    return WORK_DISTRIBUTION_UPLOADS_DIR / f"{slot_id}{source_extension}"


def _clear_stale_copies(slot_id: str) -> None:
    """Remove any previously stored file for this slot under a DIFFERENT
    extension than the new upload -- same reasoning as
    app/inventory_upload_service.py's own _clear_stale_copies."""
    for existing in WORK_DISTRIBUTION_UPLOADS_DIR.glob(f"{slot_id}.*"):
        try:
            existing.unlink()
        except OSError as exc:
            logger.warning(f"Work Distribution upload '{slot_id}': could not remove stale copy {existing}: {exc}")


def store_work_distribution_upload(report_type: str, division: str, source_path: str) -> Path:
    """Copy `source_path` into this (report_type, division) slot's own
    retained storage. Caller's responsibility to have already confirmed
    the file parsed successfully -- this function does not validate, it
    only retains (same convention as app.inventory_upload_service: an
    invalid upload is never stored)."""
    slot_id = slot_id_for(report_type, division)
    extension = Path(source_path).suffix.lower()
    _clear_stale_copies(slot_id)
    stored_path = _stored_path_for(slot_id, extension)
    shutil.copyfile(source_path, stored_path)

    session = get_session()
    try:
        row = session.query(WorkDistributionUploadSlot).filter_by(slot_id=slot_id).first()
        if row is None:
            row = WorkDistributionUploadSlot(slot_id=slot_id)
            session.add(row)
        row.filename = Path(source_path).name
        row.file_path = str(stored_path)
        row.uploaded_at = utcnow()
        row.only_on_this_machine = True
        session.commit()
    finally:
        session.close()

    logger.info(f"Work Distribution upload '{slot_id}': retained '{Path(source_path).name}' at {stored_path}")
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
    }


def _row_to_state(row: WorkDistributionUploadSlot) -> dict:
    return {
        "slot_id": row.slot_id,
        "uploaded": bool(row.file_path),
        "filename": row.filename,
        "file_path": row.file_path,
        "uploaded_at": row.uploaded_at,
        "uploaded_by": row.uploaded_by,
        "only_on_this_machine": bool(row.only_on_this_machine),
    }


def get_slot_state(report_type: str, division: str) -> dict:
    slot_id = slot_id_for(report_type, division)
    session = get_session()
    try:
        row = session.query(WorkDistributionUploadSlot).filter_by(slot_id=slot_id).first()
        return _row_to_state(row) if row else _empty_state(slot_id)
    finally:
        session.close()


def get_all_slot_states() -> dict:
    """{slot_id: state_dict} for all 9 (report_type, division) slots --
    including a slot with no row yet (never uploaded)."""
    session = get_session()
    try:
        rows = {row.slot_id: row for row in session.query(WorkDistributionUploadSlot).all()}
    finally:
        session.close()

    all_slot_ids = [slot_id_for(rt, div) for rt in REPORT_TYPES for div in DIVISIONS]
    return {
        slot_id: (_row_to_state(rows[slot_id]) if slot_id in rows else _empty_state(slot_id))
        for slot_id in all_slot_ids
    }
