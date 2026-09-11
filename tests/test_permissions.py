"""Pure-logic checks for app/permissions.py's can_send_emails() -- no
network, no DB; rbac_state is an in-memory singleton so each test sets and
clears its own profile.
"""

from app import rbac_state
from app.permissions import can_send_emails, email_authority_key

MODULE = "inventory_module"


def _set_profile(is_super_admin: bool = False, module_keys: frozenset = frozenset()) -> None:
    rbac_state.set_current_profile(
        rbac_state.Profile(
            id="u1", email="user@example.com", full_name="Test User",
            active=True, is_super_admin=is_super_admin, module_keys=module_keys,
        )
    )


def test_no_profile_signed_in_returns_false():
    rbac_state.clear_current_profile()
    try:
        assert can_send_emails(MODULE) is False
    finally:
        rbac_state.clear_current_profile()


def test_super_admin_bypasses_unconditionally():
    _set_profile(is_super_admin=True, module_keys=frozenset())
    try:
        assert can_send_emails(MODULE) is True
    finally:
        rbac_state.clear_current_profile()


def test_non_admin_with_module_and_authority_key_returns_true():
    _set_profile(module_keys=frozenset({MODULE, email_authority_key(MODULE)}))
    try:
        assert can_send_emails(MODULE) is True
    finally:
        rbac_state.clear_current_profile()


def test_non_admin_authority_key_without_module_grant_returns_false():
    # The defensive re-check: cascade enforcement (parent required) is
    # client-side only in ui/user_dialogs.py, so this state shouldn't be
    # reachable through the UI -- but can_send_emails() must not trust that
    # and grant authority just because the (unreachable-in-theory) row exists.
    _set_profile(module_keys=frozenset({email_authority_key(MODULE)}))
    try:
        assert can_send_emails(MODULE) is False
    finally:
        rbac_state.clear_current_profile()


def test_non_admin_module_without_authority_key_returns_false():
    _set_profile(module_keys=frozenset({MODULE}))
    try:
        assert can_send_emails(MODULE) is False
    finally:
        rbac_state.clear_current_profile()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Permission checks: all passed")
