"""Review System sync -- the first vertical slice of docs/SYNC_DESIGN.md,
scoped to this one module only.

Mechanism (see supabase/migrations/0023_sync_manifest.sql and
docs/SYNC_DESIGN.md): an upload goes to the `sync-uploads` Storage bucket,
then gets one append-only row in `sync_manifest` (module='review_system',
slot_key=<slot_id>). Every other client polls the manifest, downloads
anything newer than what it has already applied, verifies the file's
sha256 against what the manifest claims, and only then writes it locally
and updates review_file_slots -- exactly as if the user had uploaded it
themselves through the existing local path.

Three local-only tables carry this machine's own sync bookkeeping and are
never themselves synced: SyncState (what's been applied, per slot),
SyncModuleCheck (when this machine last successfully checked, for the
offline staleness indicator), and ProfileNameCache (uuid -> display name,
refreshed opportunistically on every successful check).

Offline posture throughout: Supabase unreachable must never block local
upload or local viewing -- every function here that talks to the network
catches connectivity failures and returns a clear, non-raising result the
UI can show as a staleness indicator, not an error dialog.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

import httpx
from loguru import logger
from postgrest.exceptions import APIError

from app.config import REVIEW_UPLOADS_DIR
from app.profile_names_service import display_name_for, refresh_profile_name_cache
from app.rbac_state import current_profile
from app.review_schemas import get_slot_def
from app.review_upload_service import _clear_stale_copies, _stored_path_for
from app.review_validation import validate_review_file
from app.supabase_client import get_supabase_client
from app.version import APP_VERSION
from database.connection import get_session, utcnow
from database.models import ReviewFileSlot, SyncModuleCheck, SyncState

MODULE = "review_system"
BUCKET = "sync-uploads"

# Network/connectivity failures every function below treats the same way:
# never raise, return a clear "couldn't reach Supabase" outcome instead.
_NETWORK_EXCEPTIONS = (httpx.ConnectError, httpx.TimeoutException)


def _sha256_of_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _parse_version(version: str) -> tuple:
    """Leading dotted-numeric prefix only (e.g. "1.3.1-dev.0.2" -> (1, 3,
    1)) -- good enough for "is this build older than the one that wrote
    this row", not a full semver parser. A build with a malformed/missing
    version string sorts as (0,), i.e. never blocks anything (fail open on
    a parsing problem; the actual gate is `manifest_row.app_version >
    APP_VERSION`, and refusing to apply something because OUR OWN version
    string was unparseable would be a worse failure mode than the skew
    check this exists to catch)."""
    parts = []
    for piece in version.split("-")[0].split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts) or (0,)


def _is_newer_version(remote_version: str, local_version: str) -> bool:
    return _parse_version(remote_version) > _parse_version(local_version)


# --- Profile name resolution ------------------------------------------------
# display_name_for()/refresh_profile_name_cache() now live in
# app/profile_names_service.py (extracted for Phase 2 of email authority,
# which reuses the exact same mechanism) -- imported above, re-exported
# from this module's own namespace so nothing that already imports
# display_name_for from here needs to change.


# --- Upload path -------------------------------------------------------

def upload_and_sync(slot_id: str, source_path: str) -> dict:
    """The full upload path, in the order that matters: local
    store+validate (existing app.review_upload_service behavior, unchanged)
    -> sha256 of the stored file -> upload the object to Storage -> insert
    the manifest row. A manifest row is only ever inserted after the
    object it references is confirmed uploaded -- failing between the two
    leaves an orphaned object (harmless, cleaned up by nothing today, by
    design -- see docs/SYNC_DESIGN.md's no-retention invariant) rather than
    a manifest row with nothing behind it.

    Never fails the LOCAL half of the upload because of a cloud problem:
    Supabase unreachable stores the file locally exactly as it always has,
    leaves only_on_this_machine=True, and reports that in the returned
    dict's `synced`/`sync_error` keys rather than raising -- the caller
    (ui/review_uploads_page.py) already shows the validation outcome
    unconditionally; it additionally checks `synced` to say so.

    Returns the same dict validate_review_file returns, plus `synced` and
    `sync_error`."""
    # --- Step 1: local store + validate, unchanged from today ---------
    from app.review_upload_service import upload_review_file

    result = upload_review_file(slot_id, source_path)
    stored_path = Path(result.get("file_path") or _stored_path_for(slot_id, Path(source_path).suffix.lower()))

    profile = current_profile()
    if profile is None:
        # Should never happen (this page is only reachable signed-in), but
        # a sync push with no known uploader is worse than skipping it.
        result["synced"] = False
        result["sync_error"] = "Not signed in -- file stored locally only."
        return result

    original_filename = Path(source_path).name
    extension = stored_path.suffix.lower()

    try:
        client = get_supabase_client()
        sha256 = _sha256_of_file(stored_path)
        storage_path = f"{MODULE}/{slot_id}/{sha256}{extension}"

        # --- Step 2: upload the object -------------------------------
        with open(stored_path, "rb") as f:
            file_bytes = f.read()
        client.storage.from_(BUCKET).upload(
            storage_path, file_bytes, {"content-type": "application/octet-stream", "upsert": "true"}
        )

        # --- Step 3: insert the manifest row (only after the object is
        # confirmed uploaded) ------------------------------------------
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
        logger.warning(f"Review sync: could not reach Supabase while uploading '{slot_id}' -- stored locally only.")
        result["synced"] = False
        result["sync_error"] = "Could not reach Supabase. This file is saved locally; it will not be visible to others until you're back online and re-upload."
        return result
    except APIError as exc:
        logger.error(f"Review sync: manifest push failed for '{slot_id}': {exc!r}")
        result["synced"] = False
        result["sync_error"] = f"Supabase rejected the sync push: {exc.message or exc.code}"
        return result
    except Exception as exc:
        logger.error(f"Review sync: unexpected error pushing '{slot_id}': {exc!r}")
        result["synced"] = False
        result["sync_error"] = "An unexpected error occurred while syncing this upload."
        return result

    # --- Advance this machine's own markers so the uploader never sees
    # a banner for their own upload ------------------------------------
    now = utcnow()
    session = get_session()
    try:
        slot_row = session.query(ReviewFileSlot).filter_by(slot_id=slot_id).first()
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

    logger.info(f"Review sync: '{slot_id}' pushed to manifest (seq={manifest_row['seq']})")
    result["synced"] = True
    result["sync_error"] = None
    return result


def current_uploader_for_confirm(slot_id: str) -> tuple[str, str] | None:
    """(display_name, local_time_text) for whoever this machine currently
    believes uploaded `slot_id`, or None if the slot is empty or the
    uploader is unknown -- purely local, no network call, so the "you're
    about to replace someone else's file" confirm dialog never blocks on
    connectivity. May be stale if this machine hasn't checked recently;
    that's an accepted limit of a friendly heads-up, not a live lock."""
    from database.connection import to_local

    session = get_session()
    try:
        row = session.query(ReviewFileSlot).filter_by(slot_id=slot_id).first()
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
        row = session.query(ReviewFileSlot).filter_by(slot_id=slot_id).first()
    finally:
        session.close()
    return bool(row and row.uploaded_by and profile and row.uploaded_by != profile.id)


# --- Check (banner) ------------------------------------------------------

def check_for_updates() -> dict:
    """The lightweight check that drives the banner -- fires on module
    entry and the explicit Refresh button, never a timer. One query:
    latest (seq, uploaded_by) per slot_key for this module. Nothing
    downloads and nothing is written here; that only happens if the
    caller then calls apply_pending_updates().

    Returns:
        {"ok": True, "changed": [{"slot_id", "is_first_fill",
            "is_replacement", "uploader_name", "remote_seq"}, ...],
         "only_local": [slot_id, ...],   # local file, no manifest row at all
         "filled_count": int, "total_count": 12, "missing": [slot_id, ...]}
        or {"ok": False, "reason": "offline"} -- never raises. On
        "ok": False, the caller must leave every existing indicator (the
        banner, only_on_this_machine flags) in whatever state they were
        already in -- this function makes no local writes on failure.
    """
    from app.review_schemas import REVIEW_FILE_SLOTS
    from app.review_upload_service import get_all_slot_states

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
        logger.info("Review sync: check skipped -- Supabase unreachable.")
        return {"ok": False, "reason": "offline"}
    except (APIError, Exception) as exc:
        logger.warning(f"Review sync: check failed: {exc!r}")
        return {"ok": False, "reason": "error"}

    latest_by_slot: dict[str, dict] = {}
    for row in response.data or []:
        latest_by_slot.setdefault(row["slot_key"], row)  # first hit per slot_key = highest seq (already ordered)

    # Refresh the name cache BEFORE resolving uploader_name below -- on a
    # fresh machine's first-ever check the cache is empty, and doing this
    # after building `changed` would show "User <uuid8>" for every
    # co-worker's upload instead of their real name.
    refresh_profile_name_cache(client)

    session = get_session()
    try:
        local_applied = {
            s.slot_key: s.applied_seq
            for s in session.query(SyncState).filter_by(module=MODULE).all()
        }
        slot_rows = {r.slot_id: r for r in session.query(ReviewFileSlot).all()}
    finally:
        session.close()

    changed = []
    only_local = []
    for slot in REVIEW_FILE_SLOTS:
        slot_id = slot.slot_id
        remote = latest_by_slot.get(slot_id)
        local_row = slot_rows.get(slot_id)
        has_local_file = bool(local_row and local_row.file_path)

        if remote is None:
            if has_local_file:
                only_local.append(slot_id)
            continue

        applied_seq = local_applied.get(slot_id)
        if applied_seq is not None and remote["seq"] <= applied_seq:
            continue  # already fully applied

        changed.append(
            {
                "slot_id": slot_id,
                "is_first_fill": applied_seq is None,
                "is_replacement": applied_seq is not None,
                "uploader_name": display_name_for(remote.get("uploaded_by")),
                "remote_seq": remote["seq"],
            }
        )

    # Only touch local state (the check-timestamp marker, only_on_this_machine
    # flags, the name cache) once the check has genuinely succeeded.
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
    missing = [sid for sid, st in states.items() if not st["uploaded"]]

    return {
        "ok": True,
        "changed": changed,
        "only_local": only_local,
        "filled_count": len(filled),
        "total_count": len(states),
        "missing": missing,
    }


# --- Apply (on banner button click) ---------------------------------------

def _affected_report_divisions(slot_id: str) -> list[tuple[str, str]]:
    """[(report_type_label, division), ...] whose already-generated output
    depends on `slot_id` -- reuses each report's own prerequisites_ready()
    building blocks (the single source of truth for these mappings; see
    app.review_opus_service.REQUIRED_SLOTS_FOR_OPUS and friends) rather
    than re-deriving the dependency list here."""
    from app.review_coverage_service import _avg_calls_slot_id, _visits_support_slot_id
    from app.review_opus_service import DIVISIONS, REQUIRED_SLOTS_FOR_OPUS, _secondary_sales_slot_id

    affected = []
    for division in DIVISIONS:
        opus_slots = set(REQUIRED_SLOTS_FOR_OPUS) | {_secondary_sales_slot_id(division)}
        if slot_id in opus_slots:
            affected.append(("Opus Summary", division))

        # Same upload slot feeds both Coverage Summary and RGD Visit and
        # Support for a division (see app.review_schemas._visits_support_slot).
        if slot_id in (_avg_calls_slot_id(division), _visits_support_slot_id(division)):
            affected.append(("Coverage Summary", division))
        if slot_id == _visits_support_slot_id(division):
            affected.append(("RGD VISIT AND SUPPORT", division))
    return affected


def _clear_stale_generated_reports(slot_id: str) -> list[str]:
    """Deletes the generated .xlsx + JSON preview sidecar for any
    (report type, division) whose inputs include `slot_id` -- called only
    for a REPLACING pull (a first fill can't make an already-generated
    report stale; nothing could have been generated from a missing input).
    Both files' existence-on-disk IS the generated state (see
    app.review_output_service's module docstring: "stateless by design") --
    deleting them is the entire fix; ui/review_file_preview_page.py's
    existing _render() already shows "hasn't been generated yet" the
    moment get_generated_fn() returns None, with no code change needed
    there. Never raises -- a missing file is not an error here."""
    from app.review_coverage_service import generated_coverage_preview_path, generated_coverage_summary_path
    from app.review_opus_service import generated_opus_preview_path, generated_opus_summary_path
    from app.review_rgd_service import generated_rgd_path, generated_rgd_preview_path

    path_fns = {
        "Opus Summary": (generated_opus_summary_path, generated_opus_preview_path),
        "Coverage Summary": (generated_coverage_summary_path, generated_coverage_preview_path),
        "RGD VISIT AND SUPPORT": (generated_rgd_path, generated_rgd_preview_path),
    }

    cleared = []
    for report_type, division in _affected_report_divisions(slot_id):
        summary_fn, preview_fn = path_fns[report_type]
        for path in (summary_fn(division), preview_fn(division)):
            try:
                if path.is_file():
                    path.unlink()
                    cleared.append(f"{report_type} ({division})")
            except OSError as exc:
                logger.warning(f"Review sync: could not clear stale {report_type} ({division}): {exc}")
    return cleared


def apply_pending_updates(slot_ids: list[str], progress_cb=None) -> dict:
    """Downloads, verifies, and applies every slot in `slot_ids` (the
    `changed` list from check_for_updates()). Per-slot isolation: one
    slot's failure never blocks another's, and a failed slot's marker is
    never advanced -- it simply stays in `changed` on the next check, so
    the banner persists for exactly that slot.

    Returns {"applied": [slot_id, ...], "failed": [{"slot_id", "reason"}, ...],
    "version_blocked": [{"slot_id", "remote_version"}, ...]}. Every failure
    reason is a plain string meant to be shown directly to the user --
    silently skipping a slot here is exactly the failure shape this slice
    exists to avoid."""
    applied, failed, version_blocked = [], [], []

    try:
        client = get_supabase_client()
    except RuntimeError as exc:
        return {"applied": [], "failed": [{"slot_id": s, "reason": str(exc)} for s in slot_ids], "version_blocked": []}

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
            progress_cb(int(i / len(slot_ids) * 100), f"Applying {slot_id}...")

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
            continue  # nothing above our cutoff after all -- already applied, or raced with another apply
        manifest_row = rows[0]

        if _is_newer_version(manifest_row["app_version"], APP_VERSION):
            version_blocked.append({"slot_id": slot_id, "remote_version": manifest_row["app_version"]})
            logger.warning(
                f"Review sync: '{slot_id}' skipped -- manifest row was written by app_version "
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
                f"Review sync: HASH MISMATCH for '{slot_id}' (manifest seq={manifest_row['seq']}) -- "
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

        is_replacement = local_applied.get(slot_id) is not None
        result = validate_review_file(slot_id, str(stored_path))
        now = utcnow()
        session = get_session()
        try:
            slot_row = session.query(ReviewFileSlot).filter_by(slot_id=slot_id).first()
            if slot_row is None:
                slot_row = ReviewFileSlot(slot_id=slot_id)
                session.add(slot_row)
            slot_row.filename = manifest_row["filename"]
            slot_row.file_path = str(stored_path)
            slot_row.valid = result["valid"]
            slot_row.errors_json = json.dumps(result["errors"])
            slot_row.row_count = result["row_count"]
            slot_row.column_count = result["column_count"]
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

        if is_replacement:
            _clear_stale_generated_reports(slot_id)

        applied.append(slot_id)
        logger.info(f"Review sync: applied '{slot_id}' (seq={manifest_row['seq']}, hash verified)")

    if progress_cb:
        progress_cb(100, "Done")

    return {"applied": applied, "failed": failed, "version_blocked": version_blocked}


def _parse_manifest_timestamp(value: str) -> datetime:
    """Postgres timestamptz (via postgrest) comes back as an ISO-8601
    string, always UTC (Postgres timestamptz is stored/serialized in UTC
    regardless of session timezone) but with a +00:00/Z offset suffix --
    strip the tzinfo to match every other bookkeeping column's naive-UTC
    shape (see database.connection.utcnow's own docstring for why naive,
    not aware)."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None)
