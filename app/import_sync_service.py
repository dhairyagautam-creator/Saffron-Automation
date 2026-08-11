"""Cloud sync for Operations imports and the shared active session --
Version 2.0 Path Validator cloud migration, Milestone 14, unified onto
the app-wide Last-Modified-Wins rule in Milestone 35 (see
app/sync_service.reconcile_rows()).

raw_visits has an intentionally dynamic schema (whatever columns the
uploaded Excel contains -- see database/import_service.py), so it is never
mirrored row-by-row into Postgres. Instead, each division's original
uploaded file is stored in Supabase Storage as the source of truth, and a
laptop that doesn't have an import locally reconstructs it by downloading
those files and re-running the existing, unchanged local pipeline
(database.import_service.save_import, app.metrics.calculate_metrics)
against them.

Reconstruction deliberately never re-runs rule evaluation
(rules.same_location.evaluate) -- a remote-origin import's findings are
pulled from the cloud instead (see app/findings_sync_service.py). Two
laptops independently re-evaluating the same import could diverge (which
findings' status gets carried forward, Hospital Suppression's
time-sensitive lookups, etc.) -- the laptop that actually ran the analysis
is kept as the single source of truth for that import's findings.

import_history rows are effectively immutable once created (an import's
file_name/rows_imported/etc. never change), so `imported_at` doubles as
the "last modified" timestamp reconcile_rows() compares -- there is
nothing to bump on mutation because nothing ever mutates an existing
import row. The active session pointer, by contrast, genuinely changes
(whoever activates a different import most recently), so it gets a real
bidirectional Last-Modified-Wins reconciliation.
"""

import uuid
from datetime import datetime
from pathlib import Path

from loguru import logger

from app.config import DATA_DIR
from app.mode_state import is_developer_mode
from app.sync_service import download_file, push_rows, reconcile_rows, upload_file
from database.connection import get_session
from database.models import ActiveSession, ImportHistory

BUCKET = "path-validator-operations-uploads"
IMPORTS_TABLE = "path_validator_imports"
ACTIVE_SESSION_TABLE = "path_validator_active_session"

CLOUD_CACHE_DIR = DATA_DIR / "cloud_cache" / "operations_imports"


def _parse_ts(raw):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _serialize(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def push_import(import_id: int, division_file_paths: dict) -> bool:
    """Uploads each division's original file to Storage and pushes the
    import's metadata row, generating its cloud_id if it doesn't have one
    yet. Called once, right after a brand-new local import finishes
    processing -- files only exist as local paths at this exact moment,
    so this is a one-time "create" action that can't be generically
    reconciled later (see sync_imports()'s docstring for why a
    stuck-local-only row isn't retried generically). No-op (returns
    False) in Developer Mode."""
    if is_developer_mode():
        return False

    session = get_session()
    try:
        history = session.query(ImportHistory).filter_by(id=import_id).first()
        if history is None:
            logger.warning(f"Sync: push_import called for unknown import_id={import_id}")
            return False
        if not history.cloud_id:
            history.cloud_id = str(uuid.uuid4())
            session.commit()
        cloud_id = history.cloud_id
        file_name = history.file_name
        imported_at = history.imported_at
        rows_imported = history.rows_imported
        duplicates_removed = history.duplicates_removed
    finally:
        session.close()

    division_files = []
    for division, local_path in division_file_paths.items():
        original_name = Path(local_path).name
        storage_path = f"{cloud_id}/{division}__{original_name}"
        result = upload_file(BUCKET, storage_path, local_path, upsert=False)
        if not result.success:
            logger.warning(
                f"Sync: failed to upload {division} file for import cloud_id={cloud_id!r}: {result.error_message}"
            )
            return False
        division_files.append(
            {"division": division, "storage_path": storage_path, "original_file_name": original_name}
        )
        logger.info(f"Sync: uploaded {division} file for import cloud_id={cloud_id!r} -> {storage_path}")

    row = {
        "cloud_id": cloud_id,
        "file_name": file_name,
        "imported_at": imported_at.isoformat(),
        "rows_imported": rows_imported,
        "duplicates_removed": duplicates_removed,
        "division_files": division_files,
    }
    result = push_rows(IMPORTS_TABLE, [row], on_conflict="cloud_id")
    if not result.success:
        logger.warning(f"Sync: failed to push import metadata cloud_id={cloud_id!r}: {result.error_message}")
        return False

    session = get_session()
    try:
        history = session.query(ImportHistory).filter_by(id=import_id).first()
        history.synced_at = datetime.now()
        session.commit()
    finally:
        session.close()

    logger.info(f"Sync: import cloud_id={cloud_id!r} push complete; local import_id={import_id} marked synced")
    return True


def sync_imports() -> bool:
    """Last-Modified-Wins reconciliation for import metadata (see
    app/sync_service.reconcile_rows()) -- compares every local
    ImportHistory row against the cloud by cloud_id. Imports are
    effectively immutable once created, so in practice this almost always
    resolves to "cloud has an import this machine doesn't" (pulled as
    lightweight metadata only -- no file download/reparse until that
    import becomes the active session, see reconstruct_import_locally()).

    A local row reconcile_rows decides should be "pushed" is deliberately
    NOT acted on here: that only happens if push_import()'s own push
    already failed after its files uploaded successfully, and retrying it
    generically would need the original division_files mapping, which
    isn't stored anywhere once push_import() returns -- an accepted,
    narrow gap (push_import() succeeding is the common path; a mid-push
    network failure on this specific step is rare and not retried
    automatically). Returns True if any new imports were pulled. No-op
    (returns False) in Developer Mode."""
    if is_developer_mode():
        return False

    session = get_session()
    try:
        local_rows = [
            {"cloud_id": row.cloud_id, "updated_at": row.imported_at}
            for row in session.query(ImportHistory).all()
            if row.cloud_id
        ]
    finally:
        session.close()

    plan = reconcile_rows(IMPORTS_TABLE, local_rows, key_columns=["cloud_id"])
    if not plan.success:
        logger.warning(f"Sync: failed to reconcile imports: {plan.error_message}")
        return False
    if not plan.to_pull:
        return False

    created = 0
    session = get_session()
    try:
        known_cloud_ids = {row.cloud_id for row in session.query(ImportHistory).all() if row.cloud_id}
        for cloud_row in plan.to_pull:
            cloud_id = cloud_row["cloud_id"]
            if cloud_id in known_cloud_ids:
                continue
            session.add(
                ImportHistory(
                    file_name=cloud_row["file_name"],
                    imported_at=_parse_ts(cloud_row["imported_at"]) or datetime.now(),
                    rows_imported=cloud_row["rows_imported"],
                    duplicates_removed=cloud_row["duplicates_removed"],
                    cloud_id=cloud_id,
                    sync_origin="remote",
                    synced_at=datetime.now(),
                )
            )
            created += 1
        session.commit()
    finally:
        session.close()

    if created:
        logger.info(f"Sync: pulled {created} new import(s) from the cloud")
    return created > 0


def reconstruct_import_locally(cloud_id: str) -> int | None:
    """Downloads a remote import's division files and re-runs the
    existing, unchanged local pipeline (save_import + calculate_metrics)
    against them -- never evaluate_same_location() (see module docstring).
    Returns the local import_id, or None on failure. No-op (returns None)
    in Developer Mode."""
    if is_developer_mode():
        return None

    import pandas as pd

    from app.findings_sync_service import sync_findings_for_import
    from app.metrics import calculate_metrics
    from app.sync_service import pull_rows
    from database.import_service import save_import

    result = pull_rows(IMPORTS_TABLE, filters={"cloud_id": cloud_id})
    if not result.success or not result.rows:
        logger.warning(f"Sync: could not find import cloud_id={cloud_id!r} in the cloud to reconstruct")
        return None
    cloud_row = result.rows[0]

    dfs = []
    for division_file in cloud_row["division_files"]:
        local_dest = CLOUD_CACHE_DIR / cloud_id / division_file["original_file_name"]
        download_result = download_file(BUCKET, division_file["storage_path"], str(local_dest))
        if not download_result.success:
            logger.warning(
                f"Sync: failed to download {division_file['division']} file for import cloud_id={cloud_id!r}: "
                f"{download_result.error_message}"
            )
            return None
        dfs.append(pd.read_excel(local_dest))

    df = pd.concat(dfs, ignore_index=True)
    stats = save_import(df, cloud_row["file_name"])
    local_import_id = stats["import_id"]

    session = get_session()
    try:
        history = session.query(ImportHistory).filter_by(id=local_import_id).first()
        history.cloud_id = cloud_id
        history.sync_origin = "remote"
        history.synced_at = datetime.now()
        session.commit()
    finally:
        session.close()

    calculate_metrics(local_import_id)
    sync_findings_for_import(local_import_id)

    logger.info(
        f"Sync: reconstructed import cloud_id={cloud_id!r} locally -> local import_id={local_import_id}; "
        "skipped local rule evaluation, findings synced from cloud instead"
    )
    return local_import_id


def push_active_session(import_id: int) -> bool:
    """Pushes `import_id` as the team's shared active session
    immediately -- called right after a local Run Analysis makes this
    import active. A direct, immediate push (not a full reconcile) is
    consistent with Last-Modified-Wins, not a deviation: activating an
    import locally makes this the newest activation at this instant. The
    module-wide Refresh action's bidirectional reconciliation is
    sync_active_session(), below. No-op (returns False) in Developer
    Mode."""
    if is_developer_mode():
        return False

    session = get_session()
    try:
        history = session.query(ImportHistory).filter_by(id=import_id).first()
        if history is None or not history.cloud_id:
            logger.warning(f"Sync: cannot push active session -- import_id={import_id} has no cloud_id yet")
            return False
        import_cloud_id = history.cloud_id
    finally:
        session.close()

    now = datetime.now()
    result = push_rows(
        ACTIVE_SESSION_TABLE,
        [{"id": 1, "import_cloud_id": import_cloud_id, "activated_at": now.isoformat()}],
        on_conflict="id",
    )
    if not result.success:
        logger.warning(f"Sync: failed to push active session: {result.error_message}")
        return False

    session = get_session()
    try:
        row = session.query(ActiveSession).filter_by(id=1).first()
        if row:
            row.import_cloud_id = import_cloud_id
            row.activated_at = now
            row.synced_at = now
            session.commit()
    finally:
        session.close()

    logger.info(f"Sync: pushed active session -> import_cloud_id={import_cloud_id!r}")
    return True


def sync_active_session() -> bool:
    """Last-Modified-Wins reconciliation for the shared active-session
    pointer (see app/sync_service.reconcile_rows()): compares the local
    ActiveSession row's own activated_at against the cloud's updated_at
    for the same singleton row (id=1). Whichever side activated its
    import most recently wins -- if local is newer, push it; if cloud is
    newer, adopt it (reconstructing the import locally first if this
    machine doesn't have it yet). Returns True if the active session
    changed locally. No-op (returns False) in Developer Mode."""
    if is_developer_mode():
        return False

    session = get_session()
    try:
        row = session.query(ActiveSession).filter_by(id=1).first()
        local_rows = []
        if row is not None and row.import_cloud_id:
            local_rows.append(
                {"id": 1, "import_cloud_id": row.import_cloud_id, "activated_at": row.activated_at,
                 "updated_at": row.activated_at}
            )
    finally:
        session.close()

    plan = reconcile_rows(ACTIVE_SESSION_TABLE, local_rows, key_columns=["id"], filters={"id": 1})
    if not plan.success:
        logger.warning(f"Sync: failed to reconcile active session: {plan.error_message}")
        return False

    if plan.to_push:
        local_row = plan.to_push[0]
        result = push_rows(
            ACTIVE_SESSION_TABLE,
            [{"id": 1, "import_cloud_id": local_row["import_cloud_id"], "activated_at": _serialize(local_row["activated_at"])}],
            on_conflict="id",
        )
        if result.success:
            session = get_session()
            try:
                row = session.query(ActiveSession).filter_by(id=1).first()
                if row:
                    row.synced_at = datetime.now()
                    session.commit()
            finally:
                session.close()
            logger.info("Sync: pushed local active session (locally newer)")
        else:
            logger.warning(f"Sync: failed to push active session: {result.error_message}")
        return False

    if not plan.to_pull:
        return False

    cloud_import_cloud_id = plan.to_pull[0].get("import_cloud_id")
    if not cloud_import_cloud_id:
        return False

    # Idempotency guard against the active_session table's own clock-skew
    # noise: this table is still on the legacy timestamp reconcile, where the
    # cloud's server-UTC `updated_at` out-ranks our local naive `activated_at`
    # on EVERY tick, so reconcile keeps reporting "cloud is newer" even when it
    # points at the SAME import we are already on. Re-adopting it every 15s
    # returns changed=True, which reloads the visible page each tick and, right
    # after a fresh analysis, surfaces the transient window where findings
    # exist but suppression (applied later during email preview) hasn't landed
    # yet -- making Region Suppression look like it "isn't showing". If the
    # cloud pointer already matches ours, there is genuinely nothing to adopt.
    session = get_session()
    try:
        current = session.query(ActiveSession).filter_by(id=1).first()
        already_on_this_import = current is not None and current.import_cloud_id == cloud_import_cloud_id
    finally:
        session.close()
    if already_on_this_import:
        logger.debug(
            "Sync: active session already on the cloud's import "
            f"({cloud_import_cloud_id!r}) -- ignoring clock-skew reconcile noise, no switch"
        )
        return False

    session = get_session()
    try:
        matching_import = session.query(ImportHistory).filter_by(cloud_id=cloud_import_cloud_id).first()
        local_import_id = matching_import.id if matching_import else None
    finally:
        session.close()

    from app.session_state import set_active_import

    if local_import_id is None:
        local_import_id = reconstruct_import_locally(cloud_import_cloud_id)
        if local_import_id is None:
            return False

    set_active_import(local_import_id)

    session = get_session()
    try:
        row = session.query(ActiveSession).filter_by(id=1).first()
        if row:
            row.import_cloud_id = cloud_import_cloud_id
            row.synced_at = datetime.now()
            session.commit()
    finally:
        session.close()

    logger.info(f"Sync: active session changed on cloud -> switched to local import_id={local_import_id}")
    return True


def sync_findings_and_emails_for_active_session() -> bool:
    """Reconciles findings and email notifications for whatever import is
    CURRENTLY the local active session -- independent of whether the
    active session itself just changed. Needed because a reviewer status
    change or a new send on another laptop, for an import that's already
    this laptop's active session, has no other trigger to be noticed
    (sync_active_session() only reconciles findings/emails as a side
    effect of *adopting* a newly-changed session, via
    reconstruct_import_locally()). Returns True if anything changed
    locally. No-op (returns False) in Developer Mode."""
    if is_developer_mode():
        return False

    from app.email_sync_service import sync_email_notifications_for_import
    from app.findings_sync_service import sync_findings_for_import

    session = get_session()
    try:
        active = session.query(ActiveSession).filter_by(id=1).first()
        if active is None or active.import_id is None:
            return False
        history = session.query(ImportHistory).filter_by(id=active.import_id).first()
        if history is None or not history.cloud_id:
            return False
        local_import_id = history.id
    finally:
        session.close()

    findings_changed = sync_findings_for_import(local_import_id)
    emails_changed = sync_email_notifications_for_import(local_import_id)
    return bool(findings_changed or emails_changed)
