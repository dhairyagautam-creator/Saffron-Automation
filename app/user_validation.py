"""Shared validation rules for User Management.

Used by both the UI dialogs (ui/user_dialogs.py, for instant feedback) and
app/user_management_service.py (before any Supabase call, as a
defense-in-depth check independent of the UI) -- one place decides what
"valid" means, not two copies that could silently drift apart.

This is the shared Python-side validation layer, not a replacement for
real server-side enforcement -- that still exists independently in
Postgres (the RLS policies in supabase/migrations/
0005_user_management_write_policies.sql and
0020_module_based_permissions.sql) and in Supabase Auth itself (email
format/uniqueness, password rules).

There is deliberately no "at least one module selected" rule here -- a
user with zero granted modules is a valid, supported state (Home Screen
already shows a plain "no modules enabled" message for exactly this case,
see ui/home_page.py), not an error to block on save.
"""

import re
from typing import Optional

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8


def validate_full_name(full_name: str) -> Optional[str]:
    if not full_name or not full_name.strip():
        return "Full name is required."
    return None


def validate_email(email: str) -> Optional[str]:
    if not email or not _EMAIL_RE.match(email):
        return "Enter a valid email address."
    return None


def validate_password(password: str) -> Optional[str]:
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    return None
