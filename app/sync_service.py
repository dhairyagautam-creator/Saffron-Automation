"""Generic cloud configuration sync service (Version 2.0, Milestone 9).

Syncs a MODULE'S ENTIRE configuration as a single JSON object between
Supabase (source of truth, the `module_configurations` table -- see
supabase/migrations/0008_module_configurations.sql) and a local cache --
never individual settings. This module knows nothing about Path Validator,
rule parameters, or any other module's data shape: every function takes a
plain `module_key` string and a plain dict. ui/parameters_page.py (via
app/rule_parameters.py) is the first caller today, but a future Employee,
Inventory, or Payments module reuses this exact service just by calling it
with their own module_key and their own local read/write functions -- no
new sync mechanism, no coupling to any particular UI.

Deliberately does NOT touch the local SQLite cache itself -- push_config()
and pull_config() only talk to Supabase. Callers write their own local
cache after a successful push, or apply a successful pull to their own
cache, keeping "what does the cloud say" and "what do we cache locally" as
two explicit, separately-logged steps rather than one opaque operation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from app.supabase_client import get_supabase_client

_NETWORK_ERROR_MESSAGE = "Could not reach Supabase. Check your internet connection and try again."
_TIMEOUT_ERROR_MESSAGE = "Supabase did not respond in time. Check your internet connection and try again."

# Rows per request when pull_rows() pages through a full table. Matches
# PostgREST's own default server-side maximum -- requesting a range larger
# than the server cap just gets silently truncated back to it, which is the
# exact failure this paging exists to avoid (see pull_rows).
_PULL_PAGE_SIZE = 1000


@dataclass
class SyncResult:
    success: bool
    config: Optional[dict] = None
    synced_at: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass
class RowsSyncResult:
    """Result of a push_rows()/pull_rows() call -- mirrors SyncResult's
    shape but carries a list of rows instead of a single config dict, since
    a table sync moves zero or more independent rows per call."""

    success: bool
    rows: list = field(default_factory=list)
    synced_at: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass
class FileSyncResult:
    """Result of an upload_file()/download_file() call."""

    success: bool
    storage_path: Optional[str] = None
    local_path: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class ReconcileResult:
    """Result of a reconcile_rows() call -- a PLAN, not an action. Never
    touches local SQLite or Supabase beyond the one read (pull_rows());
    the caller applies `to_pull` to their own local store and passes
    `to_push` into push_rows()."""

    success: bool
    to_pull: list = field(default_factory=list)
    to_push: list = field(default_factory=list)
    error_message: Optional[str] = None


def _parse_timestamp(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def push_config(module_key: str, config: dict) -> SyncResult:
    """Uploads `config` (a module's ENTIRE configuration, as one dict) to
    Supabase as the new source of truth for `module_key`. Upserts on
    module_key -- the first push for a module creates its row, every push
    after that replaces the whole config object."""
    logger.info(f"Sync: uploading configuration for module_key={module_key!r}")
    client = get_supabase_client()
    try:
        response = (
            client.table("module_configurations")
            .upsert({"module_key": module_key, "config": config}, on_conflict="module_key")
            .execute()
        )
    except httpx.ConnectError:
        logger.warning(f"Sync: upload failed for module_key={module_key!r} -- network unreachable")
        return SyncResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        logger.warning(f"Sync: upload timed out for module_key={module_key!r}")
        return SyncResult(success=False, error_message=_TIMEOUT_ERROR_MESSAGE)
    except Exception as exc:
        logger.warning(f"Sync: upload failed for module_key={module_key!r}: {exc!r}")
        return SyncResult(success=False, error_message=f"Could not save to the cloud: {exc}")

    rows = response.data or []
    synced_at = _parse_timestamp(rows[0].get("updated_at")) if rows else None
    logger.info(f"Sync: upload complete for module_key={module_key!r} at {synced_at}")
    return SyncResult(success=True, config=config, synced_at=synced_at)


def pull_config(module_key: str) -> SyncResult:
    """Downloads the current cloud configuration for `module_key`. Returns
    success=False (not an exception) if no cloud configuration has ever
    been pushed for this module yet -- a legitimate, expected state for a
    brand-new module, not an error."""
    logger.info(f"Sync: downloading configuration for module_key={module_key!r}")
    client = get_supabase_client()
    try:
        response = (
            client.table("module_configurations")
            .select("config, updated_at")
            .eq("module_key", module_key)
            .maybe_single()
            .execute()
        )
    except httpx.ConnectError:
        logger.warning(f"Sync: download failed for module_key={module_key!r} -- network unreachable")
        return SyncResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        logger.warning(f"Sync: download timed out for module_key={module_key!r}")
        return SyncResult(success=False, error_message=_TIMEOUT_ERROR_MESSAGE)
    except Exception as exc:
        logger.warning(f"Sync: download failed for module_key={module_key!r}: {exc!r}")
        return SyncResult(success=False, error_message=f"Could not reach the cloud: {exc}")

    if response is None:
        logger.info(f"Sync: no cloud configuration exists yet for module_key={module_key!r}")
        return SyncResult(success=False, error_message="No cloud configuration exists yet for this module.")

    config = response.data.get("config") or {}
    synced_at = _parse_timestamp(response.data.get("updated_at"))
    logger.info(f"Sync: download complete for module_key={module_key!r} at {synced_at}")
    return SyncResult(success=True, config=config, synced_at=synced_at)


def push_rows(table: str, rows: list, on_conflict: str) -> RowsSyncResult:
    """Upserts `rows` (a list of plain dicts, one per row) into `table`,
    conflict-resolved on `on_conflict` (a column name, e.g. "cloud_id").
    Module-agnostic like push_config() -- this function does not know what
    a finding, an import, or an Inventory/Payments row is; the caller owns
    the row shape entirely. A no-op (success, empty rows) if `rows` is
    empty -- callers don't need to guard against calling this with nothing
    to push."""
    if not rows:
        return RowsSyncResult(success=True, rows=[])

    logger.info(f"Sync: pushing {len(rows)} row(s) to table={table!r} (on_conflict={on_conflict!r})")
    client = get_supabase_client()
    try:
        response = client.table(table).upsert(rows, on_conflict=on_conflict).execute()
    except httpx.ConnectError:
        logger.warning(f"Sync: push failed for table={table!r} -- network unreachable")
        return RowsSyncResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        logger.warning(f"Sync: push timed out for table={table!r}")
        return RowsSyncResult(success=False, error_message=_TIMEOUT_ERROR_MESSAGE)
    except Exception as exc:
        logger.warning(f"Sync: push failed for table={table!r}: {exc!r}")
        return RowsSyncResult(success=False, error_message=f"Could not save to the cloud: {exc}")

    pushed_rows = response.data or []
    logger.info(f"Sync: push complete for table={table!r} ({len(pushed_rows)} row(s))")
    return RowsSyncResult(success=True, rows=pushed_rows, synced_at=datetime.now())


def pull_rows(
    table: str,
    *,
    filters: Optional[dict] = None,
    updated_after: Optional[datetime] = None,
    updated_at_column: str = "updated_at",
    order_by: Optional[str] = None,
    limit: Optional[int] = None,
) -> RowsSyncResult:
    """Selects rows from `table`, optionally narrowed by equality filters
    (`filters`, {column: value}) and/or `updated_at_column > updated_after`
    for delta pulls (only what changed since a caller's own high-water
    mark). Module-agnostic like pull_config() -- same caller-owns-the-shape
    contract as push_rows()."""
    logger.info(
        f"Sync: pulling rows from table={table!r}"
        + (f" filters={filters}" if filters else "")
        + (f" updated_after={updated_after.isoformat()}" if updated_after is not None else "")
    )
    client = get_supabase_client()

    def _build_query():
        query = client.table(table).select("*")
        for column, value in (filters or {}).items():
            query = query.eq(column, value)
        if updated_after is not None:
            query = query.gt(updated_at_column, updated_after.isoformat())
        if order_by:
            query = query.order(order_by)
        return query

    try:
        if limit:
            # An explicit limit means the caller wants a bounded set, not the
            # whole table -- honour it verbatim, single request.
            rows = _build_query().limit(limit).execute().data or []
        else:
            # No limit means "every row". PostgREST caps ANY single response at
            # its server-side maximum (1000 rows by default) and gives no
            # indication that it truncated -- so a bare select() silently
            # returns a partial table once it grows past that cap. Every row
            # beyond the cap then looks "absent from the cloud" to
            # reconcile_rows(), which re-pushes it on every sync forever while
            # never seeing it come back. Page explicitly until a short page
            # proves the end was reached (Milestone 53).
            rows = []
            offset = 0
            while True:
                page = _build_query().range(offset, offset + _PULL_PAGE_SIZE - 1).execute().data or []
                rows.extend(page)
                if len(page) < _PULL_PAGE_SIZE:
                    break
                offset += _PULL_PAGE_SIZE
    except httpx.ConnectError:
        logger.warning(f"Sync: pull failed for table={table!r} -- network unreachable")
        return RowsSyncResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        logger.warning(f"Sync: pull timed out for table={table!r}")
        return RowsSyncResult(success=False, error_message=_TIMEOUT_ERROR_MESSAGE)
    except Exception as exc:
        logger.warning(f"Sync: pull failed for table={table!r}: {exc!r}")
        return RowsSyncResult(success=False, error_message=f"Could not reach the cloud: {exc}")

    logger.info(f"Sync: pulled {len(rows)} row(s) from table={table!r}")
    return RowsSyncResult(success=True, rows=rows, synced_at=datetime.now())


def delete_rows(table: str, filters: Optional[dict] = None) -> RowsSyncResult:
    """Deletes rows from `table` matching `filters` ({column: value}), or
    every row in the table if `filters` is falsy -- a DELETE request with
    no query filter matches every row PostgREST/RLS allows, which is
    exactly "clear this table" for callers that need a full-replace sync
    (a table whose local behavior is delete-all + rebuild/reinsert, e.g.
    Payment Analytics' rolling active-month window or Collections'
    daily-refreshed outstanding invoices -- see app/payment_sync_service.py).
    Module-agnostic like push_rows()/pull_rows() -- the caller decides
    what "this table" or "these rows" means for their own module; this
    function has no opinion on it.

    Callers needing a full-table replace should call this BEFORE
    push_rows() with the fresh row set, not after -- otherwise a delete
    issued after push would just erase what was only just uploaded."""
    logger.info(
        f"Sync: deleting rows from table={table!r}" + (f" filters={filters}" if filters else " (all rows)")
    )
    client = get_supabase_client()
    try:
        query = client.table(table).delete()
        for column, value in (filters or {}).items():
            query = query.eq(column, value)
        response = query.execute()
    except httpx.ConnectError:
        logger.warning(f"Sync: delete failed for table={table!r} -- network unreachable")
        return RowsSyncResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        logger.warning(f"Sync: delete timed out for table={table!r}")
        return RowsSyncResult(success=False, error_message=_TIMEOUT_ERROR_MESSAGE)
    except Exception as exc:
        logger.warning(f"Sync: delete failed for table={table!r}: {exc!r}")
        return RowsSyncResult(success=False, error_message=f"Could not delete from the cloud: {exc}")

    deleted_rows = response.data or []
    logger.info(f"Sync: deleted {len(deleted_rows)} row(s) from table={table!r}")
    return RowsSyncResult(success=True, rows=deleted_rows, synced_at=datetime.now())


def _as_datetime(value) -> Optional[datetime]:
    """Normalizes either a Python datetime (as local rows already carry)
    or a Postgres/PostgREST ISO timestamp string (as cloud rows come back
    as) into a naive datetime, so both sides of a comparison are always
    the same shape. Returns None for anything unparseable -- reconcile_rows
    treats a missing/unparseable timestamp on either side as "push", never
    as a reason to error (see reconcile_rows' docstring)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def reconcile_rows(
    table: str,
    local_rows: list,
    *,
    key_columns: list,
    updated_at_column: str = "updated_at",
    filters: Optional[dict] = None,
    dirty_column: Optional[str] = None,
    version_column: Optional[str] = None,
) -> ReconcileResult:
    """The single, centralized "Last Modified Wins" comparison rule used
    by every synced table in the app (Version 2.0, Milestone 34) --
    Supabase is the source of truth, but a per-record `updated_at`
    comparison decides which side's copy of THIS record is actually
    newer, never "whichever action happened to run most recently
    overwrites the entire table." Every module's sync wrapper calls this
    same function; none of them implement their own comparison logic.

    `local_rows`: every local row as a plain dict, each with its own
    `key_columns` values and an `updated_at_column` value (a Python
    datetime -- see _as_datetime()). This function pulls the current cloud
    table itself (via pull_rows(), optionally narrowed by `filters` --
    e.g. one import's findings, not the whole table) and compares.

    Decision per row, matched by `key_columns`:
    - Exists in the cloud only -> `to_pull` (caller creates it locally)
    - Exists locally only -> `to_push` (caller sends it to Supabase)
    - Exists on both sides, cloud's updated_at is strictly newer ->
      `to_pull` (overwrite the local copy)
    - Exists on both sides, local's updated_at is newer or equal (or
      either side's timestamp is missing/unparseable) -> `to_push`
      (favoring push on a tie or a missing timestamp means a genuinely
      fresh local write is never silently lost)

    Returns a plan only -- never touches local SQLite or issues a push
    itself; the caller applies `to_pull` to their own local store (however
    their own model maps a cloud row) and passes `to_push` into
    push_rows() with whatever `on_conflict` matches `key_columns`.

    This function does not know or care what "the record" represents --
    a Path Validator finding, an Inventory threshold, a Payment invoice --
    exactly like push_rows()/pull_rows() already don't. It also does not
    delete anything on either side: a row removed locally (e.g. an
    evicted month, a rebuilt customer-profile list) is not treated as "a
    deletion to propagate" -- it simply has no local counterpart on the
    next reconcile and will be pulled back from the cloud if a cloud copy
    still exists there. This is a deliberate simplification (confirmed
    with the user): correct, uniform behavior across every module,
    accepting that this rule alone does not model deletions -- a
    module that truly needs a row gone from both sides has to delete it
    from the cloud explicitly (see delete_rows()), which is a separate,
    explicit action, not something this function infers from an absence."""
    result = pull_rows(table, filters=filters)
    if not result.success:
        return ReconcileResult(success=False, error_message=result.error_message)

    def key_of(row: dict) -> tuple:
        return tuple(row.get(column) for column in key_columns)

    cloud_by_key = {key_of(row): row for row in result.rows}
    local_by_key = {key_of(row): row for row in local_rows}

    # --- Version/dirty mode (opt-in; Path Validator findings, Phase 1) -----
    # When the caller passes `dirty_column`, conflict resolution is driven by
    # an explicit local dirty flag plus a server-origin version token, NOT by
    # comparing timestamps:
    #   * a DIRTY local row is NEVER pulled over -- it carries an unsynced
    #     local change, so it is pushed instead (the caller then uses an
    #     optimistic version check to detect a concurrent cloud edit);
    #   * a CLEAN local row adopts the cloud copy only when the cloud's
    #     current version token differs from the one this row last
    #     acknowledged (both values are the cloud's own server value, so no
    #     local clock / datetime.now() is ever involved -- clock skew cannot
    #     flip the decision).
    # Other modules omit `dirty_column` and keep the legacy timestamp rule
    # below completely unchanged.
    if dirty_column is not None:
        vcol = version_column or updated_at_column
        to_pull = []
        to_push = []
        for key, cloud_row in cloud_by_key.items():
            local_row = local_by_key.get(key)
            if local_row is None:
                to_pull.append(cloud_row)
                continue
            if local_row.get(dirty_column):
                to_push.append(local_row)
                continue
            if cloud_row.get(vcol) != local_row.get(vcol):
                to_pull.append(cloud_row)
        for key, local_row in local_by_key.items():
            if key not in cloud_by_key:
                to_push.append(local_row)
        logger.info(
            f"Sync: reconciled table={table!r} (version/dirty mode) -- "
            f"{len(to_pull)} row(s) to pull, {len(to_push)} row(s) to push"
        )
        return ReconcileResult(success=True, to_pull=to_pull, to_push=to_push)

    # The comparison is only meaningful if BOTH sides actually carry
    # `updated_at_column`. A caller that stores its timestamp under a
    # different cloud column name (e.g. pushing it as "last_updated" while
    # asking to compare "updated_at") silently compares against whatever
    # unrelated column happens to exist there -- typically a
    # Supabase-managed, UTC, always-just-now `updated_at`, which makes the
    # cloud copy look newer on EVERY row forever and turns this function
    # into a one-way overwrite that no local edit can ever win against.
    # That defect silently reverted real Inventory data for hours before it
    # was found, so it is now loud instead of silent (Milestone 53).
    if result.rows and not any(updated_at_column in row for row in result.rows):
        logger.warning(
            f"Sync: table={table!r} cloud rows have no {updated_at_column!r} column -- "
            "Last-Modified-Wins cannot compare, every matched row will default to push. "
            "This almost certainly means the caller pushes its timestamp under a different "
            "column name than it asks to compare on."
        )

    to_pull = []
    to_push = []

    for key, cloud_row in cloud_by_key.items():
        local_row = local_by_key.get(key)
        if local_row is None:
            to_pull.append(cloud_row)
            continue
        cloud_updated = _as_datetime(cloud_row.get(updated_at_column))
        local_updated = _as_datetime(local_row.get(updated_at_column))
        if cloud_updated is not None and local_updated is not None and cloud_updated > local_updated:
            to_pull.append(cloud_row)
        else:
            to_push.append(local_row)

    for key, local_row in local_by_key.items():
        if key not in cloud_by_key:
            to_push.append(local_row)

    logger.info(
        f"Sync: reconciled table={table!r} -- {len(to_pull)} row(s) to pull, {len(to_push)} row(s) to push"
    )
    return ReconcileResult(success=True, to_pull=to_pull, to_push=to_push)


def update_row_cas(
    table: str,
    *,
    key_column: str,
    key_value,
    version_column: str,
    expected_version,
    row: dict,
) -> RowsSyncResult:
    """Optimistic compare-and-swap update: write `row` to the record where
    `key_column == key_value` AND `version_column == expected_version`.

    On success `rows` holds the updated row, carrying the server's NEW version
    (e.g. the trigger-bumped `updated_at`). If the version no longer matches --
    someone changed the cloud row since we last acknowledged it -- the update
    matches nothing and `rows` is EMPTY, while `success` stays True: that empty
    result is a detected CONFLICT (the request ran fine; the row simply wasn't
    overwritten), which callers handle distinctly from a network failure
    (`success=False`). Because it is a conditional update, never a blind
    upsert, it can never silently overwrite a newer cloud row (Guarantee 2)."""
    logger.info(
        f"Sync: CAS update table={table!r} {key_column}={key_value!r} if {version_column}=={expected_version!r}"
    )
    client = get_supabase_client()
    try:
        response = (
            client.table(table)
            .update(row)
            .eq(key_column, key_value)
            .eq(version_column, expected_version)
            .execute()
        )
    except httpx.ConnectError:
        logger.warning(f"Sync: CAS update failed for table={table!r} -- network unreachable")
        return RowsSyncResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        logger.warning(f"Sync: CAS update timed out for table={table!r}")
        return RowsSyncResult(success=False, error_message=_TIMEOUT_ERROR_MESSAGE)
    except Exception as exc:
        logger.warning(f"Sync: CAS update failed for table={table!r}: {exc!r}")
        return RowsSyncResult(success=False, error_message=f"Could not save to the cloud: {exc}")

    rows = response.data or []
    if not rows:
        logger.warning(
            f"Sync: CAS update matched no row for table={table!r} {key_column}={key_value!r} "
            f"(cloud {version_column} moved past {expected_version!r}) -- conflict"
        )
    return RowsSyncResult(success=True, rows=rows)


def upload_file(bucket: str, storage_path: str, local_file_path: str, *, upsert: bool = True) -> FileSyncResult:
    """Uploads the file at `local_file_path` to `bucket` under
    `storage_path`. `upsert=True` (default) overwrites an existing object at
    the same path -- set False for paths meant to be written exactly once
    (e.g. a per-import folder keyed by a freshly generated cloud_id, which
    never collides)."""
    logger.info(f"Sync: uploading file to bucket={bucket!r} path={storage_path!r}")
    client = get_supabase_client()
    try:
        client.storage.from_(bucket).upload(
            storage_path,
            local_file_path,
            file_options={"upsert": "true" if upsert else "false"},
        )
    except httpx.ConnectError:
        logger.warning(f"Sync: upload failed for bucket={bucket!r} path={storage_path!r} -- network unreachable")
        return FileSyncResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        logger.warning(f"Sync: upload timed out for bucket={bucket!r} path={storage_path!r}")
        return FileSyncResult(success=False, error_message=_TIMEOUT_ERROR_MESSAGE)
    except Exception as exc:
        logger.warning(f"Sync: upload failed for bucket={bucket!r} path={storage_path!r}: {exc!r}")
        return FileSyncResult(success=False, error_message=f"Could not upload to the cloud: {exc}")

    logger.info(f"Sync: upload complete for bucket={bucket!r} path={storage_path!r}")
    return FileSyncResult(success=True, storage_path=storage_path, local_path=local_file_path)


def download_file(bucket: str, storage_path: str, local_dest_path: str) -> FileSyncResult:
    """Downloads `storage_path` from `bucket` to `local_dest_path`, creating
    parent directories if needed."""
    logger.info(f"Sync: downloading file from bucket={bucket!r} path={storage_path!r}")
    client = get_supabase_client()
    try:
        data = client.storage.from_(bucket).download(storage_path)
    except httpx.ConnectError:
        logger.warning(f"Sync: download failed for bucket={bucket!r} path={storage_path!r} -- network unreachable")
        return FileSyncResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        logger.warning(f"Sync: download timed out for bucket={bucket!r} path={storage_path!r}")
        return FileSyncResult(success=False, error_message=_TIMEOUT_ERROR_MESSAGE)
    except Exception as exc:
        logger.warning(f"Sync: download failed for bucket={bucket!r} path={storage_path!r}: {exc!r}")
        return FileSyncResult(success=False, error_message=f"Could not download from the cloud: {exc}")

    dest = Path(local_dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    logger.info(f"Sync: download complete for bucket={bucket!r} path={storage_path!r} -> {local_dest_path}")
    return FileSyncResult(success=True, storage_path=storage_path, local_path=local_dest_path)
