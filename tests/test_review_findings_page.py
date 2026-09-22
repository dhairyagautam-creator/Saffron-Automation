"""Real Tk construction test for ui/review_findings_page.py -- Review
System's new "Send Emails" page (this module's Findings-page equivalent,
added by the email rework that moved sending off the File Preview page).

Constructing the whole ui/review_system_module.py shell is the more
valuable regression guard here: it's what actually caught, during manual
review, that ui/icons.py's _ICON_FUNCS is a plain dict with NO fallback
for an unmapped nav-button name -- a "Send Emails" entry with no icon
mapping would have raised KeyError the first time anyone opened the
Review System module. That mapping was added alongside this page; this
test is what would have caught its absence.

email_send_history_service.get_last_send hits Supabase -- monkeypatched
here (no network in tests, same convention as
tests/test_hierarchy_pages_construction.py's own isolate_database use for
the local DB half).
"""

import pytest

from tests.db_isolation import isolate_database


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    isolate_database(monkeypatch)
    # app.review_notification_service did `from app.email_send_history_service
    # import get_last_send` -- that copies the reference at import time, so
    # patching it must target the NAME AS IMPORTED into that module, not the
    # original module it came from (patching the latter would silently do
    # nothing here).
    monkeypatch.setattr("app.review_notification_service.get_last_send", lambda module: None)


@pytest.fixture(scope="module")
def _tk_root(_shared_tk_root):
    return _shared_tk_root


def test_review_findings_page_constructs_and_shows(_tk_root):
    from ui.review_findings_page import ReviewFindingsPage

    page = ReviewFindingsPage(_tk_root)
    try:
        _tk_root.update()
        page.on_show()
        _tk_root.update()
    finally:
        page.destroy()


def test_review_system_module_constructs_with_send_emails_page(_tk_root, monkeypatch):
    """The real regression guard: building the whole module shell exercises
    ui.icons.get_icon("Send Emails", ...) for the new nav button -- would
    have raised KeyError before the icon mapping was added."""
    from ui.review_system_module import ReviewSystemModule

    module = ReviewSystemModule(_tk_root, on_home=lambda: None)
    try:
        _tk_root.update()
        assert "Send Emails" in module.pages
        module.show_page("Send Emails")
        _tk_root.update()
    finally:
        module.destroy()


def test_send_button_disabled_without_email_authority(_tk_root, monkeypatch):
    from app import rbac_state
    from ui.review_findings_page import ReviewFindingsPage

    rbac_state.set_current_profile(
        rbac_state.Profile(
            id="u1", email="user@example.com", full_name="Test User",
            active=True, is_super_admin=False, module_keys=frozenset({"review_system"}),
        )
    )
    try:
        page = ReviewFindingsPage(_tk_root)
        try:
            _tk_root.update()
            page.on_show()
            assert page.send_emails_button.button.cget("state") == "disabled"
        finally:
            page.destroy()
    finally:
        rbac_state.clear_current_profile()
