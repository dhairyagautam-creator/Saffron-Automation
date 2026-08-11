"""The single source of truth for who is currently authenticated and what
they can access, once sign-in and permission loading (app/rbac_service.py)
have both succeeded.

Deliberately in-memory only and structured like app/mode_state.py's
"current environment" pattern: a pure leaf module holding process-wide
state, populated/cleared by the auth flow, read from anywhere in the app
that needs to know "who is this" or "what can they do."

(Named rbac_state, not session_state, to avoid colliding with the existing
app/session_state.py -- that module tracks the active imported workbook for
the Path Validator pipeline, an unrelated, pre-existing concept.)

This module holds data, not authorization decisions -- app/permissions.py
is the one place that turns `current_profile()` into a yes/no answer for a
given module.

Module-based permission model (replaces the old shared-Role model):
`Profile.module_keys` is the set of module keys (see app/module_registry.py)
this specific user has been explicitly granted -- per-user, not shared
with anyone else on a "role." `Profile.is_super_admin` grants every module
automatically, including one added after this session started, without
`module_keys` needing to list it -- see app/permissions.can_access()."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Profile:
    id: str
    email: str
    full_name: Optional[str]
    active: bool
    is_super_admin: bool
    module_keys: frozenset = field(default_factory=frozenset)


_state: dict = {"profile": None}


def set_current_profile(profile: Profile) -> None:
    _state["profile"] = profile


def clear_current_profile() -> None:
    _state["profile"] = None


def current_profile() -> Optional[Profile]:
    """The signed-in user's profile + permissions, or None if nobody is
    signed in (or permission loading hasn't completed/failed)."""
    return _state["profile"]
