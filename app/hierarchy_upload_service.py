"""Work Distribution hierarchy upload retention -- the local-retention
layer hierarchy uploads have never had (see app/work_distribution_sync_service.py's
module docstring). Before this, ui/work_distribution_email_center_page.py's
Browse handler stored only the raw OS path a user picked (see
app.workbook_connections.set_connection) -- the file itself was never
copied anywhere, unlike every other upload in this app.

Mirrors app/work_distribution_upload_service.py's retention shape exactly
(one physical file per slot, named by slot_id; one
database.models.HierarchyUploadSlot row), scoped to Work Distribution's
own 3 division slots (Onyx/Guardians/Xandra -- app.workbook_connections.
WORKBOOK_NAMES), not the 9 report-type x division slots RGD/ABM/RBM use --
there is exactly one hierarchy workbook per division, not one per
(report_type, division).

Retention only -- no manifest, no Storage bucket, no sync/pull/gate logic
(see app/work_distribution_sync_service.py for that).
"""

import shutil
from pathlib import Path

from loguru import logger

from app.config import HIERARCHY_UPLOADS_DIR
from app.workbook_connections import WORKBOOK_NAMES
from database.connection import get_session, utcnow
from database.models import HierarchyUploadSlot

DIVISIONS = WORKBOOK_NAMES  # ("Onyx", "Guardians", "Xandra")


def slot_id_for(division: str) -> str:
    """The one place a division becomes a slot_id -- e.g. "Onyx" ->
    "hierarchy_onyx". Matches app.work_distribution_upload_service.
    slot_id_for's own {purpose}_{division.lower()} naming convention."""
    return f"hierarchy_{division.lower()}"


def _stored_path_for(slot_id: str, source_extension: str) -> Path:
    return HIERARCHY_UPLOADS_DIR / f"{slot_id}{source_extension}"


def _clear_stale_copies(slot_id: str) -> None:
    """Remove any previously stored file for this slot under a DIFFERENT
    extension than the new upload -- same reasoning as
    app/work_distribution_upload_service.py's own _clear_stale_copies."""
    for existing in HIERARCHY_UPLOADS_DIR.glob(f"{slot_id}.*"):
        try:
            existing.unlink()
        except OSError as exc:
            logger.warning(f"Hierarchy upload '{slot_id}': could not remove stale copy {existing}: {exc}")


def store_hierarchy_upload(division: str, source_path: str) -> Path:
    """Copy `source_path` into this division's own retained storage.
    Caller's responsibility to have already confirmed the file is a
    genuine hierarchy workbook -- this function does not validate, it only
    retains (same convention as app.work_distribution_upload_service:
    there is no separate schema-validation step for a hierarchy workbook
    the way RGD/ABM/RBM reports have; correctness is only ever proven by
    app.hierarchy_parser.refresh_hierarchy() actually running against it)."""
    slot_id = slot_id_for(division)
    extension = Path(source_path).suffix.lower()
    _clear_stale_copies(slot_id)
    stored_path = _stored_path_for(slot_id, extension)
    shutil.copyfile(source_path, stored_path)

    session = get_session()
    try:
        row = session.query(HierarchyUploadSlot).filter_by(slot_id=slot_id).first()
        if row is None:
            row = HierarchyUploadSlot(slot_id=slot_id)
            session.add(row)
        row.filename = Path(source_path).name
        row.file_path = str(stored_path)
        row.uploaded_at = utcnow()
        row.only_on_this_machine = True
        session.commit()
    finally:
        session.close()

    logger.info(f"Hierarchy upload '{slot_id}': retained '{Path(source_path).name}' at {stored_path}")
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


def _row_to_state(row: HierarchyUploadSlot) -> dict:
    return {
        "slot_id": row.slot_id,
        "uploaded": bool(row.file_path),
        "filename": row.filename,
        "file_path": row.file_path,
        "uploaded_at": row.uploaded_at,
        "uploaded_by": row.uploaded_by,
        "only_on_this_machine": bool(row.only_on_this_machine),
    }


def get_slot_state(division: str) -> dict:
    slot_id = slot_id_for(division)
    session = get_session()
    try:
        row = session.query(HierarchyUploadSlot).filter_by(slot_id=slot_id).first()
        return _row_to_state(row) if row else _empty_state(slot_id)
    finally:
        session.close()


def get_all_slot_states() -> dict:
    """{slot_id: state_dict} for all 3 division slots -- including a slot
    with no row yet (never uploaded)."""
    session = get_session()
    try:
        rows = {row.slot_id: row for row in session.query(HierarchyUploadSlot).all()}
    finally:
        session.close()

    all_slot_ids = [slot_id_for(d) for d in DIVISIONS]
    return {
        slot_id: (_row_to_state(rows[slot_id]) if slot_id in rows else _empty_state(slot_id))
        for slot_id in all_slot_ids
    }
