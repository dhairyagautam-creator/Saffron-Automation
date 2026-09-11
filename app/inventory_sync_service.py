"""Inventory Monitoring sync -- the second vertical slice of
docs/SYNC_DESIGN.md, extending the exact mechanism app/review_sync_service.py
already proved (see that module's docstring) to two new manifest slots:
sales_report and inventory_report. Same sync_manifest table, same
sync-uploads bucket, same content-addressed storage path, same hash
verification -- module="inventory" is all that scopes these two slots
away from Review System's rows; no Supabase-side schema change was needed.

The one behavior that's genuinely new here (not a mechanical copy): a
pulled Inventory Report is never applied on a machine that has no Sales
Report retained yet -- see apply_pending_updates()'s gate, and
docs/INVENTORY_SYNC_CONTEXT.md §3 for exactly the silent zero-evaluated
result this exists to prevent. This gate is sync-pull-only; the existing
single-machine local-upload behavior is unchanged (see
app/inventory_upload_service.py's module docstring).

Offline posture, version skew handling, and per-slot failure isolation are
all identical to Review System's own slice -- see app/review_sync_service.py
for the reasoning; this module reuses its _sha256_of_file/_parse_version/
_is_newer_version helpers directly rather than duplicating them.
"""

import hashlib
from datetime import datetime
from pathlib import Path

import httpx
from loguru import logger
from postgrest.exceptions import APIError

from app.inventory_upload_service import (
    INVENTORY_REPORT_SLOT,
    INVENTORY_SLOTS,
    PROCESS_FN,
    SALES_REPORT_SLOT,
    SLOT_LABELS,
    _clear_stale_copies,
    _stored_path_for,
    get_all_slot_states,
    has_sales_report_retained,
    store_inventory_upload,
)
from app.profile_names_service import display_name_for, refresh_profile_name_cache
from app.rbac_state import current_profile
from app.review_sync_service import _is_newer_version, _parse_version, _sha256_of_file  # noqa: F401 (re-exported)
from app.supabase_client import get_supabase_client
from app.version import APP_VERSION
from database.connection import get_session, utcnow
from database.models import InventoryUploadSlot, SyncModuleCheck, SyncState

MODULE = "inventory"
BUCKET = "sync-uploads"

_NETWORK_EXCEPTIONS = (httpx.ConnectError, httpx.TimeoutException)


# --- Upload path -------------------------------------------------------

def upload_and_sync(slot_id: str, source_path: str) -> dict:
    """The full upload path: process (validate + recompute/evaluate,
    against the ORIGINAL picked path) first -- an invalid file is never
    stored or synced, matching today's existing behavior exactly (see
    app/inventory_upload_service.py's module docstring) -- then, only on
    success, retain it locally and push it to Storage + the manifest.

    Never fails the LOCAL half because of a cloud problem: Supabase
    unreachable still retains the file and leaves the local
    processing result intact, reporting the sync outcome via `synced`/
    `sync_error` rather than raising -- same posture as
    app/review_sync_service.upload_and_sync."""
    process_fn = PROCESS_FN[slot_id]
    result = process_fn(source_path)
    if not result["success"] or result["error"] is not None:
        result.setdefault("synced", False)
        result.setdefault("sync_error", None)
        return result

    stored_path = store_inventory_upload(slot_id, source_path)

    profile = current_profile()
    if profile is None:
        result["synced"] = False
        result["sync_error"] = "Not signed in -- file stored locally only."
        return result

    original_filename = Path(source_path).name
    extension = stored_path.suffix.lower()

    try:
        client = get_supabase_client()
        sha256 = _sha256_of_file(stored_path)
        storage_path = f"{MODULE}/{slot_id}/{sha256}{extension}"

        with open(stored_path, "rb") as f:
            file_bytes = f.read()
        client.storage.from_(BUCKET).upload(
            storage_path, file_bytes, {"content-type": "application/octet-stream", "upsert": "true"}
        )

        insert_response = client.table("sync_manifest").insert(
            {
                "module": MODULE,
                "slot_key": slot_id,
                "storage_path": storage_path,
                "sha256": sha256,
                "size_bytes": len(file_bytes),
                "filename": original_filename,
                "app_version": APP_VERSION,
                "uploaded_by": profile.id,
            }
        ).execute()
        manifest_row = insert_response.data[0]

    except _NETWORK_EXCEPTIONS:
        logger.warning(f"Inventory sync: could not reach Supabase while uploading '{slot_id}' -- stored locally only.")
        result["synced"] = False
        result["sync_error"] = "Could not reach Supabase. This file is saved locally; it will not be visible to others until you're back online and re-upload."
        return result
    except APIError as exc:
        logger.error(f"Inventory sync: manifest push failed for '{slot_id}': {exc!r}")
        result["synced"] = False
        result["sync_error"] = f"Supabase rejected the sync push: {exc.message or exc.code}"
        return result
    except Exception as exc:
        logger.error(f"Inventory sync: unexpected error pushing '{slot_id}': {exc!r}")
        result["synced"] = False
        result["sync_error"] = "An unexpected error occurred while syncing this upload."
        return result

    now = utcnow()
    session = get_session()
    try:
        slot_row = session.query(InventoryUploadSlot).filter_by(slot_id=slot_id).first()
        if slot_row is not None:
            slot_row.uploaded_by = profile.id
            slot_row.only_on_this_machine = False

        state = session.query(SyncState).filter_by(module=MODULE, slot_key=slot_id).first()
        if state is None:
            state = SyncState(module=MODULE, slot_key=slot_id)
            session.add(state)
        state.applied_seq = manifest_row["seq"]
        state.applied_sha256 = sha256
        state.applied_at = now
        session.commit()
    finally:
        session.close()

    logger.info(f"Inventory sync: '{slot_id}' pushed to manifest (seq={manifest_row['seq']})")
    result["synced"] = True
    result["sync_error"] = None
    return result


def current_uploader_for_confirm(slot_id: str) -> tuple[str, str] | None:
    """(display_name, local_time_text) for whoever this machine currently
    believes uploaded `slot_id`, or None if empty/unknown -- purely local,
    no network call. Same shape as app.review_sync_service's own
    current_uploader_for_confirm."""
    from database.connection import to_local

    session = get_session()
    try:
        row = session.query(InventoryUploadSlot).filter_by(slot_id=slot_id).first()
    finally:
        session.close()
    if row is None or not row.file_path or not row.uploaded_by:
        return None
    local_time = to_local(row.uploaded_at)
    time_text = local_time.strftime("%d %b %Y, %I:%M %p") if local_time else "an unknown time"
    return display_name_for(row.uploaded_by), time_text


def is_uploaded_by_someone_else(slot_id: str) -> bool:
    row_uploader = current_uploader_for_confirm(slot_id)
    if row_uploader is None:
        return False
    profile = current_profile()
    session = get_session()
    try:
        row = session.query(InventoryUploadSlot).filter_by(slot_id=slot_id).first()
    finally:
        session.close()
    return bool(row and row.uploaded_by and profile and row.uploaded_by != profile.id)


# --- Check (banner) ------------------------------------------------------

def check_for_updates() -> dict:
    """The lightweight check that drives the Uploads page's status panel
    banner -- same one-query-per-module shape as
    app.review_sync_service.check_for_updates, scoped to INVENTORY_SLOTS
    (2 slots, not 12). Never raises; {"ok": False, "reason": ...} on any
    failure, with no local writes."""
    try:
        client = get_supabase_client()
        response = (
            client.table("sync_manifest")
            .select("slot_key, seq, uploaded_by")
            .eq("module", MODULE)
            .order("seq", desc=True)
            .execute()
        )
    except _NETWORK_EXCEPTIONS:
        logger.info("Inventory sync: check skipped -- Supabase unreachable.")
        return {"ok": False, "reason": "offline"}
    except (APIError, Exception) as exc:
        logger.warning(f"Inventory sync: check failed: {exc!r}")
        return {"ok": False, "reason": "error"}

    latest_by_slot: dict[str, dict] = {}
    for row in response.data or []:
        latest_by_slot.setdefault(row["slot_key"], row)

    refresh_profile_name_cache(client)

    session = get_session()
    try:
        local_applied = {
            s.slot_key: s.applied_seq
            for s in session.query(SyncState).filter_by(module=MODULE).all()
        }
        slot_rows = {r.slot_id: r for r in session.query(InventoryUploadSlot).all()}
    finally:
        session.close()

    changed = []
    only_local = []
    for slot_id in INVENTORY_SLOTS:
        remote = latest_by_slot.get(slot_id)
        local_row = slot_rows.get(slot_id)
        has_local_file = bool(local_row and local_row.file_path)

        if remote is None:
            if has_local_file:
                only_local.append(slot_id)
            continue

        applied_seq = local_applied.get(slot_id)
        if applied_seq is not None and remote["seq"] <= applied_seq:
            continue

        changed.append(
            {
                "slot_id": slot_id,
                "is_first_fill": applied_seq is None,
                "is_replacement": applied_seq is not None,
                "uploader_name": display_name_for(remote.get("uploaded_by")),
                "remote_seq": remote["seq"],
            }
        )

    now = utcnow()
    session = get_session()
    try:
        check_row = session.query(SyncModuleCheck).filter_by(module=MODULE).first()
        if check_row is None:
            check_row = SyncModuleCheck(module=MODULE)
            session.add(check_row)
        max_seq = max((r["seq"] for r in latest_by_slot.values()), default=None)
        check_row.last_checked_seq = max_seq
        check_row.last_checked_at = now

        only_local_set = set(only_local)
        for slot_id, row in slot_rows.items():
            if not row.file_path:
                continue
            row.only_on_this_machine = slot_id in only_local_set
        session.commit()
    finally:
        session.close()

    states = get_all_slot_states()
    filled = [sid for sid, st in states.items() if st["uploaded"]]

    return {
        "ok": True,
        "changed": changed,
        "only_local": only_local,
        "filled_count": len(filled),
        "total_count": len(states),
    }


# --- Apply (pull) ---------------------------------------------------------

def apply_pending_updates(slot_ids: list[str], progress_cb=None) -> dict:
    """Downloads, verifies, and applies every slot in `slot_ids` (the
    `changed` list from check_for_updates()). Always processed in
    INVENTORY_SLOTS order (sales_report before inventory_report)
    regardless of the order `slot_ids` was passed in -- inventory_report's
    own processing recomputes thresholds from whichever Sales Report is
    currently retained, so if both slots changed in the same check,
    sales_report must land on disk first for that recompute to see it.

    GATE: inventory_report is never applied on a machine with no Sales
    Report currently retained -- see has_sales_report_retained() and
    docs/INVENTORY_SYNC_CONTEXT.md §3 (the exact silent zero-evaluated
    result this exists to prevent). A blocked inventory_report is reported
    as a `failed` entry with a clear reason, never a silent skip.

    Returns {"applied": [...], "failed": [{"slot_id","reason"}, ...],
    "version_blocked": [...]} -- same shape as
    app.review_sync_service.apply_pending_updates."""
    applied, failed, version_blocked = [], [], []
    ordered_slot_ids = [s for s in INVENTORY_SLOTS if s in slot_ids]

    try:
        client = get_supabase_client()
    except RuntimeError as exc:
        return {
            "applied": [],
            "failed": [{"slot_id": s, "reason": str(exc)} for s in ordered_slot_ids],
            "version_blocked": [],
        }

    session = get_session()
    try:
        local_applied = {
            s.slot_key: s.applied_seq
            for s in session.query(SyncState).filter_by(module=MODULE).all()
        }
    finally:
        session.close()

    for i, slot_id in enumerate(ordered_slot_ids):
        if progress_cb:
            progress_cb(int(i / len(ordered_slot_ids) * 100), f"Applying {SLOT_LABELS[slot_id]}...")

        cutoff = local_applied.get(slot_id) or 0
        try:
            response = (
                client.table("sync_manifest")
                .select("*")
                .eq("module", MODULE)
                .eq("slot_key", slot_id)
                .gt("seq", cutoff)
                .order("seq", desc=True)
                .limit(1)
                .execute()
            )
        except _NETWORK_EXCEPTIONS:
            failed.append({"slot_id": slot_id, "reason": "Could not reach Supabase."})
            continue
        except Exception as exc:
            failed.append({"slot_id": slot_id, "reason": f"Could not fetch manifest row: {exc!r}"})
            continue

        rows = response.data or []
        if not rows:
            continue
        manifest_row = rows[0]

        if _is_newer_version(manifest_row["app_version"], APP_VERSION):
            version_blocked.append({"slot_id": slot_id, "remote_version": manifest_row["app_version"]})
            logger.warning(
                f"Inventory sync: '{slot_id}' skipped -- manifest row was written by app_version "
                f"{manifest_row['app_version']!r}, newer than this build ({APP_VERSION!r})."
            )
            continue

        if slot_id == INVENTORY_REPORT_SLOT and not has_sales_report_retained():
            failed.append(
                {
                    "slot_id": slot_id,
                    "reason": (
                        "No Sales Report has been uploaded or synced to this machine yet. "
                        "Upload or sync a Sales Report first -- applying the Inventory Report now "
                        "would evaluate replenishment/CWH against empty thresholds."
                    ),
                }
            )
            logger.warning(f"Inventory sync: '{slot_id}' pull BLOCKED -- no Sales Report retained on this machine.")
            continue

        try:
            file_bytes = client.storage.from_(BUCKET).download(manifest_row["storage_path"])
        except _NETWORK_EXCEPTIONS:
            failed.append({"slot_id": slot_id, "reason": "Could not reach Supabase to download the file."})
            continue
        except Exception as exc:
            failed.append({"slot_id": slot_id, "reason": f"Download failed: {exc!r}"})
            continue

        actual_hash = hashlib.sha256(file_bytes).hexdigest()
        if actual_hash != manifest_row["sha256"]:
            failed.append({"slot_id": slot_id, "reason": "Downloaded file's hash did not match the manifest. Not applied."})
            logger.error(
                f"Inventory sync: HASH MISMATCH for '{slot_id}' (manifest seq={manifest_row['seq']}) -- "
                f"expected {manifest_row['sha256']}, got {actual_hash}. File rejected, marker not advanced."
            )
            continue

        try:
            extension = Path(manifest_row["filename"]).suffix.lower()
            _clear_stale_copies(slot_id)
            stored_path = _stored_path_for(slot_id, extension)
            with open(stored_path, "wb") as f:
                f.write(file_bytes)
        except OSError as exc:
            failed.append({"slot_id": slot_id, "reason": f"Could not write the file locally: {exc}"})
            continue

        try:
            process_result = PROCESS_FN[slot_id](str(stored_path))
        except Exception as exc:
            failed.append({"slot_id": slot_id, "reason": f"Applied file failed processing: {exc!r}"})
            logger.error(f"Inventory sync: '{slot_id}' processing raised after a verified download: {exc!r}")
            continue

        if not process_result["success"] or process_result["error"] is not None:
            failed.append(
                {
                    "slot_id": slot_id,
                    "reason": f"Downloaded file did not pass validation: {process_result.get('error') or process_result.get('missing_columns')}",
                }
            )
            continue

        now = utcnow()
        session = get_session()
        try:
            slot_row = session.query(InventoryUploadSlot).filter_by(slot_id=slot_id).first()
            if slot_row is None:
                slot_row = InventoryUploadSlot(slot_id=slot_id)
                session.add(slot_row)
            slot_row.filename = manifest_row["filename"]
            slot_row.file_path = str(stored_path)
            slot_row.uploaded_at = _parse_manifest_timestamp(manifest_row["uploaded_at"])
            slot_row.uploaded_by = manifest_row["uploaded_by"]
            slot_row.only_on_this_machine = False

            state = session.query(SyncState).filter_by(module=MODULE, slot_key=slot_id).first()
            if state is None:
                state = SyncState(module=MODULE, slot_key=slot_id)
                session.add(state)
            state.applied_seq = manifest_row["seq"]
            state.applied_sha256 = actual_hash
            state.applied_at = now
            session.commit()
        finally:
            session.close()

        applied.append(slot_id)
        logger.info(f"Inventory sync: applied '{slot_id}' (seq={manifest_row['seq']}, hash verified)")

    if progress_cb:
        progress_cb(100, "Done")

    return {"applied": applied, "failed": failed, "version_blocked": version_blocked}


def _parse_manifest_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None)
