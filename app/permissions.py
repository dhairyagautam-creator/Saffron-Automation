"""Centralized, reusable permission checks -- module-based (redesigned
from the old shared-role model; see app/module_registry.py and
app/rbac_state.py for the data this reads).

Every place in the app that needs to know "can this user see/use X" calls
can_access(module_key) below -- never `rbac_state.current_profile()`
directly, and never re-implemented ad hoc per screen. This is the single
place that knows what "can access" means; UI code (ui/home_page.py,
ui/main_window.py) only ever asks can_access(key) and reacts to a bool.

Fails closed: if nobody is signed in, or permission loading never
completed, every check returns False -- there is no "permission unknown,
allow by default" path.
"""

from loguru import logger

from app import module_registry, rbac_state


def can_access(module_key: str) -> bool:
    """True if the signed-in user can access `module_key` (one of
    app.module_registry's own ModuleDef.key values) -- either because
    they're a super admin (every module, automatically -- including one
    added after they were last granted anything, since this checks the
    flag, not a per-module row) or because they hold an explicit grant for
    that exact key."""
    profile = rbac_state.current_profile()
    if profile is None:
        return False
    if profile.is_super_admin:
        return True
    return module_key in profile.module_keys


def email_authority_key(module_key: str) -> str:
    """The user_module_permissions key that grants email-send authority for
    `module_key` -- a free-form string in the same table as the module
    grants themselves (see supabase/migrations/0020_module_based_permissions.sql),
    not a separate column or table. Single source of truth for this format:
    both can_send_emails() below and ui/user_dialogs.py's sub-checkbox
    construct/parse it via this function rather than a duplicated f-string."""
    return f"{module_key}:email_authority"


def can_send_emails(module_key: str) -> bool:
    """True if the signed-in user may trigger a manual email send for
    `module_key` -- a super admin, unconditionally (same bypass as
    can_access, and for the same reason: zero-maintenance access to a
    sub-permission added after they were last granted anything), or a user
    who holds both the module grant itself AND the email_authority_key
    sub-grant. Re-checks can_access(module_key) here rather than trusting
    that the sub-permission can never exist without it -- cascade
    enforcement (parent required, revoked with it) is client-side only in
    ui/user_dialogs.py, so this is a defensive backstop against the two
    rows drifting apart, not redundant belt-and-suspenders for its own
    sake."""
    profile = rbac_state.current_profile()
    if profile is None:
        return False
    if profile.is_super_admin:
        return True
    return can_access(module_key) and email_authority_key(module_key) in profile.module_keys


def log_accessible_modules() -> None:
    """Logs which modules are enabled vs. restricted for the current user.
    Called once right after permission loading succeeds (see
    app/rbac_service.py) -- not something that needs calling anywhere else,
    since the signed-in user's permissions don't change again mid-session."""
    profile = rbac_state.current_profile()
    if profile is None:
        logger.warning("log_accessible_modules() called with no profile loaded -- all modules restricted.")
        return

    enabled = [m.title for m in module_registry.all_modules() if can_access(m.key)]
    restricted = [m.title for m in module_registry.all_modules() if not can_access(m.key)]
    logger.info(
        f"Modules enabled for {profile.email!r} (super_admin={profile.is_super_admin}): {enabled or 'none'}"
    )
    logger.info(f"Modules restricted for {profile.email!r}: {restricted or 'none'}")
