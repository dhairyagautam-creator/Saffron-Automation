"""User Management data access -- module-based permissions (redesigned
from the old shared-role model; see app/module_registry.py and
supabase/migrations/0020_module_based_permissions.sql).

Fetching the user list calls the `get_all_users()` Postgres function
(supabase/migrations/0004_get_all_users_function.sql, redefined by
migration 0020 to return `is_super_admin`/`modules` instead of
`role_id`/`role_name`), the one deliberate place `auth.users` data ever
reaches this app.

create_user / update_user / set_user_active are the real write path, built
on:
  - supabase/migrations/0005_user_management_write_policies.sql +
    0020_module_based_permissions.sql -- the RLS policies letting an admin
    (is_super_admin, or holding the 'user_management' module grant)
    insert/update any profiles row and grant/revoke any
    user_module_permissions row, with "can't disable your own account"
    and "can't revoke your own user_management grant" enforced at the
    database level, not just here.
  - app/user_validation.py -- the same field validation the dialogs
    already run client-side, run again here independent of the UI.
  - app.supabase_client.create_standalone_client() for create_user
    specifically -- signing up a new user establishes a session for that
    user on whichever client makes the call, which would otherwise
    silently replace the admin's own active session if done on the shared
    singleton.

The list of which module keys EXIST comes from app/module_registry.py
directly (plain Python, no network round trip) -- only which keys a given
user currently HOLDS comes from Supabase.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx
from loguru import logger
from supabase_auth.errors import AuthApiError, AuthError

from app import rbac_state, user_validation
from app.supabase_client import create_standalone_client, get_supabase_client

_NETWORK_ERROR_MESSAGE = "Could not reach Supabase. Check your internet connection and try again."
_SELF_LOCKOUT_MESSAGE = "You cannot remove your own User Management access."


@dataclass(frozen=True)
class UserRecord:
    id: str
    email: str
    full_name: Optional[str]
    is_super_admin: bool
    modules: list[str]
    active: bool
    created_at: Optional[datetime]


@dataclass
class UserListResult:
    success: bool
    users: Optional[list[UserRecord]] = None
    error_message: Optional[str] = None


@dataclass
class UserOperationResult:
    success: bool
    error_message: Optional[str] = None
    needs_email_confirmation: bool = False


def _parse_timestamp(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _would_self_lock_out(user_id: str, is_super_admin: bool, module_keys: set[str]) -> bool:
    """True if applying this change to the CALLER'S OWN account would leave
    them unable to reach User Management again -- mirrors, client-side,
    the same specific protection the 'user_management' grant's RLS DELETE
    policy enforces at the database level (see migration 0020's own
    comment on "Admins can revoke module permissions"). Only blocks the
    exact case that locks the caller out; does not restrict any other
    self-edit."""
    current = rbac_state.current_profile()
    if current is None or current.id != user_id:
        return False
    return not (is_super_admin or "user_management" in module_keys)


def list_users() -> UserListResult:
    """Fetches every user via the get_all_users() RPC. That function
    requires the caller to be a super admin or hold the 'user_management'
    module grant and enforces it itself -- defense in depth beyond the
    UI-level gate already in MainWindow.show_screen(), so an unauthorized
    caller gets a clear Postgres exception here rather than silently
    empty/partial data."""
    client = get_supabase_client()
    try:
        response = client.rpc("get_all_users").execute()
    except httpx.ConnectError:
        return UserListResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        return UserListResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except Exception as exc:
        logger.warning(f"Error fetching user list: {exc!r}")
        return UserListResult(success=False, error_message="Could not load users. Please try again.")

    users = [
        UserRecord(
            id=row["id"],
            email=row["email"],
            full_name=row.get("full_name"),
            is_super_admin=bool(row.get("is_super_admin")),
            modules=list(row.get("modules") or []),
            active=row["active"],
            created_at=_parse_timestamp(row.get("created_at")),
        )
        for row in (response.data or [])
    ]
    logger.info(f"Fetched {len(users)} user(s) for User Management.")
    return UserListResult(success=True, users=users)


def _replace_module_grants(client, user_id: str, module_keys: set[str]) -> None:
    """Deletes every existing user_module_permissions row for `user_id`,
    then inserts one row per key in `module_keys` -- the whole grant set is
    always replaced wholesale (matching what the module checklist UI
    submits: the complete set of currently-checked boxes), never diffed
    add/remove. Two REST calls, not a single transaction (this app has no
    direct Postgres connection, only the REST/RPC surface) -- an admin
    re-saving after a failed replace is expected to be rare and safe
    (idempotent: re-running this with the same set is a no-op)."""
    client.table("user_module_permissions").delete().eq("user_id", user_id).execute()
    if module_keys:
        client.table("user_module_permissions").insert(
            [{"user_id": user_id, "module_key": key} for key in module_keys]
        ).execute()


def create_user(
    full_name: str, email: str, password: str, is_super_admin: bool, module_keys: set[str], active: bool
) -> UserOperationResult:
    """Creates a new Supabase Auth user (via a standalone client -- see
    module docstring) then their profiles row (id = the auth user's id),
    then one user_module_permissions row per selected module key.

    NOT fully transactional across these systems -- and this is a real,
    permanent architectural limit, not something patched around here: if
    the profile insert fails after the auth user already exists, there is
    no way to delete that auth user without the service_role key, which
    this app deliberately never holds (see app/supabase_client.py's
    service_role guard). What this function DOES do is make that failure
    maximally clear -- exactly which step failed, the auth user's id for
    manual lookup, and that manual cleanup via the Supabase dashboard is
    required -- rather than a generic error that hides what actually
    happened. The module-grants step (3b) failing after profile creation
    succeeds (3a) is lower-stakes -- the user exists and can log in, just
    with no modules yet -- so that failure is logged and surfaced, not
    escalated to the same "manual cleanup required" severity.
    """
    logger.info(f"Create user: step 1/3 (validate input) for {email!r}")
    for error in (
        user_validation.validate_full_name(full_name),
        user_validation.validate_email(email),
        user_validation.validate_password(password),
    ):
        if error:
            logger.warning(f"Create user: step 1/3 failed for {email!r}: {error}")
            return UserOperationResult(success=False, error_message=error)

    logger.info(f"Create user: step 2/3 (create Supabase Auth account) for {email!r}")
    standalone_client = create_standalone_client()
    try:
        response = standalone_client.auth.sign_up({"email": email, "password": password})
    except httpx.ConnectError:
        logger.warning(f"Create user: step 2/3 network error for {email!r}")
        return UserOperationResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        logger.warning(f"Create user: step 2/3 timed out for {email!r}")
        return UserOperationResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except AuthApiError as exc:
        logger.warning(f"Create user: step 2/3 failed for {email!r}: {exc.code} {exc.message}")
        if exc.code in ("user_already_exists", "email_exists"):
            return UserOperationResult(success=False, error_message="A user with this email already exists.")
        if exc.code == "over_email_send_rate_limit":
            return UserOperationResult(
                success=False,
                error_message=(
                    "Supabase's email service has hit its rate limit for confirmation emails. "
                    "Wait a few minutes and try again, disable \"Confirm email\" in Supabase Auth settings for "
                    "testing, or configure a custom SMTP provider for production use."
                ),
            )
        return UserOperationResult(
            success=False, error_message=f"Step 2 of 3 (create Auth account) failed: {exc.message}"
        )
    except AuthError as exc:
        logger.warning(f"Create user: step 2/3 failed for {email!r}: {exc}")
        return UserOperationResult(success=False, error_message=f"Step 2 of 3 (create Auth account) failed: {exc}")
    except Exception as exc:
        logger.warning(f"Create user: step 2/3 unexpected error for {email!r}: {exc!r}")
        return UserOperationResult(
            success=False, error_message="Step 2 of 3 (create Auth account) failed unexpectedly."
        )

    new_user = response.user
    # Supabase deliberately does NOT raise an error for a duplicate email
    # (an anti-enumeration measure -- an attacker shouldn't be able to
    # probe which emails are registered via error messages). Instead it
    # returns what looks like a normal new-user response but with an empty
    # `identities` list. Confirmed against this project directly: signing
    # up an already-registered email returned success=True with
    # identities=[] rather than any exception -- so that emptiness, not an
    # exception, is the real signal a duplicate-email create must check.
    if new_user is None or not new_user.identities:
        logger.warning(f"Create user: step 2/3 -- {email!r} already has an account (empty identities returned).")
        return UserOperationResult(success=False, error_message="A user with this email already exists.")

    logger.info(f"Create user: step 2/3 complete for {email!r} -- auth id={new_user.id}")

    logger.info(
        f"Create user: step 3/3 (create profile: is_super_admin={is_super_admin}, "
        f"modules={sorted(module_keys)}, active={active}) for auth id={new_user.id}"
    )
    client = get_supabase_client()
    try:
        client.table("profiles").insert(
            {"id": new_user.id, "full_name": full_name, "is_super_admin": is_super_admin, "active": active}
        ).execute()
    except Exception as exc:
        logger.error(
            f"Create user: step 3/3 FAILED for {email!r} (auth id={new_user.id}): {exc!r}. "
            f"This auth account now has NO profile row and cannot log in usefully. Automatic cleanup is not "
            f"possible -- this app deliberately never holds the service_role key required to delete an Auth "
            f"user. Manual fix: Supabase dashboard -> Authentication -> Users -> delete {email!r} "
            f"(id={new_user.id}), then retry Add User."
        )
        return UserOperationResult(
            success=False,
            error_message=(
                f"Step 3 of 3 (create profile) failed: {exc}\n\n"
                f"The Supabase Auth account for {email} (id {new_user.id}) was already created and now has no "
                f"profile. This app cannot delete it automatically -- remove it manually in the Supabase "
                f"dashboard (Authentication → Users) before retrying, or this email will stay unusable."
            ),
        )

    try:
        _replace_module_grants(client, new_user.id, module_keys)
    except Exception as exc:
        logger.error(
            f"Create user: module grants failed for {email!r} (auth id={new_user.id}): {exc!r}. "
            "The account and profile were created successfully -- only the module grants need retrying "
            "(Edit User, re-check the intended modules, Save)."
        )

    logger.info(f"Create user: step 3/3 complete for {email!r} -- profile created")

    needs_confirmation = response.session is None
    logger.info(
        f"Create user: all steps complete for email={email!r} id={new_user.id} is_super_admin={is_super_admin} "
        f"modules={sorted(module_keys)} active={active} needs_email_confirmation={needs_confirmation}"
    )
    return UserOperationResult(success=True, needs_email_confirmation=needs_confirmation)


def update_user(
    user_id: str, full_name: str, is_super_admin: bool, module_keys: set[str], active: bool
) -> UserOperationResult:
    """Updates full_name/is_super_admin/active on an existing profiles row,
    then wholesale-replaces that user's module_keys (see
    _replace_module_grants). Blocks an admin from deactivating their own
    account, or from removing their own ability to reach User Management,
    before ever making a network call (fast, specific error) -- the RLS
    policies (migrations 0005/0020) enforce both regardless (the real,
    unbypassable backstop), but would only surface as a generic database
    error."""
    error = user_validation.validate_full_name(full_name)
    if error:
        return UserOperationResult(success=False, error_message=error)

    current = rbac_state.current_profile()
    if not active and current is not None and current.id == user_id:
        return UserOperationResult(success=False, error_message="You cannot disable your own account.")
    if _would_self_lock_out(user_id, is_super_admin, module_keys):
        return UserOperationResult(success=False, error_message=_SELF_LOCKOUT_MESSAGE)

    client = get_supabase_client()
    try:
        client.table("profiles").update(
            {"full_name": full_name, "is_super_admin": is_super_admin, "active": active}
        ).eq("id", user_id).execute()
        _replace_module_grants(client, user_id, module_keys)
    except httpx.ConnectError:
        return UserOperationResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        return UserOperationResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except Exception as exc:
        logger.warning(f"Error updating user {user_id}: {exc!r}")
        return UserOperationResult(
            success=False, error_message="Could not update this user. You may not have permission for this change."
        )

    logger.info(
        f"User updated: id={user_id} full_name={full_name!r} is_super_admin={is_super_admin} "
        f"modules={sorted(module_keys)} active={active}"
    )
    return UserOperationResult(success=True)


def set_user_active(user_id: str, active: bool) -> UserOperationResult:
    """Enable/Disable action -- toggles only the active flag. Same
    self-disable guard as update_user, checked first and independently
    since this is reachable from a different UI path (the table's
    Enable/Disable button, not the Edit dialog)."""
    current = rbac_state.current_profile()
    if not active and current is not None and current.id == user_id:
        return UserOperationResult(success=False, error_message="You cannot disable your own account.")

    client = get_supabase_client()
    try:
        client.table("profiles").update({"active": active}).eq("id", user_id).execute()
    except httpx.ConnectError:
        return UserOperationResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        return UserOperationResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except Exception as exc:
        logger.warning(f"Error setting active={active} for user {user_id}: {exc!r}")
        return UserOperationResult(success=False, error_message="Could not update this user's status.")

    logger.info(f"User status changed: id={user_id} active={active}")
    return UserOperationResult(success=True)
