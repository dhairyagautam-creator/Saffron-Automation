"""Phase 2 of email authority: the shared, cross-machine record of every
actual email send (see supabase/migrations/0025_email_send_history.sql and
docs/EMAIL_AUTHORITY_PHASE2_CONTEXT.md). Replaces each module's Phase 1
LOCAL per-machine "have I sent since this changed" check with one shared
Supabase table, so the answer is the same regardless of which machine
last sent -- for the "who sent it" half; see the Phase 2 report's own §6
for the accepted, documented limitation this does NOT solve (data_version
values aren't comparable across machines when the underlying business
data itself isn't synced).

Offline posture matches every other Supabase-touching module in this app
(see app/review_sync_service.py's own module docstring): unreachable must
never block the local action. get_last_send() returns None on any network
failure -- the same shape as "nothing has ever been sent" -- so a button's
state naturally (and safely) falls back to "new_data" rather than blocking
Send Emails entirely because of a network hiccup. This is a deliberate
fail-open choice: the resend-confirmation dialog is the accepted backstop
for the false-positive-toward-caution case, exactly as it already is for
Phase 1's local timestamp comparison.
"""

from datetime import datetime

import httpx
from loguru import logger

from app.profile_names_service import display_name_for, refresh_profile_name_cache
from app.rbac_state import current_profile
from app.supabase_client import get_supabase_client
from database.connection import utcnow

_NETWORK_EXCEPTIONS = (httpx.ConnectError, httpx.TimeoutException)


def _parse_send_timestamp(value: str) -> datetime:
    """Postgres timestamptz (via postgrest) comes back as an ISO-8601
    string, always UTC, with a +00:00/Z offset suffix -- strip the tzinfo
    to match every other bookkeeping column's naive-UTC shape. Same
    pattern as app.review_sync_service._parse_manifest_timestamp."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=None)


def format_relative_time(sent_at: datetime) -> str:
    """"just now" / "5m ago" / "3h ago" / "2d ago" -- computed once at
    call time, not re-rendered on a timer (no periodic UI tick for this,
    per explicit instruction: the status line is static until the next
    on_show/refresh, same as the button's own state)."""
    delta = utcnow() - sent_at
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def get_last_send(module: str) -> dict | None:
    """The most recent send for `module`, or None if none exists yet OR
    Supabase is unreachable (see module docstring -- both cases mean "we
    don't know of a prior send", handled identically by the caller).

    One query serves both the button-state check (compare `data_version`
    against the module's current local version) and the status line
    (`sent_by_name`/`sent_at`) -- no second round trip needed.

    Returns {"data_version": str, "sent_by": uuid str, "sent_by_name": str,
    "sent_at": datetime (naive UTC)}.
    """
    try:
        client = get_supabase_client()
        response = (
            client.table("email_send_history")
            .select("data_version, sent_by, sent_at")
            .eq("module", module)
            .order("sent_at", desc=True)
            .limit(1)
            .execute()
        )
    except _NETWORK_EXCEPTIONS:
        logger.info(f"Email send history: check skipped for {module!r} -- Supabase unreachable.")
        return None
    except Exception as exc:
        logger.warning(f"Email send history: check failed for {module!r}: {exc!r}")
        return None

    rows = response.data or []
    if not rows:
        return None
    row = rows[0]

    # Best-effort, same convention as app.review_sync_service.check_for_updates()
    # -- refresh the local name cache opportunistically so sent_by_name
    # resolves to a real name rather than a shortened uuid.
    refresh_profile_name_cache(client)

    return {
        "data_version": row["data_version"],
        "sent_by": row["sent_by"],
        "sent_by_name": display_name_for(row["sent_by"]),
        "sent_at": _parse_send_timestamp(row["sent_at"]),
    }


def record_send(module: str, data_version: str) -> bool:
    """Inserts one row recording that the signed-in user just sent for
    `module` at `data_version`. Returns True on success, False on any
    failure (network or otherwise) -- never raises, since a failure here
    must not be treated as "the emails weren't actually sent" (the SMTP
    send has already completed by the time this is called; see each
    module's own Send Emails click handler). A failed insert here just
    means the shared history is temporarily out of date -- the local send
    itself already succeeded and was logged locally regardless.

    `data_version` is explicitly str()'d here too, not just trusted from
    the caller (every current call site already passes str(import_id) /
    str(get_data_version(...)) itself -- this is a second, defensive cast
    so the column's `text` type is enforced by this function's own body
    regardless of caller discipline, not left to implicit
    ORM/postgrest/JSON coercion of an int)."""
    profile = current_profile()
    if profile is None:
        logger.warning(f"Email send history: cannot record send for {module!r} -- not signed in.")
        return False

    try:
        client = get_supabase_client()
        client.table("email_send_history").insert(
            {"module": module, "data_version": str(data_version), "sent_by": profile.id}
        ).execute()
    except _NETWORK_EXCEPTIONS:
        logger.warning(f"Email send history: could not record send for {module!r} -- Supabase unreachable.")
        return False
    except Exception as exc:
        logger.warning(f"Email send history: could not record send for {module!r}: {exc!r}")
        return False

    return True
