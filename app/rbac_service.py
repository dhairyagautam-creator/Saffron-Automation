"""Loads the authenticated user's profile and module permissions after
sign-in. Reads from the `profiles`/`user_module_permissions` tables (see
supabase/migrations/0020_module_based_permissions.sql) and stores the
result in app/rbac_state.py, the centralized, in-memory place the rest of
the app reads "who is this / what can they do" from -- mirrors
app/mode_state.py's pattern.

This module only LOADS data and logs what it found. It does not enforce
per-module permissions -- no feature is blocked, no UI is hidden, based on
what's loaded here; that's app/permissions.py's job. It does, however,
block *access entirely* when a profile is missing/broken, or the account
is disabled -- these are pass/fail account-level gates, not permission
enforcement (there's nothing partial about them: either the account loads
cleanly, or the user never reaches the app).

Requires read access to `profiles`/`user_module_permissions` for the
`authenticated` Postgres role -- see supabase/migrations/
0002_profiles_roles_read_policies.sql and 0020_module_based_permissions.sql.
Uses the same shared Supabase client as auth_service.py, so these queries
run with the just-signed-in user's own JWT (RLS sees them as
`authenticated` with auth.uid() set), not the anon key.
"""

from dataclasses import dataclass
from typing import Optional

import httpx
from loguru import logger

from app import auth_service, permissions, rbac_state
from app.supabase_client import get_supabase_client

_NETWORK_ERROR_MESSAGE = "Could not reach Supabase. Check your internet connection and try again."
_NO_PROFILE_MESSAGE = "No profile is set up for this account yet. Contact your administrator."
_ACCOUNT_DISABLED_MESSAGE = "Your account has been disabled. Contact your administrator."
_UNEXPECTED_MESSAGE = "Could not load your account. Please try again."


@dataclass
class RbacResult:
    success: bool
    error_message: Optional[str] = None


def load_profile_and_permissions(user_id: str, user_email: str) -> RbacResult:
    """Fetches `profiles` (by user_id) then every `user_module_permissions`
    row for that user, and on success stores both in rbac_state. Fails
    closed: any missing profile or unexpected error returns success=False
    with a message meant to prevent access to the application, not just
    warn about it. Unlike the old role-based version, a user with ZERO
    granted modules is NOT an error -- Home Screen already handles that
    gracefully (see ui/home_page.py's EmptyState), same as a role that
    happened to grant nothing before this redesign."""
    logger.info(f"Profile lookup: step 1/2 (initialize Supabase client) for {user_email!r}")
    try:
        client = get_supabase_client()
    except RuntimeError as exc:
        # Same failure mode as auth_service.sign_in()/restore_session() --
        # this used to be uncaught here too, silently killing the
        # background thread that calls this function right after every
        # successful sign-in.
        logger.error(f"Profile lookup: step 1/2 FAILED for {user_email!r} -- Supabase client not configured: {exc}")
        return RbacResult(success=False, error_message=str(exc))

    logger.info(f"Profile lookup: step 2/2 (fetch profiles row) for {user_email!r} ({user_id})")
    try:
        profile_response = client.table("profiles").select("*").eq("id", user_id).maybe_single().execute()
    except httpx.ConnectError:
        logger.warning(f"Network error fetching profile for {user_email!r} ({user_id})")
        return RbacResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching profile for {user_email!r} ({user_id})")
        return RbacResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except Exception as exc:
        logger.warning(f"Unexpected error fetching profile for {user_email!r} ({user_id}): {exc!r}")
        return RbacResult(success=False, error_message=_UNEXPECTED_MESSAGE)

    if profile_response is None:
        logger.warning(f"Login blocked: no profile row for user {user_email!r} ({user_id}).")
        return RbacResult(success=False, error_message=_NO_PROFILE_MESSAGE)

    profile_row = profile_response.data
    if not profile_row.get("active", True):
        # Blocking rbac_state from being populated (below, for every other
        # failure case too) stops this account from ever reaching Home,
        # but on its own it leaves Supabase's own session -- both the
        # in-memory client session and the persisted keyring one -- fully
        # valid and authenticated as this user. That's a real gap for a
        # disabled account specifically (unlike a missing profile, which
        # isn't a "should never be usable again" case): the session must
        # be torn down immediately, not just declined access here.
        logger.warning(f"Login blocked: account for {user_email!r} ({user_id}) is disabled. Terminating session.")
        auth_service.sign_out()
        rbac_state.clear_current_profile()  # in case a prior, still-active session left stale state behind
        return RbacResult(success=False, error_message=_ACCOUNT_DISABLED_MESSAGE)

    try:
        permissions_response = client.table("user_module_permissions").select("module_key").eq(
            "user_id", user_id
        ).execute()
    except httpx.ConnectError:
        logger.warning(f"Network error fetching module permissions for {user_email!r} ({user_id})")
        return RbacResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        logger.warning(f"Timeout fetching module permissions for {user_email!r} ({user_id})")
        return RbacResult(success=False, error_message=_NETWORK_ERROR_MESSAGE)
    except Exception as exc:
        logger.warning(f"Unexpected error fetching module permissions for {user_email!r} ({user_id}): {exc!r}")
        return RbacResult(success=False, error_message=_UNEXPECTED_MESSAGE)

    module_keys = frozenset(row["module_key"] for row in (permissions_response.data or []))

    profile = rbac_state.Profile(
        id=profile_row["id"],
        email=user_email,
        full_name=profile_row.get("full_name"),
        active=profile_row["active"],
        is_super_admin=profile_row.get("is_super_admin", False),
        module_keys=module_keys,
    )
    rbac_state.set_current_profile(profile)

    logger.info(
        f"Session loaded: user={user_email!r} is_super_admin={profile.is_super_admin} "
        f"granted_modules={sorted(module_keys)}"
    )
    permissions.log_accessible_modules()
    return RbacResult(success=True)
