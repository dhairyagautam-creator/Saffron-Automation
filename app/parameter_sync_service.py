"""Generic cloud config sync for per-module parameters -- revives the
`module_configurations` table (see supabase/migrations/0026_module_configurations.sql)
Version 2.0 Milestone 9 originally built and the sync-removal task later
dropped (see supabase/migrations/0022_drop_sync_infrastructure.sql, which
listed this table for deletion but was itself never executed anywhere --
this module does not assume the old table still exists either way).

Full-blob, latest-wins per module: no field-level merge for scalar
parameters. This is confirmed safe by the parameter inventory this
project started from -- no parameter anywhere in the app has a
legitimate per-user or per-machine difference concept. (Developer Mode
was the one past reason such a difference could exist, and it is itself
now fully dead -- see database/migrations.py's own
drop_developer_mode_schema(), which permanently collapsed both
app_settings and rule_parameters to a single environment.)

Each module owns its own `get_full_configuration()` (assembles the
blob from its local parameter store) and `apply_full_configuration(config)`
(validates and writes a pulled blob back into local storage) -- this
module only knows how to push/pull an opaque dict for a given
`module_key`, never which fields a particular module cares about.

SECURITY: no credential or per-machine-only bookkeeping value may ever
reach this table. Every module's own get_full_configuration() is
written to never include one (confirmed: sender email/app password
pairs, the Geoapify API key, and the three one-time AppSettings
bookkeeping flags are excluded by construction, not by filtering).
FORBIDDEN_KEYS below is a second, defensive layer -- push_config()
refuses to push a blob containing any of them, raising loudly rather
than silently stripping, so a future bug that tries to sync a
credential fails at push time instead of leaking it to Supabase.

Offline posture matches every other Supabase-touching module in this
app: a network failure never raises, and is reported back to the caller
as a plain, non-raising result. Unlike file-upload sync, though, a save
that cannot push is BLOCKED entirely (see try_push_and_apply) -- this is
a deliberate, stricter design for parameters: a local-only save with a
"not synced" warning is explicitly rejected in favor of "if it can't
sync, it doesn't save."
"""

from datetime import datetime

import httpx
from loguru import logger

from app.profile_names_service import display_name_for, refresh_profile_name_cache
from app.rbac_state import current_profile
from app.supabase_client import get_supabase_client

_NETWORK_EXCEPTIONS = (httpx.ConnectError, httpx.TimeoutException)

# Every key name that must never appear inside a pushed config blob, from
# any module -- credentials and per-machine-only bookkeeping. See module
# docstring's SECURITY note.
FORBIDDEN_KEYS = frozenset(
    {
        "sender_gmail_address",
        "gmail_app_password",
        "sender_email",
        "app_password",
        "inventory_sender_email",
        "inventory_sender_app_password",
        "work_distribution_sender_email",
        "work_distribution_sender_app_password",
        "geoapify_api_key",
        "setup_completed",
        "inventory_data_reset_completed",
        "timestamps_backfilled_to_utc",
    }
)


class ParameterSyncBlocked(Exception):
    """Raised by push_config() when a blob contains a forbidden key -- a
    programming-error signal (a module tried to sync a credential or
    per-machine flag), never a runtime condition callers are meant to
    catch and route around."""


def _assert_no_forbidden_keys(config: dict) -> None:
    found = FORBIDDEN_KEYS & config.keys()
    if found:
        raise ParameterSyncBlocked(
            f"Refusing to push config containing forbidden key(s): {sorted(found)} -- "
            "credentials and per-machine bookkeeping must never be synced."
        )


def push_config(module_key: str, config: dict) -> tuple[bool, str | None]:
    """Pushes `config` (the module's FULL current configuration, already
    assembled by that module's own get_full_configuration()) to
    module_configurations, upserting the one row for `module_key`.
    Returns (True, None) on success, (False, error_message) on any
    failure (not signed in, offline, or a Supabase error)."""
    _assert_no_forbidden_keys(config)

    profile = current_profile()
    if profile is None:
        return False, "Sign in required to save -- parameter changes must sync immediately."

    try:
        client = get_supabase_client()
        client.table("module_configurations").upsert(
            {"module_key": module_key, "config": config}, on_conflict="module_key"
        ).execute()
    except _NETWORK_EXCEPTIONS:
        return False, (
            "Could not reach Supabase -- parameter changes must sync immediately, so this "
            "save was not applied. Check your connection and try again."
        )
    except Exception as exc:
        logger.error(f"Parameter sync: push failed for {module_key!r}: {exc!r}")
        return False, f"Sync failed: {exc!r}. This save was not applied."

    logger.info(f"Parameter sync: pushed config for {module_key!r}")
    return True, None


def try_push_and_apply(module_key: str, new_config: dict, apply_fn) -> tuple[bool, str | None]:
    """The save-time flow every module's own Save button uses: push
    `new_config` FIRST, and only call `apply_fn(new_config)` (the
    module's own local-write function) if that push succeeds. Returns
    (True, None) on success, (False, error_message) on failure --
    apply_fn is never called on failure, so a blocked save never writes
    locally while failing to sync (the offline/signed-out block this
    project's UI design requires -- see module docstring)."""
    ok, error = push_config(module_key, new_config)
    if not ok:
        return False, error
    apply_fn(new_config)
    return True, None


def _parse_timestamp(value: str) -> datetime:
    """Postgres timestamptz (via postgrest) comes back as an ISO-8601
    string, always UTC, with a +00:00/Z offset suffix -- strip the
    tzinfo to match every other bookkeeping column's naive-UTC shape
    (see database.connection.utcnow's own docstring for why naive)."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None)


def pull_config(module_key: str) -> dict:
    """{'ok': True, 'config': dict, 'updated_at': datetime (naive UTC),
    'updated_by_name': str} or {'ok': False, 'reason': 'offline' | 'error'
    | 'not_found'} -- never raises. 'not_found' means no one has ever
    pushed this module's config yet."""
    try:
        client = get_supabase_client()
        response = (
            client.table("module_configurations")
            .select("config, updated_at, updated_by")
            .eq("module_key", module_key)
            .limit(1)
            .execute()
        )
    except _NETWORK_EXCEPTIONS:
        logger.info(f"Parameter sync: pull skipped for {module_key!r} -- Supabase unreachable.")
        return {"ok": False, "reason": "offline"}
    except Exception as exc:
        logger.warning(f"Parameter sync: pull failed for {module_key!r}: {exc!r}")
        return {"ok": False, "reason": "error"}

    rows = response.data or []
    if not rows:
        return {"ok": False, "reason": "not_found"}

    refresh_profile_name_cache(client)

    row = rows[0]
    return {
        "ok": True,
        "config": row["config"],
        "updated_at": _parse_timestamp(row["updated_at"]),
        "updated_by_name": display_name_for(row["updated_by"]) if row.get("updated_by") else "someone",
    }


def check_for_config_update(module_key: str, local_config: dict) -> dict:
    """The lightweight check that drives the "New configuration
    available" banner -- pulls the remote config and compares it against
    `local_config` (the module's own current get_full_configuration()
    result) value-by-value. No local "last seen" bookkeeping table is
    needed for this: if this machine's own values already match what's
    on the server (nobody else changed anything, or this machine's own
    last save IS what's on the server), there's nothing to apply and no
    banner shows.

    Returns pull_config()'s own dict shape, plus a `changed: bool` key on
    success. `reason: 'not_found'` is never itself a "changed" state --
    nothing has ever been pushed, so there's nothing to pull; the caller
    should treat that the same as "no banner", not an error."""
    result = pull_config(module_key)
    if not result["ok"]:
        return result
    result["changed"] = result["config"] != local_config
    return result
