"""Supabase Authentication service (Version 2.0, Milestone 3).

Every Supabase Auth call (sign in, sign out, session persistence and
restoration) is centralized here behind a small result type, so UI code
(ui/login_page.py) never touches the Supabase client, gotrue exceptions, or
the session-storage mechanism directly. This is also the intended seam for
future role-based access control: `AuthResult.user_email` is the only user
identity exposed today, deliberately -- reading roles/claims off the
Supabase user object would be added here, not in the UI.

Does not touch employee/inventory/payment business logic, cloud sync, or
app/supabase_client.py's connectivity concerns beyond using its shared
client.

Session persistence uses the OS credential store (via `keyring` -- Windows
Credential Manager on this platform) rather than a plaintext file, since a
refresh token is a long-lived credential equivalent to a saved password.
"""

import json
from dataclasses import dataclass
from typing import Optional

import httpx
import keyring
from loguru import logger
from supabase_auth.errors import AuthApiError, AuthError

from app.supabase_client import get_supabase_client

_KEYRING_SERVICE = "SaffronAutomationV2"
_KEYRING_ACCOUNT = "supabase_session"

_NETWORK_ERROR_MESSAGE = "Could not reach Supabase. Check your internet connection and try again."
_TIMEOUT_ERROR_MESSAGE = "Supabase did not respond in time. Check your internet connection and try again."


@dataclass
class AuthResult:
    """Returned by every function below -- UI code branches on `success`
    and, on failure, shows `error_message` in the login page's existing
    error banner. `error_kind` is for callers that want to distinguish
    cases programmatically (e.g. deciding whether to retry); the UI today
    just displays `error_message` regardless of kind."""

    success: bool
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    error_kind: Optional[str] = None  # "invalid_credentials" | "network" | "session_expired" | "unexpected" | None
    error_message: Optional[str] = None


# --- Session storage (OS credential store, not a plaintext file) -----------


def _save_session(access_token: str, refresh_token: str) -> None:
    payload = json.dumps({"access_token": access_token, "refresh_token": refresh_token})
    try:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT, payload)
    except Exception as exc:
        # Persistence failing must never break sign-in itself -- worst case
        # the user just has to log in again next launch.
        logger.warning(f"Could not persist Supabase session to the OS credential store: {exc!r}")


def _load_session() -> Optional[tuple[str, str]]:
    try:
        raw = keyring.get_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
    except Exception as exc:
        logger.warning(f"Could not read saved Supabase session from the OS credential store: {exc!r}")
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data["access_token"], data["refresh_token"]
    except Exception:
        return None


def _clear_session() -> None:
    try:
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_ACCOUNT)
    except Exception:
        pass  # nothing stored, or backend unavailable -- either way, non-fatal


# --- Public API --------------------------------------------------------


def sign_in(email: str, password: str) -> AuthResult:
    """Authenticate with Supabase Auth using email + password. On success,
    persists the session (see module docstring) so `restore_session()` can
    find it on the next launch."""
    logger.info(f"Login: step 1/4 (initialize Supabase client) for {email!r}")
    try:
        client = get_supabase_client()
    except RuntimeError as exc:
        # Missing/misconfigured SUPABASE_URL/SUPABASE_ANON_KEY (e.g. .env not
        # bundled into an installed build) previously escaped uncaught from
        # here -- since sign_in() runs on a background thread (see
        # ui/login_page.py), an uncaught exception there is silently
        # swallowed by the thread with no callback ever firing, leaving the
        # UI stuck on "Signing in..." forever with no visible error at all.
        # Catching it here turns that silent hang into a real, specific
        # AuthResult the UI can actually show.
        logger.error(f"Login: step 1/4 FAILED for {email!r} -- Supabase client not configured: {exc}")
        return AuthResult(success=False, error_kind="unexpected", error_message=str(exc))

    logger.info(f"Login: step 2/4 (send sign-in request) for {email!r}")
    try:
        response = client.auth.sign_in_with_password({"email": email, "password": password})
    except httpx.ConnectError:
        logger.warning(f"Login: step 2/4 network error for {email!r}")
        return AuthResult(success=False, error_kind="network", error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        logger.warning(f"Login: step 2/4 timed out for {email!r}")
        return AuthResult(success=False, error_kind="network", error_message=_TIMEOUT_ERROR_MESSAGE)
    except AuthApiError as exc:
        logger.warning(f"Login: step 2/4 rejected for {email!r}: code={exc.code!r} message={exc.message!r}")
        if exc.code == "invalid_credentials":
            return AuthResult(
                success=False, error_kind="invalid_credentials", error_message="Invalid email or password."
            )
        return AuthResult(success=False, error_kind="unexpected", error_message=f"Sign-in failed: {exc.message}")
    except AuthError as exc:
        logger.warning(f"Login: step 2/4 failed for {email!r}: {exc}")
        return AuthResult(success=False, error_kind="unexpected", error_message=f"Sign-in failed: {exc}")
    except Exception as exc:
        logger.warning(f"Login: step 2/4 unexpected error for {email!r}: {exc!r}")
        return AuthResult(
            success=False, error_kind="unexpected", error_message=f"An unexpected error occurred while signing in: {exc!r}"
        )

    logger.info(f"Login: step 3/4 (validate response) for {email!r}")
    session, user = response.session, response.user
    if session is None or user is None:
        logger.error(f"Login: step 3/4 FAILED for {email!r} -- response had no session/user: {response!r}")
        return AuthResult(
            success=False, error_kind="unexpected", error_message="Sign-in did not return a valid session."
        )

    logger.info(f"Login: step 4/4 (persist session) for {email!r} -- user id={user.id}")
    _save_session(session.access_token, session.refresh_token)
    logger.info(f"Login: complete for {email!r}")
    return AuthResult(success=True, user_id=user.id, user_email=user.email)


def restore_session() -> AuthResult:
    """Called on startup. Returns success only if a session was saved from
    a previous run AND Supabase confirms it's still valid (refreshing it
    first if the access token has expired but the refresh token hasn't).
    Any failure clears the stored session so a bad/expired one is never
    retried indefinitely."""
    stored = _load_session()
    if stored is None:
        return AuthResult(success=False)  # nothing saved -- show login normally, no error message

    access_token, refresh_token = stored
    try:
        client = get_supabase_client()
    except RuntimeError as exc:
        # See sign_in()'s matching comment -- this used to be uncaught here
        # too, silently killing the background thread that calls
        # restore_session() on every startup.
        logger.error(f"Session restore FAILED -- Supabase client not configured: {exc}")
        return AuthResult(success=False, error_kind="unexpected", error_message=str(exc))

    try:
        response = client.auth.set_session(access_token, refresh_token)
    except httpx.ConnectError:
        return AuthResult(success=False, error_kind="network", error_message=_NETWORK_ERROR_MESSAGE)
    except httpx.TimeoutException:
        return AuthResult(success=False, error_kind="network", error_message=_TIMEOUT_ERROR_MESSAGE)
    except AuthError:
        # Covers AuthApiError, AuthInvalidJwtError, AuthSessionMissingError,
        # etc. -- the saved session is no longer usable for any reason.
        _clear_session()
        return AuthResult(
            success=False, error_kind="session_expired", error_message="Your saved session has expired. Please log in again."
        )
    except Exception as exc:
        logger.warning(f"Unexpected error restoring session: {exc!r}")
        _clear_session()
        return AuthResult(
            success=False, error_kind="unexpected", error_message="Could not restore your saved session. Please log in again."
        )

    session, user = response.session, response.user
    if session is None or user is None:
        _clear_session()
        return AuthResult(success=False)

    # The access/refresh token pair may have been rotated during the refresh
    # above -- re-save so next launch uses the current pair, not a stale one.
    _save_session(session.access_token, session.refresh_token)
    return AuthResult(success=True, user_id=user.id, user_email=user.email)


def sign_out() -> None:
    """Clears the local session unconditionally, even if the network call
    to invalidate it server-side fails -- a user clicking Logout must never
    be left unable to log out just because Supabase is unreachable."""
    try:
        get_supabase_client().auth.sign_out()
    except Exception as exc:
        logger.warning(f"Supabase sign-out API call failed (clearing local session anyway): {exc!r}")
    finally:
        _clear_session()
