"""Work Distribution sync -- extends the exact mechanism
app/review_sync_service.py proved and app/inventory_sync_service.py already
reused (same sync_manifest table, same sync-uploads bucket, same
content-addressed storage path, same hash verification) to Work
Distribution's 12 slots: module="work_distribution".

9 slots are RGD Coverage / ABM / RBM, one per (report_type, division) --
see app/work_distribution_upload_service.py, Phase 1 of this sync (already
committed, retention only). 3 slots are the hierarchy workbook, one per
division -- see app/hierarchy_upload_service.py (this phase's new
retention layer).

Two things are genuinely new here, not a mechanical copy of Inventory's
shape:

1. RGD/ABM/RBM are NOT independently processable per division the way
   Inventory's two slots are -- each engine's own processing function
   (process_work_distribution_report / process_manager_work_allocation_report
   / process_rbm_report) takes a COMBINED cross-division list and does a
   full delete-then-rebuild of its findings table. A single slot's pull
   therefore never calls its engine's processing function directly (that
   would silently wipe the other two divisions' already-computed findings
   using only the one division that just arrived) -- see PROCESS_FN below,
   which only parses+validates on pull. _maybe_process_engine() is the
   real trigger: after EVERY pull (regardless of which slot), it re-checks
   all 3 of that engine's CURRENT retained files (never upload/pull
   history) and auto-runs the engine exactly once all 3 are present --
   correct whether they arrived together or days apart, and correct again
   on a later re-sync of just one corrected division.

2. A hierarchy slot's pull must update BOTH the retained file AND that
   division's workbook_connections row (module_key="work_distribution") --
   app.hierarchy_parser.refresh_hierarchy() reads ONLY workbook_connections
   to decide which divisions to include, never the retention layer
   directly. A pull that only retained the file would be silently useless.
   refresh_hierarchy() itself is never modified -- this makes sync just
   another path that produces the same "Connected" state manual
   browse+connect already does (see app/hierarchy_upload_service.py).

   Failure handling: if the file downloads and hash-verifies but the
   subsequent refresh_hierarchy() call raises, the whole pull is treated
   as if it never happened for marker-advancement purposes -- the local
   HierarchyUploadSlot row is not updated and SyncState.applied_seq is not
   advanced, so the slot stays "pending" and is retried on the next check
   (same failure posture as a hash mismatch or processing failure in every
   other sync module). The already-retained file and the already-updated
   workbook_connections row are left in place rather than rolled back
   (this codebase has no cross-table transaction spanning both, and a
   left-behind file/connection is harmless and self-corrects on retry --
   see _apply_hierarchy_slot's own docstring).

No hierarchy gate exists for RGD/ABM/RBM pulls or processing -- confirmed
safe by investigation (every hierarchy read in app/hierarchy_parser.py
degrades to None/{}/[] with no table present; app.doj_eligibility_service.
resolve_doj() -> None -> ACTIVE is existing, intentional behavior). Do not
add one here.
"""

import hashlib
from datetime import datetime
from pathlib import Path

import httpx
from loguru import logger
from postgrest.exceptions import APIError

from app.hierarchy_parser import refresh_hierarchy
from app.hierarchy_upload_service import (
    DIVISIONS,
    _clear_stale_copies as _clear_stale_hierarchy_copies,
    _stored_path_for as _hierarchy_stored_path_for,
    get_all_slot_states as get_all_hierarchy_slot_states,
    slot_id_for as hierarchy_slot_id_for,
    store_hierarchy_upload,
)
from app.manager_work_allocation_parser import parse_manager_work_allocation_report
from app.manager_work_allocation_rbm_service import process_rbm_report
from app.manager_work_allocation_service import process_manager_work_allocation_report
from app.profile_names_service import display_name_for, refresh_profile_name_cache
from app.rbac_state import current_profile
from app.review_sync_service import _is_newer_version, _parse_version, _sha256_of_file  # noqa: F401 (re-exported)
from app.supabase_client import get_supabase_client
from app.version import APP_VERSION
from app.work_distribution_parser import parse_work_distribution_report
from app.work_distribution_service import process_work_distribution_report
from app.work_distribution_upload_service import (
    ABM,
    RBM,
    RGD,
    REPORT_TYPE_LABELS,
    _clear_stale_copies as _clear_stale_report_copies,
    _stored_path_for as _report_stored_path_for,
    get_slot_state as get_report_slot_state,
    slot_id_for as report_slot_id_for,
    store_work_distribution_upload,
)
from app.workbook_connections import set_connection
from database.connection import get_session, utcnow
from database.models import HierarchyUploadSlot, SyncModuleCheck, SyncState, WorkDistributionUploadSlot

MODULE = "work_distribution"
BUCKET = "sync-uploads"
# The module_key app.workbook_connections/app.hierarchy_parser use for Work
# Distribution's own hierarchy dataset -- see app/hierarchy_parser.py's
# HIERARCHY_TABLES. Distinct from MODULE above (this file's own
# sync_manifest module column); same string value, different purpose.
_HIERARCHY_MODULE_KEY = "work_distribution"

REPORT_TYPES = (RGD, ABM, RBM)
RGD_SLOTS = tuple(report_slot_id_for(RGD, d) for d in DIVISIONS)
ABM_SLOTS = tuple(report_slot_id_for(ABM, d) for d in DIVISIONS)
RBM_SLOTS = tuple(report_slot_id_for(RBM, d) for d in DIVISIONS)
HIERARCHY_SLOTS = tuple(hierarchy_slot_id_for(d) for d in DIVISIONS)
ALL_SLOTS = RGD_SLOTS + ABM_SLOTS + RBM_SLOTS + HIERARCHY_SLOTS  # 12

_REPORT_TYPE_BY_SLOTS = {RGD: RGD_SLOTS, ABM: ABM_SLOTS, RBM: RBM_SLOTS}


def _slot_info(slot_id: str) -> dict:
    """{"kind": "report"|"hierarchy", "report_type": .. or None, "division": ..}
    -- the one place a slot_id is decoded back into what it's for, mirroring
    slot_id_for()'s own "one place a slot_id is built" convention."""
    if slot_id in HIERARCHY_SLOTS:
        for d in DIVISIONS:
            if slot_id == hierarchy_slot_id_for(d):
                return {"kind": "hierarchy", "report_type": None, "division": d}
    for report_type, slots in _REPORT_TYPE_BY_SLOTS.items():
        for d in DIVISIONS:
            if slot_id == report_slot_id_for(report_type, d):
                return {"kind": "report", "report_type": report_type, "division": d}
    raise KeyError(f"Unknown Work Distribution sync slot_id: {slot_id!r}")


def _slot_label(slot_id: str) -> str:
    info = _slot_info(slot_id)
    if info["kind"] == "hierarchy":
        return f"Hierarchy ({info['division']})"
    return f"{REPORT_TYPE_LABELS[info['report_type']]} ({info['division']})"


SLOT_LABELS = {slot_id: _slot_label(slot_id) for slot_id in ALL_SLOTS}

_NETWORK_EXCEPTIONS = (httpx.ConnectError, httpx.TimeoutException)


def _validate_rgd(path: str) -> dict:
    result = parse_work_distribution_report(path)
    return {"success": result["success"], "error": result["error"]}


def _validate_mwa(path: str) -> dict:
    result = parse_manager_work_allocation_report(path)
    return {"success": result["success"], "error": result["error"]}


PROCESS_FN = {
    **{s: _validate_rgd for s in RGD_SLOTS},
    **{s: _validate_mwa for s in ABM_SLOTS},
    **{s: _validate_mwa for s in RBM_SLOTS},
}


# --- Combined per-engine auto-run (RGD/ABM/RBM only) -----------------------

def _maybe_process_engine(report_type: str) -> None:
    """Gathers all 3 divisions' CURRENT retained files for `report_type`
    (never upload/pull history -- see module docstring) and, only if all 3
    are present on disk right now, re-parses them fresh and runs that
    engine's combined processing function exactly once. A no-op if any
    division's file isn't retained yet. Never raises -- a re-parse or
    processing failure here is logged and skipped, not surfaced as a
    pull failure (the slot(s) that triggered this call already synced
    successfully; this is a best-effort convenience layer on top of that,
    not a condition of the sync itself)."""
    states = [get_report_slot_state(report_type, d) for d in DIVISIONS]
    if not all(s["uploaded"] and s["file_path"] and Path(s["file_path"]).exists() for s in states):
        return

    try:
        combined = []
        if report_type == RGD:
            for state in states:
                parsed = parse_work_distribution_report(state["file_path"])
                if not parsed["success"]:
                    logger.warning(
                        f"Work Distribution sync: auto-run skipped for RGD Coverage -- "
                        f"retained file for a division failed to re-parse: {parsed.get('error')}"
                    )
                    return
                combined.extend(parsed["doctors"])
            process_work_distribution_report(combined)
        else:
            for state in states:
                parsed = parse_manager_work_allocation_report(state["file_path"])
                if not parsed["success"]:
                    logger.warning(
                        f"Work Distribution sync: auto-run skipped for {REPORT_TYPE_LABELS[report_type]} -- "
                        f"retained file for a division failed to re-parse: {parsed.get('error')}"
                    )
                    return
                combined.extend(parsed["records"])
            if report_type == ABM:
                process_manager_work_allocation_report(combined)
            else:
                process_rbm_report(combined)

        logger.info(
            f"Work Distribution sync: auto-ran {REPORT_TYPE_LABELS[report_type]} analysis "
            "(all 3 divisions' current retained files present)"
        )
    except Exception as exc:
        logger.error(f"Work Distribution sync: auto-run failed for {REPORT_TYPE_LABELS[report_type]}: {exc!r}")


# --- Upload path -------------------------------------------------------

def upload_and_sync(slot_id: str, source_path: str) -> dict:
    """The full upload path for any of the 12 slots -- process/validate
    (against the ORIGINAL picked path) first, an invalid file is never
    stored or synced, then retain locally and push to Storage + the
    manifest. Never fails the LOCAL half because of a cloud problem -- see
    app.review_sync_service.upload_and_sync, same posture.

    Deliberately does NOT auto-run a report engine or refresh_hierarchy()
    after a LOCAL upload -- both stay exactly what they are today, a
    separate manual action (the page's own Run Analysis / Refresh Hierarchy
    button). The auto-run behavior in this module is sync-pull-only (see
    module docstring) -- a local upload already has a human sitting at the
    keyboard who can click that button themselves, same as always."""
    info = _slot_info(slot_id)
    if info["kind"] == "hierarchy":
        return _upload_hierarchy_and_sync(info["division"], source_path)
    return _upload_report_and_sync(slot_id, info["report_type"], info["division"], source_path)


def _push_to_manifest(slot_id: str, stored_path: Path, original_filename: str) -> dict:
    """Shared push step for both upload paths below -- identical shape to
    app.inventory_sync_service.upload_and_sync's own push block. Returns
    {"synced": bool, "sync_error": str|None, "manifest_row": dict|None}."""
    profile = current_profile()
    if profile is None:
        return {"synced": False, "sync_error": "Not signed in -- file stored locally only.", "manifest_row": None}

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
        logger.warning(f"Work Distribution sync: could not reach Supabase while uploading '{slot_id}' -- stored locally only.")
        return {
            "synced": False,
            "sync_error": "Could not reach Supabase. This file is saved locally; it will not be visible to others until you're back online and re-upload.",
            "manifest_row": None,
        }
    except APIError as exc:
        logger.error(f"Work Distribution sync: manifest push failed for '{slot_id}': {exc!r}")
        return {"synced": False, "sync_error": f"Supabase rejected the sync push: {exc.message or exc.code}", "manifest_row": None}
    except Exception as exc:
        logger.error(f"Work Distribution sync: unexpected error pushing '{slot_id}': {exc!r}")
        return {"synced": False, "sync_error": "An unexpected error occurred while syncing this upload.", "manifest_row": None}

    now = utcnow()
    session = get_session()
    try:
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

    logger.info(f"Work Distribution sync: '{slot_id}' pushed to manifest (seq={manifest_row['seq']})")
    return {"synced": True, "sync_error": None, "manifest_row": manifest_row}


def _upload_report_and_sync(slot_id: str, report_type: str, division: str, source_path: str) -> dict:
    process_fn = PROCESS_FN[slot_id]
    result = process_fn(source_path)
    if not result["success"] or result["error"] is not None:
        result.setdefault("synced", False)
        result.setdefault("sync_error", None)
        return result

    stored_path = store_work_distribution_upload(report_type, division, source_path)

    push = _push_to_manifest(slot_id, stored_path, Path(source_path).name)
    if push["manifest_row"] is not None:
        session = get_session()
        try:
            slot_row = session.query(WorkDistributionUploadSlot).filter_by(slot_id=slot_id).first()
            if slot_row is not None:
                slot_row.uploaded_by = current_profile().id
                slot_row.only_on_this_machine = False
                session.commit()
        finally:
            session.close()

    result["synced"] = push["synced"]
    result["sync_error"] = push["sync_error"]
    return result


def _upload_hierarchy_and_sync(division: str, source_path: str) -> dict:
    slot_id = hierarchy_slot_id_for(division)
    stored_path = store_hierarchy_upload(division, source_path)
    # Repoint workbook_connections at the RETAINED copy, not the original
    # picked path -- exactly what a sync pull does (see _apply_hierarchy_slot),
    # so a local upload gets the same durability benefit (the original
    # external file can move/vanish without breaking the next
    # refresh_hierarchy()). refresh_hierarchy() itself is NOT called here --
    # stays a separate manual action, unchanged (see upload_and_sync's
    # docstring).
    set_connection(_HIERARCHY_MODULE_KEY, division, str(stored_path))

    push = _push_to_manifest(slot_id, stored_path, Path(source_path).name)
    if push["manifest_row"] is not None:
        session = get_session()
        try:
            slot_row = session.query(HierarchyUploadSlot).filter_by(slot_id=slot_id).first()
            if slot_row is not None:
                slot_row.uploaded_by = current_profile().id
                slot_row.only_on_this_machine = False
                session.commit()
        finally:
            session.close()

    return {"success": True, "error": None, "synced": push["synced"], "sync_error": push["sync_error"]}


def current_uploader_for_confirm(slot_id: str) -> tuple[str, str] | None:
    """(display_name, local_time_text) for whoever this machine currently
    believes uploaded `slot_id`, or None -- same shape as
    app.review_sync_service's own current_uploader_for_confirm."""
    from database.connection import to_local

    info = _slot_info(slot_id)
    session = get_session()
    try:
        if info["kind"] == "hierarchy":
            row = session.query(HierarchyUploadSlot).filter_by(slot_id=slot_id).first()
        else:
            row = session.query(WorkDistributionUploadSlot).filter_by(slot_id=slot_id).first()
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
    info = _slot_info(slot_id)
    profile = current_profile()
    session = get_session()
    try:
        if info["kind"] == "hierarchy":
            row = session.query(HierarchyUploadSlot).filter_by(slot_id=slot_id).first()
        else:
            row = session.query(WorkDistributionUploadSlot).filter_by(slot_id=slot_id).first()
    finally:
        session.close()
    return bool(row and row.uploaded_by and profile and row.uploaded_by != profile.id)


# --- Check (banner) ------------------------------------------------------

def _local_slot_states() -> dict:
    """{slot_id: state_dict} across all 12 slots, from BOTH local retention
    tables -- WorkDistributionUploadSlot (9) and HierarchyUploadSlot (3)."""
    from app.work_distribution_upload_service import get_all_slot_states as get_all_report_slot_states

    states = dict(get_all_report_slot_states())
    states.update(get_all_hierarchy_slot_states())
    return states


def check_for_updates() -> dict:
    """The lightweight check that drives the upload pages' banners -- same
    one-query-per-module shape as app.review_sync_service.check_for_updates
    and app.inventory_sync_service.check_for_updates, scoped to ALL_SLOTS
    (12). Never raises; {"ok": False, "reason": ...} on any failure, with
    no local writes."""
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
        logger.info("Work Distribution sync: check skipped -- Supabase unreachable.")
        return {"ok": False, "reason": "offline"}
    except (APIError, Exception) as exc:
        logger.warning(f"Work Distribution sync: check failed: {exc!r}")
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
        report_rows = {r.slot_id: r for r in session.query(WorkDistributionUploadSlot).all()}
        hierarchy_rows = {r.slot_id: r for r in session.query(HierarchyUploadSlot).all()}
    finally:
        session.close()

    changed = []
    only_local = []
    for slot_id in ALL_SLOTS:
        remote = latest_by_slot.get(slot_id)
        local_row = report_rows.get(slot_id) or hierarchy_rows.get(slot_id)
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
        for slot_id, row in {**report_rows, **hierarchy_rows}.items():
            if not row.file_path:
                continue
            row.only_on_this_machine = slot_id in only_local_set
        session.commit()
    finally:
        session.close()

    states = _local_slot_states()
    filled = [sid for sid, st in states.items() if st["uploaded"]]

    return {
        "ok": True,
        "changed": changed,
        "only_local": only_local,
        "filled_count": len(filled),
        "total_count": len(states),
    }


# --- Apply (pull) ---------------------------------------------------------

def _apply_hierarchy_slot(slot_id: str, division: str, manifest_row: dict, file_bytes: bytes) -> tuple[bool, str | None]:
    """Writes the downloaded+hash-verified hierarchy file to this
    division's retained path, repoints workbook_connections at it (exactly
    what manual browse+connect does today -- see
    app/hierarchy_upload_service.py), then calls refresh_hierarchy() so the
    combined table actually picks up the change (refresh_hierarchy() itself
    is never modified).

    Returns (ok, failure_reason). On failure, the file has already been
    written to disk and workbook_connections has already been repointed --
    neither is rolled back (no cross-table transaction spans both in this
    codebase, and both are harmless left in place: the next successful
    refresh_hierarchy() -- retried via this same pull staying "pending", or
    triggered manually -- reads whatever is currently connected regardless
    of when it was connected). The caller (apply_pending_updates) uses
    (ok, reason) only to decide whether to advance the sync marker /
    update HierarchyUploadSlot -- it never happens on failure, so the slot
    stays visibly "pending" and is retried on the next check, same posture
    as a hash mismatch or processing failure in every other sync module."""
    extension = Path(manifest_row["filename"]).suffix.lower()
    _clear_stale_hierarchy_copies(slot_id)
    stored_path = _hierarchy_stored_path_for(slot_id, extension)
    try:
        with open(stored_path, "wb") as f:
            f.write(file_bytes)
    except OSError as exc:
        return False, f"Could not write the file locally: {exc}"

    try:
        set_connection(_HIERARCHY_MODULE_KEY, division, str(stored_path))
        refresh_hierarchy(_HIERARCHY_MODULE_KEY)
    except Exception as exc:
        logger.error(
            f"Work Distribution sync: '{slot_id}' hierarchy refresh failed after a verified download: "
            f"{exc!r} -- marker NOT advanced, safe to retry."
        )
        return False, f"Downloaded file did not refresh the hierarchy successfully: {exc!r}"

    return True, None


def apply_pending_updates(slot_ids: list[str], progress_cb=None) -> dict:
    """Downloads, verifies, and applies every slot in `slot_ids` (the
    `changed` list from check_for_updates()). No slot-ordering dependency
    (confirmed no gate -- see module docstring): every slot is independent.

    After every slot in this call is processed, re-checks all 3 divisions'
    CURRENT retained files for each of RGD/ABM/RBM and auto-runs that
    engine's combined analysis if all 3 are now present -- see
    _maybe_process_engine(). This runs for all three engines regardless of
    which slots were actually in `slot_ids`, so it stays correct even if,
    say, only a hierarchy slot was pulled while RGD's 3 files were already
    sitting in retention from earlier uploads.

    Returns {"applied": [...], "failed": [{"slot_id","reason"}, ...],
    "version_blocked": [...]} -- same shape as
    app.review_sync_service.apply_pending_updates."""
    applied, failed, version_blocked = [], [], []

    try:
        client = get_supabase_client()
    except RuntimeError as exc:
        return {
            "applied": [],
            "failed": [{"slot_id": s, "reason": str(exc)} for s in slot_ids],
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

    for i, slot_id in enumerate(slot_ids):
        if progress_cb:
            progress_cb(int(i / len(slot_ids) * 100), f"Applying {SLOT_LABELS[slot_id]}...")

        info = _slot_info(slot_id)
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
                f"Work Distribution sync: '{slot_id}' skipped -- manifest row was written by app_version "
                f"{manifest_row['app_version']!r}, newer than this build ({APP_VERSION!r})."
            )
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
                f"Work Distribution sync: HASH MISMATCH for '{slot_id}' (manifest seq={manifest_row['seq']}) -- "
                f"expected {manifest_row['sha256']}, got {actual_hash}. File rejected, marker not advanced."
            )
            continue

        now = utcnow()

        if info["kind"] == "hierarchy":
            ok, reason = _apply_hierarchy_slot(slot_id, info["division"], manifest_row, file_bytes)
            if not ok:
                failed.append({"slot_id": slot_id, "reason": reason})
                continue

            session = get_session()
            try:
                slot_row = session.query(HierarchyUploadSlot).filter_by(slot_id=slot_id).first()
                if slot_row is None:
                    slot_row = HierarchyUploadSlot(slot_id=slot_id)
                    session.add(slot_row)
                slot_row.filename = manifest_row["filename"]
                slot_row.file_path = str(_hierarchy_stored_path_for(slot_id, Path(manifest_row["filename"]).suffix.lower()))
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

        else:
            try:
                extension = Path(manifest_row["filename"]).suffix.lower()
                _clear_stale_report_copies(slot_id)
                stored_path = _report_stored_path_for(slot_id, extension)
                with open(stored_path, "wb") as f:
                    f.write(file_bytes)
            except OSError as exc:
                failed.append({"slot_id": slot_id, "reason": f"Could not write the file locally: {exc}"})
                continue

            try:
                process_result = PROCESS_FN[slot_id](str(stored_path))
            except Exception as exc:
                failed.append({"slot_id": slot_id, "reason": f"Applied file failed processing: {exc!r}"})
                logger.error(f"Work Distribution sync: '{slot_id}' processing raised after a verified download: {exc!r}")
                continue

            if not process_result["success"] or process_result["error"] is not None:
                failed.append({"slot_id": slot_id, "reason": f"Downloaded file did not pass validation: {process_result.get('error')}"})
                continue

            session = get_session()
            try:
                slot_row = session.query(WorkDistributionUploadSlot).filter_by(slot_id=slot_id).first()
                if slot_row is None:
                    slot_row = WorkDistributionUploadSlot(slot_id=slot_id)
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
        logger.info(f"Work Distribution sync: applied '{slot_id}' (seq={manifest_row['seq']}, hash verified)")

    for report_type in REPORT_TYPES:
        _maybe_process_engine(report_type)

    if progress_cb:
        progress_cb(100, "Done")

    return {"applied": applied, "failed": failed, "version_blocked": version_blocked}


def _parse_manifest_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None)
