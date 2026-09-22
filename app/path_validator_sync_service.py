"""Path Validator sync -- extends the exact mechanism
app/review_sync_service.py proved and app/inventory_sync_service.py /
app/work_distribution_sync_service.py already reused (same sync_manifest
table, same sync-uploads bucket, same content-addressed storage path, same
hash verification) to Path Validator's 6 slots: module="path_validator".

3 slots are the daily call report, one per division -- see
app/path_validator_upload_service.py. 3 slots are the Organization Data
hierarchy workbook, one per division -- see app/hierarchy_upload_service.py
(the shared, now module-scoped retention layer also used by Work
Distribution; module_key here is "employee_module", NOT "path_validator" --
that is the module_key app.hierarchy_parser.HIERARCHY_TABLES and
app.workbook_connections already use for this exact table everywhere else
in the app (ui/organization_data_page.py, rules/same_location.py's own
PATH_VALIDATOR_MODULE_KEY). "path_validator" only ever appears as this
file's sync_manifest `module` column value -- never confuse the two.

Two things are genuinely new here, beyond what Work Distribution's sync
already established:

1. THE HARD HIERARCHY GATE (see hierarchy_gate_open() / module docstring
   in rules/same_location.py). Unlike Work Distribution, where a missing
   hierarchy degrades gracefully (resolve_doj() -> None -> ACTIVE), Path
   Validator's own same_location rule has a hard, silent-zero failure
   mode: get_all_designations() returns {} with no hierarchy table, every
   employee_code fails the ANALYZABLE_DESIGNATIONS check, and analysis
   silently produces zero findings for everyone. This gate blocks BOTH
   the manual Run Analysis button (see ui/operations_page.py) and this
   module's own sync-triggered auto-run -- same treatment as Inventory's
   Sales-before-Inventory-Report gate, just checked before compute instead
   of before a specific slot's apply.

2. THE COMBINED-ANALYSIS AUTO-RUN mirrors Work Distribution's own
   _maybe_process_engine() exactly (same full-replace-needs-all-3-divisions
   constraint as RGD/ABM/RBM -- Path Validator's own daily-report upload
   is ALSO a 3-division-combine-then-run workflow, see
   ui/operations_page.py's own DIVISION_SLOTS/_on_run_analysis_clicked).
   The one difference: raw_visits/import_history are ADDITIVE, never
   delete-then-rebuild (see database/import_service.py's own docstring) --
   so a sync-triggered auto-run calls the EXACT SAME save_import() +
   set_active_import() + calculate_metrics() + evaluate_same_location() +
   evaluate_hours_worked() pipeline ui/operations_page.py's manual Run
   Analysis button already calls, appending a new import_id and never
   deleting or trimming any prior one. No cleanup/trimming/archival logic
   is added here, or should ever be, per that table's own intentional
   design.

Email sending is NEVER reachable from anywhere in this module. Manager
notifications are exclusively a manual, permission-gated action
(can_send_emails("employee_module"), see ui/findings_page.py's own Send
Emails button) -- this file contains no import of, or call into,
app.notification_service, by design.
"""

import hashlib
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd
from loguru import logger
from postgrest.exceptions import APIError

from app.config import REQUIRED_COLUMNS
from app.coordinates import parse_coordinates
from app.hierarchy_parser import get_all_designations, refresh_hierarchy
from app.hierarchy_upload_service import (
    DIVISIONS,
    _clear_stale_copies as _clear_stale_hierarchy_copies,
    _stored_path_for as _hierarchy_stored_path_for,
    get_all_slot_states as get_all_hierarchy_slot_states,
    slot_id_for as hierarchy_slot_id_for,
    store_hierarchy_upload,
)
from app.metrics import calculate_metrics
from app.path_validator_upload_service import (
    _clear_stale_copies as _clear_stale_report_copies,
    _stored_path_for as _report_stored_path_for,
    get_all_slot_states as get_all_report_slot_states,
    get_slot_state as get_report_slot_state,
    slot_id_for as report_slot_id_for,
    store_daily_report_upload,
)
from app.profile_names_service import display_name_for, refresh_profile_name_cache
from app.rbac_state import current_profile
from app.review_sync_service import _is_newer_version, _parse_version, _sha256_of_file  # noqa: F401 (re-exported)
from app.session_state import set_active_import
from app.supabase_client import get_supabase_client
from app.version import APP_VERSION
from app.workbook_connections import set_connection
from database.connection import get_session, utcnow
from database.import_service import save_import
from database.models import HierarchyUploadSlot, PathValidatorUploadSlot, SyncModuleCheck, SyncState
from rules.hours_worked import evaluate as evaluate_hours_worked
from rules.same_location import evaluate as evaluate_same_location

MODULE = "path_validator"
BUCKET = "sync-uploads"
# The module_key app.hierarchy_parser/app.workbook_connections/
# rules.same_location already use for Path Validator's own hierarchy
# dataset (see app/hierarchy_parser.py's HIERARCHY_TABLES:
# "employee_module" -> "employee_hierarchy_path_validator"). Deliberately
# DIFFERENT from MODULE above -- confirmed against the actual codebase
# rather than assumed, since the two strings could easily be mixed up.
_HIERARCHY_MODULE_KEY = "employee_module"

DAILY_REPORT_SLOTS = tuple(report_slot_id_for(d) for d in DIVISIONS)
HIERARCHY_SLOTS = tuple(hierarchy_slot_id_for(d) for d in DIVISIONS)
ALL_SLOTS = DAILY_REPORT_SLOTS + HIERARCHY_SLOTS  # 6

_NETWORK_EXCEPTIONS = (httpx.ConnectError, httpx.TimeoutException)


def _slot_info(slot_id: str) -> dict:
    """{"kind": "report"|"hierarchy", "division": ..} -- the one place a
    slot_id is decoded back into what it's for."""
    for d in DIVISIONS:
        if slot_id == report_slot_id_for(d):
            return {"kind": "report", "division": d}
        if slot_id == hierarchy_slot_id_for(d):
            return {"kind": "hierarchy", "division": d}
    raise KeyError(f"Unknown Path Validator sync slot_id: {slot_id!r}")


def _slot_label(slot_id: str) -> str:
    info = _slot_info(slot_id)
    label = "Daily Call Report" if info["kind"] == "report" else "Hierarchy"
    return f"{label} ({info['division']})"


SLOT_LABELS = {slot_id: _slot_label(slot_id) for slot_id in ALL_SLOTS}


# --- The hard hierarchy gate ------------------------------------------------

def hierarchy_gate_open() -> bool:
    """True if employee_hierarchy_path_validator has at least one row --
    see module docstring for why this is a HARD gate here (silent-zero
    failure mode), unlike Work Distribution's graceful degradation.
    get_all_designations() itself already returns {} for both "table
    doesn't exist" and "table exists but is empty" -- no need to
    distinguish those two cases, both mean the same thing to a caller."""
    return bool(get_all_designations(_HIERARCHY_MODULE_KEY))


def _validate_daily_report(path: str) -> dict:
    """Mirrors ui/operations_page.py's own per-division validation
    exactly (read + required-columns check + coordinate parsing) --
    without ever calling save_import(). Used both by upload_and_sync's
    local-upload path and by apply_pending_updates' per-slot pull path;
    the actual combined save_import()/set_active_import()/rule-evaluation
    run only happens in _maybe_process_analysis(), once all 3 divisions'
    CURRENT files are present (see module docstring)."""
    try:
        df = pd.read_excel(path)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    missing_columns = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_columns:
        return {"success": False, "error": f"Missing required columns: {missing_columns}"}

    try:
        parse_coordinates(df)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    return {"success": True, "error": None}


def _maybe_process_analysis() -> None:
    """Gathers all 3 divisions' CURRENT retained daily report files (never
    upload/pull history -- same principle as Work Distribution's
    _maybe_process_engine) and, only if all 3 are present AND the hard
    hierarchy gate is open, re-parses them fresh and runs the exact same
    pipeline ui/operations_page.py's manual Run Analysis button runs:
    save_import() -> set_active_import() -> calculate_metrics() ->
    evaluate_same_location() -> evaluate_hours_worked(). Appends a new
    import_id; never deletes or trims any prior one (see module
    docstring). Never raises -- a re-parse or pipeline failure here is
    logged and skipped, not surfaced as a pull failure (the slot(s) that
    triggered this call already synced successfully)."""
    states = [get_report_slot_state(d) for d in DIVISIONS]
    if not all(s["uploaded"] and s["file_path"] and Path(s["file_path"]).exists() for s in states):
        return

    if not hierarchy_gate_open():
        logger.warning(
            "Path Validator sync: auto-run BLOCKED -- employee_hierarchy_path_validator has zero rows "
            "(hard gate). All 3 divisions' daily reports are retained and ready; analysis will run "
            "automatically once Organization Data is synced or refreshed."
        )
        return

    try:
        dfs = []
        file_names = []
        for state in states:
            df = pd.read_excel(state["file_path"])
            parse_coordinates(df)
            dfs.append(df)
            file_names.append(state["filename"])

        combined = pd.concat(dfs, ignore_index=True)
        file_name = f"Combined ({', '.join(file_names)})"

        stats = save_import(combined, file_name)
        import_id = stats["import_id"]
        set_active_import(import_id)
        calculate_metrics(import_id)
        evaluate_same_location(import_id)
        evaluate_hours_worked(import_id)

        logger.info(
            f"Path Validator sync: auto-ran analysis (import_id={import_id}, "
            f"{stats['rows_imported']} row(s), {stats['duplicates_removed']} duplicate(s) removed)"
        )
    except Exception as exc:
        logger.error(f"Path Validator sync: auto-run failed: {exc!r}")


# --- Upload path -------------------------------------------------------

def upload_and_sync(slot_id: str, source_path: str) -> dict:
    """The full upload path for any of the 6 slots -- process/validate
    (against the ORIGINAL picked path) first, an invalid file is never
    stored or synced, then retain locally and push to Storage + the
    manifest.

    Deliberately does NOT auto-run analysis or refresh_hierarchy() after a
    LOCAL upload -- both stay exactly what they are today, a separate
    manual action (Run Analysis / Refresh Organization Data). The
    auto-run behavior in this module is sync-pull-only (see module
    docstring)."""
    info = _slot_info(slot_id)
    if info["kind"] == "hierarchy":
        return _upload_hierarchy_and_sync(info["division"], source_path)
    return _upload_report_and_sync(slot_id, info["division"], source_path)


def _push_to_manifest(slot_id: str, stored_path: Path, original_filename: str) -> dict:
    """Shared push step for both upload paths below -- identical shape to
    app.work_distribution_sync_service._push_to_manifest. Returns
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
        logger.warning(f"Path Validator sync: could not reach Supabase while uploading '{slot_id}' -- stored locally only.")
        return {
            "synced": False,
            "sync_error": "Could not reach Supabase. This file is saved locally; it will not be visible to others until you're back online and re-upload.",
            "manifest_row": None,
        }
    except APIError as exc:
        logger.error(f"Path Validator sync: manifest push failed for '{slot_id}': {exc!r}")
        return {"synced": False, "sync_error": f"Supabase rejected the sync push: {exc.message or exc.code}", "manifest_row": None}
    except Exception as exc:
        logger.error(f"Path Validator sync: unexpected error pushing '{slot_id}': {exc!r}")
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

    logger.info(f"Path Validator sync: '{slot_id}' pushed to manifest (seq={manifest_row['seq']})")
    return {"synced": True, "sync_error": None, "manifest_row": manifest_row}


def _upload_report_and_sync(slot_id: str, division: str, source_path: str) -> dict:
    result = _validate_daily_report(source_path)
    if not result["success"] or result["error"] is not None:
        result.setdefault("synced", False)
        result.setdefault("sync_error", None)
        return result

    stored_path = store_daily_report_upload(division, source_path)

    push = _push_to_manifest(slot_id, stored_path, Path(source_path).name)
    if push["manifest_row"] is not None:
        session = get_session()
        try:
            slot_row = session.query(PathValidatorUploadSlot).filter_by(slot_id=slot_id).first()
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
    stored_path = store_hierarchy_upload(_HIERARCHY_MODULE_KEY, division, source_path)
    # Repoints workbook_connections at the RETAINED copy, not the original
    # picked path -- same reasoning as
    # app.work_distribution_sync_service._upload_hierarchy_and_sync.
    # refresh_hierarchy() is NOT called here -- stays a separate manual
    # action ("Refresh Organization Data"), unchanged.
    set_connection(_HIERARCHY_MODULE_KEY, division, str(stored_path))

    push = _push_to_manifest(slot_id, stored_path, Path(source_path).name)
    if push["manifest_row"] is not None:
        session = get_session()
        try:
            slot_row = session.query(HierarchyUploadSlot).filter_by(
                module_key=_HIERARCHY_MODULE_KEY, slot_id=slot_id
            ).first()
            if slot_row is not None:
                slot_row.uploaded_by = current_profile().id
                slot_row.only_on_this_machine = False
                session.commit()
        finally:
            session.close()

    return {"success": True, "error": None, "synced": push["synced"], "sync_error": push["sync_error"]}


# --- Check (banner) ------------------------------------------------------

def _local_slot_states() -> dict:
    """{slot_id: state_dict} across all 6 slots, from BOTH local retention
    tables -- PathValidatorUploadSlot (3) and HierarchyUploadSlot (3,
    filtered to module_key="employee_module")."""
    states = dict(get_all_report_slot_states())
    states.update(get_all_hierarchy_slot_states(_HIERARCHY_MODULE_KEY))
    return states


def check_for_updates() -> dict:
    """Same one-query-per-module shape as every other sync module's
    check_for_updates, scoped to ALL_SLOTS (6). Never raises;
    {"ok": False, "reason": ...} on any failure, with no local writes."""
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
        logger.info("Path Validator sync: check skipped -- Supabase unreachable.")
        return {"ok": False, "reason": "offline"}
    except (APIError, Exception) as exc:
        logger.warning(f"Path Validator sync: check failed: {exc!r}")
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
        report_rows = {r.slot_id: r for r in session.query(PathValidatorUploadSlot).all()}
        hierarchy_rows = {
            r.slot_id: r
            for r in session.query(HierarchyUploadSlot).filter_by(module_key=_HIERARCHY_MODULE_KEY).all()
        }
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
    division's retained path, repoints workbook_connections at it, then
    calls refresh_hierarchy() -- mirrors
    app.work_distribution_sync_service._apply_hierarchy_slot exactly,
    scoped to module_key="employee_module" instead of "work_distribution".
    Same non-rollback failure posture -- see that function's own
    docstring for the full reasoning."""
    extension = Path(manifest_row["filename"]).suffix.lower()
    _clear_stale_hierarchy_copies(_HIERARCHY_MODULE_KEY, slot_id)
    stored_path = _hierarchy_stored_path_for(_HIERARCHY_MODULE_KEY, slot_id, extension)
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
            f"Path Validator sync: '{slot_id}' hierarchy refresh failed after a verified download: "
            f"{exc!r} -- marker NOT advanced, safe to retry."
        )
        return False, f"Downloaded file did not refresh the hierarchy successfully: {exc!r}"

    return True, None


def apply_pending_updates(slot_ids: list[str], progress_cb=None) -> dict:
    """Downloads, verifies, and applies every slot in `slot_ids`. No
    slot-ordering dependency between daily-report and hierarchy slots.

    After every slot in this call is processed, calls
    _maybe_process_analysis() once -- it internally re-checks all 3
    CURRENT retained daily-report files and the hard hierarchy gate fresh,
    so it stays correct regardless of which slots were actually in
    `slot_ids` (e.g. only a hierarchy slot was pulled while all 3 daily
    reports were already retained from earlier uploads).

    Returns {"applied": [...], "failed": [{"slot_id","reason"}, ...],
    "version_blocked": [...]}."""
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
                f"Path Validator sync: '{slot_id}' skipped -- manifest row was written by app_version "
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
                f"Path Validator sync: HASH MISMATCH for '{slot_id}' (manifest seq={manifest_row['seq']}) -- "
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
                slot_row = session.query(HierarchyUploadSlot).filter_by(
                    module_key=_HIERARCHY_MODULE_KEY, slot_id=slot_id
                ).first()
                if slot_row is None:
                    slot_row = HierarchyUploadSlot(module_key=_HIERARCHY_MODULE_KEY, slot_id=slot_id)
                    session.add(slot_row)
                slot_row.filename = manifest_row["filename"]
                slot_row.file_path = str(
                    _hierarchy_stored_path_for(_HIERARCHY_MODULE_KEY, slot_id, Path(manifest_row["filename"]).suffix.lower())
                )
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
                process_result = _validate_daily_report(str(stored_path))
            except Exception as exc:
                failed.append({"slot_id": slot_id, "reason": f"Applied file failed processing: {exc!r}"})
                logger.error(f"Path Validator sync: '{slot_id}' processing raised after a verified download: {exc!r}")
                continue

            if not process_result["success"] or process_result["error"] is not None:
                failed.append({"slot_id": slot_id, "reason": f"Downloaded file did not pass validation: {process_result.get('error')}"})
                continue

            session = get_session()
            try:
                slot_row = session.query(PathValidatorUploadSlot).filter_by(slot_id=slot_id).first()
                if slot_row is None:
                    slot_row = PathValidatorUploadSlot(slot_id=slot_id)
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
        logger.info(f"Path Validator sync: applied '{slot_id}' (seq={manifest_row['seq']}, hash verified)")

    _maybe_process_analysis()

    if progress_cb:
        progress_cb(100, "Done")

    return {"applied": applied, "failed": failed, "version_blocked": version_blocked}


def _parse_manifest_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None)
