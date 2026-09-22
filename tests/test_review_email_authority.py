"""Tests for the email rework's Phase 2 permission wiring: "review_system"
joining ui/user_dialogs.py's _EMAIL_CAPABLE_MODULE_KEYS (Q -- add the
same email-authority gate the other three modules have), plus
can_send_emails("review_system") behaving exactly like every other
module key (the function itself is module-key-agnostic -- see
app/permissions.py -- so this is really confirming the wiring, not
re-testing the generic logic tests/test_permissions.py already covers).
"""

from app import rbac_state
from app.permissions import can_send_emails, email_authority_key
from ui.user_dialogs import _EMAIL_CAPABLE_MODULE_KEYS

MODULE = "review_system"


def test_review_system_is_email_capable():
    assert MODULE in _EMAIL_CAPABLE_MODULE_KEYS


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


def test_non_admin_module_without_authority_key_returns_false():
    """The exact gap the original investigation flagged: before this
    rework, any user who could open Review System could also send its
    emails, with no separate authority sub-grant. This test fails if that
    regresses."""
    _set_profile(module_keys=frozenset({MODULE}))
    try:
        assert can_send_emails(MODULE) is False
    finally:
        rbac_state.clear_current_profile()
