"""Hierarchy workbook upload retention -- the local-retention layer
hierarchy uploads have never had on their own (see
app/work_distribution_sync_service.py's and app/path_validator_sync_service.py's
own module docstrings). Before this, both
ui/work_distribution_email_center_page.py's and
ui/organization_data_page.py's Browse handlers stored only the raw OS path
a user picked (see app.workbook_connections.set_connection) -- the file
itself was never copied anywhere, unlike every other upload in this app.

Mirrors app/work_distribution_upload_service.py's retention shape (one
physical file per slot; one database.models.HierarchyUploadSlot row),
scoped to one division per slot (Onyx/Guardians/Xandra --
app.workbook_connections.WORKBOOK_NAMES), not report-type x division.

Shared across every module that owns its own hierarchy dataset (Work
Distribution, Path Validator, and eventually Review System) --
`module_key` is threaded through every function, exactly mirroring
app.hierarchy_parser.py's own per-module HIERARCHY_TABLES design: each
module's hierarchy is a completely independent dataset, so "Onyx" for one
module must never collide with "Onyx" for another, on disk or in the
database. `slot_id_for()` deliberately keeps returning the SAME bare
string it always has (e.g. "hierarchy_onyx") regardless of module_key --
that string is also Work Distribution's already-shipped, already-synced
sync_manifest slot_key, and changing it would silently orphan every real
manifest row already pushed under it. The actual collision fix lives in
two other places instead: the database row is now keyed by
(module_key, slot_id) together, not slot_id alone, and the retained file
on disk is now named f"{module_key}_{slot_id}{extension}", not just
f"{slot_id}{extension}".

Retention only -- no manifest, no Storage bucket, no sync/pull/gate logic
(see each module's own *_sync_service.py for that).
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
    "hierarchy_onyx". Deliberately NOT module-scoped (see module
    docstring) -- this is also the literal sync_manifest slot_key, and
    Work Distribution's is already shipped under this exact string."""
    return f"hierarchy_{division.lower()}"


def _stored_path_for(module_key: str, slot_id: str, source_extension: str) -> Path:
    return HIERARCHY_UPLOADS_DIR / f"{module_key}_{slot_id}{source_extension}"


def _clear_stale_copies(module_key: str, slot_id: str) -> None:
    """Remove any previously stored file for this (module_key, slot_id)
    under a DIFFERENT extension than the new upload -- same reasoning as
    app/work_distribution_upload_service.py's own _clear_stale_copies."""
    for existing in HIERARCHY_UPLOADS_DIR.glob(f"{module_key}_{slot_id}.*"):
        try:
            existing.unlink()
        except OSError as exc:
            logger.warning(f"Hierarchy upload '{module_key}/{slot_id}': could not remove stale copy {existing}: {exc}")


def store_hierarchy_upload(module_key: str, division: str, source_path: str) -> Path:
    """Copy `source_path` into this module's own retained storage for
    `division`. Caller's responsibility to have already confirmed the file
    is a genuine hierarchy workbook -- this function does not validate, it
    only retains (same convention as app.work_distribution_upload_service:
    correctness is only ever proven by app.hierarchy_parser.refresh_hierarchy()
    actually running against it)."""
    slot_id = slot_id_for(division)
    extension = Path(source_path).suffix.lower()
    _clear_stale_copies(module_key, slot_id)
    stored_path = _stored_path_for(module_key, slot_id, extension)
    shutil.copyfile(source_path, stored_path)

    session = get_session()
    try:
        row = session.query(HierarchyUploadSlot).filter_by(module_key=module_key, slot_id=slot_id).first()
        if row is None:
            row = HierarchyUploadSlot(module_key=module_key, slot_id=slot_id)
            session.add(row)
        row.filename = Path(source_path).name
        row.file_path = str(stored_path)
        row.uploaded_at = utcnow()
        row.only_on_this_machine = True
        session.commit()
    finally:
        session.close()

    logger.info(f"Hierarchy upload '{module_key}/{slot_id}': retained '{Path(source_path).name}' at {stored_path}")
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


def get_slot_state(module_key: str, division: str) -> dict:
    slot_id = slot_id_for(division)
    session = get_session()
    try:
        row = session.query(HierarchyUploadSlot).filter_by(module_key=module_key, slot_id=slot_id).first()
        return _row_to_state(row) if row else _empty_state(slot_id)
    finally:
        session.close()


def get_all_slot_states(module_key: str) -> dict:
    """{slot_id: state_dict} for all 3 of THIS module's division slots --
    including a slot with no row yet (never uploaded). Scoped to
    module_key, so Work Distribution's and Path Validator's own 3 slots
    each other never appear in each other's results even though both use
    the identical 3 slot_id strings."""
    session = get_session()
    try:
        rows = {
            row.slot_id: row
            for row in session.query(HierarchyUploadSlot).filter_by(module_key=module_key).all()
        }
    finally:
        session.close()

    all_slot_ids = [slot_id_for(d) for d in DIVISIONS]
    return {
        slot_id: (_row_to_state(rows[slot_id]) if slot_id in rows else _empty_state(slot_id))
        for slot_id in all_slot_ids
    }
