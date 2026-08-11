"""Cloud sync for Organization Data workbooks (Onyx/Guardians/Xandra) --
Version 2.0 Path Validator cloud migration, Milestone 13, unified onto the
app-wide Last-Modified-Wins rule in Milestone 35 (see
app/sync_service.reconcile_rows()).

Supabase Storage (bucket 'path-validator-organization-data') holds each
workbook as the source of truth, one object per workbook name, overwritten
in place every time a new one is connected. employee_hierarchy is never
mirrored row-by-row into Postgres -- each laptop reconstructs it by
downloading the workbook and re-running the existing, unchanged
app.hierarchy_parser.refresh_hierarchy() against it, exactly as if someone
had manually browsed to that file.

WorkbookConnection.updated_at (bumped by app/workbook_connections.py's
set_connection(), whether the caller is a local Browse click or this
module applying a pull) is the reliable local "last modified" timestamp
compared against the cloud's own updated_at.
"""

from datetime import datetime
from pathlib import Path

from loguru import logger

from app.config import DATA_DIR
from app.mode_state import is_developer_mode
from app.sync_service import download_file, push_rows, reconcile_rows, upload_file
from app.workbook_connections import WORKBOOK_NAMES, set_connection
from database.connection import get_session
from database.models import WorkbookConnection

BUCKET = "path-validator-organization-data"
TABLE = "path_validator_organization_workbooks"

CLOUD_CACHE_DIR = DATA_DIR / "cloud_cache" / "organization_data"


def _parse_ts(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def push_workbook(workbook_name: str, local_file_path: str) -> bool:
    """Uploads the connected workbook to Storage and pushes its cloud
    metadata row immediately -- called right after a local Browse click
    connects a new file. A direct, immediate push (not a full reconcile)
    is consistent with Last-Modified-Wins, not a deviation: connecting a
    workbook locally makes this the newest version at this instant. The
    module-wide Refresh action's bidirectional reconciliation is
    sync_workbooks(), below. No-op (returns False) in Developer Mode --
    Developer Mode data must never reach the shared cloud tables."""
    if is_developer_mode():
        return False
    if workbook_name not in WORKBOOK_NAMES:
        logger.warning(f"Sync: push_workbook called with unknown workbook_name={workbook_name!r}")
        return False

    original_name = Path(local_file_path).name
    storage_path = f"{workbook_name}.xlsx"

    upload_result = upload_file(BUCKET, storage_path, local_file_path, upsert=True)
    if not upload_result.success:
        logger.warning(f"Sync: failed to upload workbook '{workbook_name}': {upload_result.error_message}")
        return False

    push_result = push_rows(
        TABLE,
        [{"workbook_name": workbook_name, "storage_path": storage_path, "original_file_name": original_name}],
        on_conflict="workbook_name",
    )
    if not push_result.success:
        logger.warning(f"Sync: failed to push workbook metadata for '{workbook_name}': {push_result.error_message}")
        return False

    session = get_session()
    try:
        row = session.query(WorkbookConnection).filter_by(workbook_name=workbook_name).first()
        if row is not None:
            row.storage_path = storage_path
            cloud_updated_at_raw = push_result.rows[0].get("updated_at") if push_result.rows else None
            row.cloud_updated_at = _parse_ts(cloud_updated_at_raw)
            row.synced_at = datetime.now()
            session.commit()
    finally:
        session.close()

    logger.info(f"Sync: workbook '{workbook_name}' pushed to storage_path={storage_path!r}")
    return True


def sync_workbooks() -> bool:
    """Last-Modified-Wins reconciliation for all 3 Organization Data
    workbooks (see app/sync_service.reconcile_rows()): compares each
    local WorkbookConnection's updated_at against the cloud row's own
    updated_at, keyed by workbook_name. Whichever side connected a newer
    version wins -- if local is newer, push it (file + metadata); if
    cloud is newer, download and reparse it. Returns True if anything
    changed locally. No-op (returns False) in Developer Mode."""
    if is_developer_mode():
        return False

    session = get_session()
    try:
        local_rows = [
            {
                "workbook_name": row.workbook_name,
                "storage_path": row.storage_path,
                "updated_at": row.updated_at,
                "_file_path": row.file_path,
            }
            for row in session.query(WorkbookConnection).all()
            if row.workbook_name in WORKBOOK_NAMES
        ]
    finally:
        session.close()

    plan = reconcile_rows(TABLE, local_rows, key_columns=["workbook_name"])
    if not plan.success:
        logger.warning(f"Sync: failed to reconcile organization workbooks: {plan.error_message}")
        return False

    changed = False

    for local_row in plan.to_push:
        local_file_path = local_row.get("_file_path")
        if not local_file_path:
            logger.warning(
                f"Sync: workbook '{local_row['workbook_name']}' is locally newer but has no local file path to "
                "push -- skipping"
            )
            continue
        push_workbook(local_row["workbook_name"], local_file_path)

    for cloud_row in plan.to_pull:
        workbook_name = cloud_row.get("workbook_name")
        if workbook_name not in WORKBOOK_NAMES:
            continue

        storage_path = cloud_row["storage_path"]
        local_dest = CLOUD_CACHE_DIR / f"{workbook_name}.xlsx"
        download_result = download_file(BUCKET, storage_path, str(local_dest))
        if not download_result.success:
            logger.warning(f"Sync: failed to download workbook '{workbook_name}': {download_result.error_message}")
            continue

        set_connection(workbook_name, str(local_dest))
        session = get_session()
        try:
            row = session.query(WorkbookConnection).filter_by(workbook_name=workbook_name).first()
            if row is not None:
                row.storage_path = storage_path
                # Stamped to the cloud's own updated_at (not now()) so this
                # pulled connection doesn't look locally-newer than it
                # actually is on the next reconcile -- set_connection()
                # already bumped updated_at to now() above, which this
                # deliberately overwrites.
                row.updated_at = _parse_ts(cloud_row.get("updated_at")) or datetime.now()
                row.cloud_updated_at = row.updated_at
                row.synced_at = datetime.now()
                session.commit()
        finally:
            session.close()

        logger.info(f"Sync: downloaded newer workbook '{workbook_name}' from cloud -> {local_dest}")
        changed = True

    if changed:
        from app.hierarchy_parser import refresh_hierarchy

        refresh_hierarchy()
        logger.info("Sync: employee_hierarchy rebuilt from newly downloaded Organization Data workbook(s)")

    return changed
