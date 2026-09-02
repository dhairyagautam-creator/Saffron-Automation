"""Review System upload state: physical file storage + persisted
validation state per slot, and the one centralized readiness gate.

Mirrors app/workbook_connections.py's named-slot-to-file-path shape --
each of the 12 slots (app/review_schemas.REVIEW_FILE_SLOTS) gets exactly
one physical file (named by slot_id, so a replace just overwrites it) and
one database.models.ReviewFileSlot row. The uploaded workbook itself is
never committed to git (see .gitignore) and never leaves the local data
directory.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.config import REVIEW_UPLOADS_DIR
from app.review_schemas import REVIEW_FILE_SLOTS, TOTAL_REQUIRED_SLOTS
from app.review_validation import validate_review_file
from database.connection import get_session
from database.models import ReviewFileSlot


def _empty_state(slot_id: str) -> dict:
    return {
        "slot_id": slot_id,
        "uploaded": False,
        "valid": False,
        "filename": None,
        "file_path": None,
        "errors": [],
        "row_count": None,
        "column_count": None,
        "uploaded_at": None,
    }


def _row_to_state(row: ReviewFileSlot) -> dict:
    return {
        "slot_id": row.slot_id,
        "uploaded": bool(row.file_path),
        "valid": bool(row.valid),
        "filename": row.filename,
        "file_path": row.file_path,
        "errors": json.loads(row.errors_json) if row.errors_json else [],
        "row_count": row.row_count,
        "column_count": row.column_count,
        "uploaded_at": row.uploaded_at,
    }


def get_slot_state(slot_id: str) -> dict:
    """Current state for one slot. Always returns a dict, even for a slot
    that has never been uploaded to (uploaded=False, valid=False)."""
    session = get_session()
    try:
        row = session.query(ReviewFileSlot).filter_by(slot_id=slot_id).first()
        return _row_to_state(row) if row else _empty_state(slot_id)
    finally:
        session.close()


def get_all_slot_states() -> dict:
    """{slot_id: state_dict} for every one of the 12 required slots --
    including slots with no row yet (never uploaded)."""
    session = get_session()
    try:
        rows = {row.slot_id: row for row in session.query(ReviewFileSlot).all()}
    finally:
        session.close()

    return {
        slot.slot_id: (_row_to_state(rows[slot.slot_id]) if slot.slot_id in rows else _empty_state(slot.slot_id))
        for slot in REVIEW_FILE_SLOTS
    }


def _stored_path_for(slot_id: str, source_extension: str) -> Path:
    return REVIEW_UPLOADS_DIR / f"{slot_id}{source_extension}"


def _clear_stale_copies(slot_id: str) -> None:
    """Remove any previously stored file for this slot under a DIFFERENT
    extension than the new upload -- otherwise replacing a .xlsx with a
    .csv would leave the old .xlsx orphaned on disk."""
    for existing in REVIEW_UPLOADS_DIR.glob(f"{slot_id}.*"):
        try:
            existing.unlink()
        except OSError as exc:
            logger.warning(f"Review System '{slot_id}': could not remove stale copy {existing}: {exc}")


def upload_review_file(slot_id: str, source_path: str) -> dict:
    """Copy `source_path` into the Review System's own storage for
    `slot_id`, validate it, and persist the result. Returns the validation
    result dict (see app.review_validation.validate_review_file) so the
    caller can show detail immediately without a second lookup.

    Always stores the file (even if invalid) and always persists the
    validation outcome -- an invalid upload still occupies the slot (the
    user replaces or removes it explicitly), it just doesn't count toward
    readiness.
    """
    extension = Path(source_path).suffix.lower()
    _clear_stale_copies(slot_id)
    stored_path = _stored_path_for(slot_id, extension)
    shutil.copyfile(source_path, stored_path)

    result = validate_review_file(slot_id, str(stored_path))
    original_filename = Path(source_path).name

    session = get_session()
    try:
        row = session.query(ReviewFileSlot).filter_by(slot_id=slot_id).first()
        if row is None:
            row = ReviewFileSlot(slot_id=slot_id)
            session.add(row)
        row.filename = original_filename
        row.file_path = str(stored_path)
        row.valid = result["valid"]
        row.errors_json = json.dumps(result["errors"])
        row.row_count = result["row_count"]
        row.column_count = result["column_count"]
        row.uploaded_at = datetime.now()
        session.commit()
    finally:
        session.close()

    logger.info(f"Review System '{slot_id}': stored '{original_filename}' -> valid={result['valid']}")
    return result


def remove_review_file(slot_id: str) -> None:
    """Remove the uploaded file (disk + slot state) for `slot_id`. The slot
    ID itself is preserved (it's a fixed constant, not a database row) --
    this just resets that slot back to NOT UPLOADED."""
    session = get_session()
    try:
        row = session.query(ReviewFileSlot).filter_by(slot_id=slot_id).first()
        if row is not None:
            session.delete(row)
            session.commit()
    finally:
        session.close()

    _clear_stale_copies(slot_id)
    logger.info(f"Review System '{slot_id}': file removed")


def readiness_counts() -> tuple:
    """(uploaded_count, valid_count, total_required) across all 12 slots."""
    states = get_all_slot_states()
    uploaded = sum(1 for s in states.values() if s["uploaded"])
    valid = sum(1 for s in states.values() if s["uploaded"] and s["valid"])
    return uploaded, valid, TOTAL_REQUIRED_SLOTS


def is_review_analysis_ready() -> bool:
    """The single centralized readiness gate -- True only when every
    required slot has a file AND that file is valid. Every caller (the
    Uploads page today; the future Analysis engine) reads this same
    function rather than re-deriving readiness itself."""
    states = get_all_slot_states()
    return all(state["uploaded"] and state["valid"] for state in states.values())
