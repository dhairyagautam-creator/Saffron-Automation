"""Path Validator daily call report retention -- Phase 1-equivalent of
Path Validator sync (see app/path_validator_sync_service.py): local
retention for the daily call report, one physical file per division
(Onyx/Guardians/Xandra -- DIVISION_SLOTS in ui/operations_page.py).

Before this, ui/operations_page.py read the picked file directly via
pd.read_excel(file_path) with no retention at all -- the file itself was
never copied anywhere. Mirrors app/work_distribution_upload_service.py's
retention shape exactly (one physical file per slot; one
database.models.PathValidatorUploadSlot row), minus the report_type
dimension WD needs (RGD/ABM/RBM) -- Path Validator has exactly one report
type, so slots are keyed by division alone.

Retention only -- no manifest, no Storage bucket, no sync/pull/gate logic,
no save_import()/set_active_import() (see app/path_validator_sync_service.py
for all of that).
"""

import shutil
from pathlib import Path

from loguru import logger

from app.config import PATH_VALIDATOR_UPLOADS_DIR
from app.workbook_connections import WORKBOOK_NAMES
from database.connection import get_session, utcnow
from database.models import PathValidatorUploadSlot

DIVISIONS = WORKBOOK_NAMES  # ("Onyx", "Guardians", "Xandra")


def slot_id_for(division: str) -> str:
    """The one place a division becomes a slot_id -- e.g. "Onyx" ->
    "daily_report_onyx". Matches app.work_distribution_upload_service.
    slot_id_for's own {purpose}_{division.lower()} naming convention."""
    return f"daily_report_{division.lower()}"


def _stored_path_for(slot_id: str, source_extension: str) -> Path:
    return PATH_VALIDATOR_UPLOADS_DIR / f"{slot_id}{source_extension}"


def _clear_stale_copies(slot_id: str) -> None:
    """Remove any previously stored file for this slot under a DIFFERENT
    extension than the new upload -- same reasoning as
    app/work_distribution_upload_service.py's own _clear_stale_copies."""
    for existing in PATH_VALIDATOR_UPLOADS_DIR.glob(f"{slot_id}.*"):
        try:
            existing.unlink()
        except OSError as exc:
            logger.warning(f"Path Validator upload '{slot_id}': could not remove stale copy {existing}: {exc}")


def store_daily_report_upload(division: str, source_path: str) -> Path:
    """Copy `source_path` into this division's own retained storage.
    Caller's responsibility to have already confirmed the file parsed
    successfully -- this function does not validate, it only retains
    (same convention as app.work_distribution_upload_service)."""
    slot_id = slot_id_for(division)
    extension = Path(source_path).suffix.lower()
    _clear_stale_copies(slot_id)
    stored_path = _stored_path_for(slot_id, extension)
    shutil.copyfile(source_path, stored_path)

    session = get_session()
    try:
        row = session.query(PathValidatorUploadSlot).filter_by(slot_id=slot_id).first()
        if row is None:
            row = PathValidatorUploadSlot(slot_id=slot_id)
            session.add(row)
        row.filename = Path(source_path).name
        row.file_path = str(stored_path)
        row.uploaded_at = utcnow()
        row.only_on_this_machine = True
        session.commit()
    finally:
        session.close()

    logger.info(f"Path Validator upload '{slot_id}': retained '{Path(source_path).name}' at {stored_path}")
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


def _row_to_state(row: PathValidatorUploadSlot) -> dict:
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
        row = session.query(PathValidatorUploadSlot).filter_by(slot_id=slot_id).first()
        return _row_to_state(row) if row else _empty_state(slot_id)
    finally:
        session.close()


def get_all_slot_states() -> dict:
    """{slot_id: state_dict} for all 3 division slots -- including a slot
    with no row yet (never uploaded)."""
    session = get_session()
    try:
        rows = {row.slot_id: row for row in session.query(PathValidatorUploadSlot).all()}
    finally:
        session.close()

    all_slot_ids = [slot_id_for(d) for d in DIVISIONS]
    return {
        slot_id: (_row_to_state(rows[slot_id]) if slot_id in rows else _empty_state(slot_id))
        for slot_id in all_slot_ids
    }
