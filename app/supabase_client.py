"""Supabase client wiring for Version 2.0 (cloud foundation).

Centralized, singleton entry point for every future module (Validator,
Inventory, Payment Analysis, ...) to reach Supabase through -- nothing else
in the app should call `create_client` directly. Not imported by main.py or
any existing module yet; this is foundation only, no auth, no sync.
"""

import base64
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from loguru import logger
from postgrest.exceptions import APIError
from supabase import Client, create_client

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_env_file_found = _ENV_PATH.exists()
load_dotenv(_ENV_PATH)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")


def _masked_key(key: str | None) -> str:
    """Never log a full API key, even an anon one -- just enough to confirm
    something non-empty and plausible-looking was actually loaded."""
    if not key:
        return "(not set)"
    return f"{key[:12]}...({len(key)} chars)"


def log_config_status() -> None:
    """Logs the resolved .env path/existence, the loaded (non-secret) URL,
    and a masked form of the key -- the first place to look for "why can't
    this machine reach Supabase at all." _env_file_found=False on an
    installed/frozen build (as opposed to running from source) means .env
    was never bundled into that build -- see Saffron Automation.spec's
    `datas` list, which must include it.

    Deliberately NOT called automatically at import time: this module is
    imported transitively from main.py's top-level imports, which run
    BEFORE app.logging_config.configure_logging() does (that only happens
    inside main()). Logging this at import time would use loguru's
    unconfigured default handler (stderr) instead of the real file sinks --
    and on a windowed build (console=False), sys.stderr is None, so the
    message would simply vanish with nothing ever written anywhere. main()
    calls this explicitly, right after configure_logging() succeeds, so it
    reliably lands in the actual log file on every machine, not just ones
    run from a terminal."""
    logger.info(
        f"Supabase config: .env path={_ENV_PATH} found={_env_file_found} "
        f"SUPABASE_URL={SUPABASE_URL or '(not set)'} SUPABASE_ANON_KEY={_masked_key(SUPABASE_ANON_KEY)}"
    )


_client: Client | None = None


def _decode_jwt_role(token: str) -> str | None:
    """Best-effort read of the `role` claim from a Supabase API key's JWT
    payload. Not signature-verified -- we don't hold the project's JWT
    secret and don't need to for this purpose. This is only a
    misconfiguration guard (catching "someone pasted the wrong key"), never
    an authentication check, so its result must never be trusted for
    anything security-sensitive."""
    try:
        payload_segment = token.split(".")[1]
        padded = payload_segment + "=" * (-len(payload_segment) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        return payload.get("role")
    except Exception:
        return None


def _assert_not_service_role(key: str) -> None:
    if _decode_jwt_role(key) == "service_role":
        raise RuntimeError(
            "SUPABASE_ANON_KEY is set to a service_role key. The service_role key "
            "bypasses Row Level Security and must NEVER be embedded in the desktop "
            "app. Replace it with the 'anon' / 'public' key from Supabase Project "
            "Settings -> Data API -> Project API keys."
        )


def get_supabase_client() -> Client:
    """Returns the single shared Supabase client for the whole application.
    Every module should call this instead of constructing its own client --
    keeps connection setup, key loading, and the service_role guard in one
    place regardless of how many modules end up talking to Supabase."""
    global _client
    if _client is not None:
        return _client

    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_ANON_KEY are not set (.env not found or empty at "
            f"{_ENV_PATH}). If running from source, copy .env.example to .env and fill in "
            "the values. If running an installed build, this means .env was not bundled "
            "into the package -- see Saffron Automation.spec's `datas` list."
        )
    _assert_not_service_role(SUPABASE_ANON_KEY)

    _client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _client


def create_standalone_client() -> Client:
    """Returns a brand-new, independent Supabase client -- NOT the shared
    singleton. Needed for operations (e.g. app.user_management_service's
    admin-creates-a-user flow via auth.sign_up()) whose side effect is
    establishing a session for whichever client makes the call. Using the
    shared singleton for that would silently replace the currently
    signed-in admin's own session with the new user's -- an independent
    client keeps that side effect fully contained and discarded once the
    call returns."""
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_ANON_KEY are not set (.env not found or empty at "
            f"{_ENV_PATH}). If running from source, copy .env.example to .env and fill in "
            "the values. If running an installed build, this means .env was not bundled "
            "into the package -- see Saffron Automation.spec's `datas` list."
        )
    _assert_not_service_role(SUPABASE_ANON_KEY)
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def check_connection() -> tuple[bool, str]:
    """Round-trips a request to Supabase and never raises. Callers (UI code
    in particular, once this is wired into the app) get back a plain
    (ok, message) pair suitable for showing directly to the user instead of
    a crash or a raw traceback -- covers no-internet, Supabase-unreachable,
    and rejected-credentials cases explicitly."""
    try:
        client = get_supabase_client()
    except RuntimeError as exc:
        return False, str(exc)

    try:
        client.table("connection_test").select("*").execute()
        return True, "Connected to Supabase."
    except httpx.ConnectError:
        return False, "Could not reach Supabase. Check your internet connection and try again."
    except httpx.TimeoutException:
        return False, "Supabase did not respond in time. Check your internet connection and try again."
    except APIError as exc:
        # exc.code is sometimes an int (gateway-level errors, e.g. 401) and
        # sometimes a Postgres/PostgREST error string (e.g. "42501",
        # "PGRST301") depending on whether Supabase rejected the request
        # before or after it reached the database -- normalize to str so
        # both shapes match here instead of silently falling through.
        if str(exc.code) in ("401", "42501", "PGRST301"):
            return False, "Supabase rejected the connection's credentials. Contact IT support."
        return False, f"Supabase returned an unexpected error: {exc.message or exc.code}"
    except Exception as exc:
        logger.warning(f"Unexpected error checking Supabase connection: {exc!r}")
        return False, "An unexpected error occurred while connecting to Supabase."


def verify_connection() -> None:
    """CLI entry point (see __main__ below) -- prints a clear pass/fail
    without ever raising a raw traceback at the user."""
    ok, message = check_connection()
    if ok:
        print(f"SUCCESS: {message}")
    else:
        print(f"FAILED: {message}")
        sys.exit(1)


if __name__ == "__main__":
    verify_connection()
