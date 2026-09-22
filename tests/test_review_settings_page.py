"""Real Tk construction test for ui/review_settings_page.py after the
email rework removed its dead "Enable Automatic Email Sending" switch,
and the parameter-sync project's Phase 1 cleanup later dropped the
underlying AppSettings.automatic_email_enabled column entirely (see
database/migrations.py's drop_dead_automatic_email_flags() and
app/email_settings_service.py's own module docstring) -- there is
nothing left to preserve on save, so this page's save handler is now a
plain passthrough to app.email_settings_service.save_settings().
"""

import pytest

from tests.db_isolation import isolate_database


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    isolate_database(monkeypatch)


@pytest.fixture(scope="module")
def _tk_root(_shared_tk_root):
    return _shared_tk_root


def test_page_constructs_with_no_automatic_switch(_tk_root):
    from ui.review_settings_page import ReviewSettingsPage

    page = ReviewSettingsPage(_tk_root)
    try:
        _tk_root.update()
        assert not hasattr(page, "automatic_switch")
    finally:
        page.destroy()


def test_save_round_trips_sender_and_password(_tk_root):
    from app.email_settings_service import get_settings
    from ui.review_settings_page import ReviewSettingsPage

    page = ReviewSettingsPage(_tk_root)
    try:
        _tk_root.update()
        page.sender_entry.delete(0, "end")
        page.sender_entry.insert(0, "reviewer@x.com")
        page.password_entry.delete(0, "end")
        page.password_entry.insert(0, "app-password")
        page._on_save_clicked()

        settings = get_settings()
        assert settings["sender_email"] == "reviewer@x.com"
        assert settings["app_password"] == "app-password"
    finally:
        page.destroy()
