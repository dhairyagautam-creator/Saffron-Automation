"""Profile uuid -> display name resolution -- extracted from
app/review_sync_service.py (Review System's sync slice, where this was
first built) so Phase 2 of email authority can reuse the exact same
mechanism for "Last sent 2h ago by Priya" instead of inventing a second
name-resolution path. review_sync_service.py re-exports display_name_for
from here, so nothing that already imports it from there needs to change.

Source of truth is public.profile_display_names (a view -- see
supabase/migrations/0023_sync_manifest.sql -- exposing exactly
id/full_name/active to any authenticated user, since a plain RLS policy
can't restrict to specific columns and profiles holds role_id/is_super_admin
alongside the name).

ProfileNameCache is a local, read-through fallback ONLY, kept for the
offline case (docs/SYNC_DESIGN.md's "Supabase unreachable must not block
anything"): display_name_for() runs synchronously on every render, so it
cannot make a live network call itself. Whenever a caller's own check
succeeds, this cache is refreshed straight from the view (never
authoritative on its own -- it just mirrors the last successful read);
when offline, it serves whatever it last knew rather than blocking or
showing nothing. It plays no part in any sync/permission/send decision --
the uuid columns it resolves remain the only source of truth for "who did
this".
"""

from loguru import logger

from app.rbac_state import current_profile
from database.connection import get_session, utcnow
from database.models import ProfileNameCache


def refresh_profile_name_cache(client) -> None:
    """Best-effort: pulls the full {id -> full_name} roster from
    public.profile_display_names and upserts it into the local read-through
    cache. Call opportunistically after any successful Supabase read that
    also needs display names -- never raises, a failure here just means
    names stay whatever was cached before."""
    try:
        response = client.table("profile_display_names").select("id, full_name").execute()
    except Exception as exc:
        logger.warning(f"Could not refresh profile name cache: {exc!r}")
        return

    now = utcnow()
    session = get_session()
    try:
        for row in response.data or []:
            cached = session.query(ProfileNameCache).filter_by(id=row["id"]).first()
            if cached is None:
                cached = ProfileNameCache(id=row["id"])
                session.add(cached)
            cached.full_name = row.get("full_name")
            cached.cached_at = now
        session.commit()
    finally:
        session.close()


def display_name_for(profile_id: str | None) -> str:
    """Best-effort display name for a profile uuid -- the current user's
    own name resolves instantly from rbac_state (no cache/network needed,
    so a user's own action always shows correctly even before any check
    has ever run); anyone else's comes from the local read-through cache
    of public.profile_display_names (see module note above -- never a live
    query, this runs synchronously during UI rendering). Falls back to a
    shortened uuid if truly nothing is known yet, never to a blank label --
    an unresolved name should look incomplete, not silently anonymous."""
    if not profile_id:
        return "Unknown"
    me = current_profile()
    if me is not None and me.id == profile_id:
        return me.full_name or me.email
    session = get_session()
    try:
        cached = session.query(ProfileNameCache).filter_by(id=profile_id).first()
    finally:
        session.close()
    if cached and cached.full_name:
        return cached.full_name
    return f"User {profile_id[:8]}"
