"""Real Tk construction + behavior tests for the parameter sync UI --
covers ui/parameters_page.py as the representative page (the same
ui.parameter_sync_panel.ParameterSyncPanel + try_push_and_apply wiring
is used identically on Inventory/Work Distribution/Payment Analytics'
own Settings pages), plus a construction smoke test for the other pages
so a wiring mistake on any of them would fail loudly here rather than
only in the real app.

Confirms, per Phase 6's explicit checklist:
  - Dashboard Parameters is genuinely gone from the Parameters page UI
    (not just the backing data -- see tests/test_parameter_sync_modules.py
    for that half).
  - Auto-push actually fires on every local save with no separate button.
  - A save attempt while offline/signed-out is blocked with a clear
    message, not silently allowed to write locally.
"""

from types import SimpleNamespace

import pytest

from app import rbac_state
import app.parameter_sync_service as pss
from tests.db_isolation import isolate_database


class _FakeModuleConfigTable:
    def __init__(self, rows: dict):
        self._rows = rows
        self._eq_key = None

    def select(self, _cols):
        return self

    def eq(self, _col, value):
        self._eq_key = value
        return self

    def limit(self, _n):
        return self

    def upsert(self, data, on_conflict=None):
        self._rows[data["module_key"]] = {
            "config": data["config"],
            "updated_at": "2026-01-01T00:00:00+00:00",
            "updated_by": "11111111-1111-1111-1111-111111111111",
        }
        return self

    def execute(self):
        if self._eq_key is not None:
            row = self._rows.get(self._eq_key)
            return SimpleNamespace(data=[row] if row else [])
        return SimpleNamespace(data=list(self._rows.values()))


class _FakeSupabaseClient:
    def __init__(self, rows: dict):
        self._rows = rows

    def table(self, name):
        assert name == "module_configurations"
        return _FakeModuleConfigTable(self._rows)


@pytest.fixture
def fake_supabase(monkeypatch):
    rows: dict = {}
    monkeypatch.setattr(pss, "get_supabase_client", lambda: _FakeSupabaseClient(rows))
    monkeypatch.setattr(pss, "display_name_for", lambda user_id: "Test User")
    return rows


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    isolate_database(monkeypatch)


@pytest.fixture(scope="module")
def _tk_root(_shared_tk_root):
    return _shared_tk_root


def _sign_in():
    rbac_state.set_current_profile(
        rbac_state.Profile(
            id="u1", email="user@example.com", full_name="Test User",
            active=True, is_super_admin=True, module_keys=frozenset(),
        )
    )


@pytest.fixture(autouse=True)
def _clean_profile():
    yield
    rbac_state.clear_current_profile()


def test_parameters_page_has_no_dashboard_section(_tk_root):
    from ui.parameters_page import ParametersPage, RULE_SECTIONS

    assert not any(s["rule_name"] == "DASHBOARD" for s in RULE_SECTIONS)

    page = ParametersPage(_tk_root)
    try:
        _tk_root.update()
        rule_names = [s["rule_name"] for s in page._active_sections]
        assert "DASHBOARD" not in rule_names
        assert not hasattr(page, "_build_color_field")
        assert not hasattr(page, "_on_color_swatch_clicked")
    finally:
        page.destroy()


def test_parameters_page_save_auto_pushes_with_no_separate_button(_tk_root, fake_supabase):
    """Auto-push-on-save: clicking the ONE "Save Parameters" button must
    both write locally AND push to the cloud -- no separate sync/push
    button exists or needs clicking."""
    from app.rule_parameters import MODULE_KEY, get_parameters
    from ui.parameters_page import ParametersPage

    _sign_in()
    page = ParametersPage(_tk_root)
    try:
        _tk_root.update()
        slider = page._controls[("SAME_LOCATION", "same_place_radius_meters")]["slider"]
        slider.set(250)
        page._on_slider_moved(("SAME_LOCATION", "same_place_radius_meters"), {"step": 10, "unit": " m"}, 250)

        page._on_save_clicked()

        assert get_parameters("SAME_LOCATION")["same_place_radius_meters"] == "250"
        remote = pss.pull_config(MODULE_KEY)
        assert remote["ok"] is True
        assert remote["config"]["SAME_LOCATION"]["same_place_radius_meters"] == "250"
        assert "synced" in page.confirmation_label.cget("text").lower()
    finally:
        page.destroy()


def test_parameters_page_hospital_suppression_radius_saves_and_auto_pushes(_tk_root, fake_supabase):
    """Hospital Suppression's radius_meters has its own visible, editable
    section distinct from SAME_LOCATION's own radius -- saving it writes
    to HOSPITAL_SUPPRESSION's own row (not SAME_LOCATION's) and pushes
    through the same auto-push-on-save path as every other field."""
    from app.rule_parameters import MODULE_KEY, get_parameters
    from ui.parameters_page import ParametersPage

    _sign_in()
    page = ParametersPage(_tk_root)
    try:
        _tk_root.update()
        rule_names = [s["rule_name"] for s in page._active_sections]
        assert "HOSPITAL_SUPPRESSION" in rule_names

        slider = page._controls[("HOSPITAL_SUPPRESSION", "radius_meters")]["slider"]
        slider.set(150)
        page._on_slider_moved(("HOSPITAL_SUPPRESSION", "radius_meters"), {"step": 10, "unit": " m"}, 150)

        page._on_save_clicked()

        # Written to HOSPITAL_SUPPRESSION.radius_meters specifically -- not
        # aliased onto SAME_LOCATION's own (differently-named) radius field.
        assert get_parameters("HOSPITAL_SUPPRESSION")["radius_meters"] == "150"
        assert "radius_meters" not in get_parameters("SAME_LOCATION")
        assert "same_place_radius_meters" not in get_parameters("HOSPITAL_SUPPRESSION")

        remote = pss.pull_config(MODULE_KEY)
        assert remote["ok"] is True
        assert remote["config"]["HOSPITAL_SUPPRESSION"]["radius_meters"] == "150"
        assert "synced" in page.confirmation_label.cget("text").lower()
    finally:
        page.destroy()


def test_parameters_page_save_blocked_when_signed_out_writes_nothing_locally(_tk_root):
    from app.rule_parameters import get_full_configuration
    from ui.parameters_page import ParametersPage

    page = ParametersPage(_tk_root)  # not signed in
    try:
        _tk_root.update()
        original = get_full_configuration()["SAME_LOCATION"]["same_place_radius_meters"]

        slider = page._controls[("SAME_LOCATION", "same_place_radius_meters")]["slider"]
        slider.set(999)
        page._on_slider_moved(("SAME_LOCATION", "same_place_radius_meters"), {"step": 10, "unit": " m"}, 999)
        page._on_save_clicked()

        # Blocked: local value is unchanged, and the confirmation label
        # shows a clear reason, not a silent success.
        assert get_full_configuration()["SAME_LOCATION"]["same_place_radius_meters"] == original
        message = page.confirmation_label.cget("text").lower()
        assert "sign in" in message
    finally:
        page.destroy()


def test_parameters_page_save_blocked_when_offline_writes_nothing_locally(_tk_root, monkeypatch):
    import httpx

    from app.rule_parameters import get_full_configuration
    from ui.parameters_page import ParametersPage

    _sign_in()

    def _raise_connect_error():
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(pss, "get_supabase_client", _raise_connect_error)

    page = ParametersPage(_tk_root)
    try:
        _tk_root.update()
        original = get_full_configuration()["HOURS_WORKED"]["minimum_hours_threshold"]

        slider = page._controls[("HOURS_WORKED", "minimum_hours_threshold")]["slider"]
        slider.set(9)
        page._on_slider_moved(("HOURS_WORKED", "minimum_hours_threshold"), {"step": 1, "unit": " hours"}, 9)
        page._on_save_clicked()

        assert get_full_configuration()["HOURS_WORKED"]["minimum_hours_threshold"] == original
        message = page.confirmation_label.cget("text").lower()
        assert "could not reach" in message
    finally:
        page.destroy()


# --- Construction smoke tests for the other 5 sync-wired pages -----------

def test_inventory_settings_page_constructs_with_sync_panel(_tk_root):
    from ui.inventory_settings_page import InventorySettingsPage

    page = InventorySettingsPage(_tk_root)
    try:
        _tk_root.update()
        assert hasattr(page, "_sync_panel")
        page.on_show()
        _tk_root.update()
    finally:
        page.destroy()


def test_work_distribution_settings_page_constructs_with_sync_panel(_tk_root):
    from ui.work_distribution_settings_page import WorkDistributionSettingsPage

    page = WorkDistributionSettingsPage(_tk_root)
    try:
        _tk_root.update()
        assert hasattr(page, "_sync_panel")
        page.on_show()
        _tk_root.update()
    finally:
        page.destroy()


def test_payment_parameters_page_constructs_with_sync_panel(_tk_root):
    from ui.payment_parameters_page import PaymentParametersPage

    page = PaymentParametersPage(_tk_root)
    try:
        _tk_root.update()
        assert hasattr(page, "_sync_panel")
        page.on_show()
        _tk_root.update()
    finally:
        page.destroy()


def test_master_email_recipients_section_constructs_with_sync_panel(_tk_root):
    from ui.master_email_recipients_section import MasterEmailRecipientsSection

    section = MasterEmailRecipientsSection(_tk_root)
    try:
        _tk_root.update()
        assert hasattr(section, "_sync_panel")
        section.on_show()
        _tk_root.update()
    finally:
        section.destroy()


def test_inventory_automated_emails_page_constructs_with_sync_panel(_tk_root, monkeypatch):
    from app import rbac_state as rbac
    from ui.inventory_automated_emails_page import InventoryAutomatedEmailsPage

    rbac.clear_current_profile()
    page = InventoryAutomatedEmailsPage(_tk_root)
    try:
        _tk_root.update()
        assert hasattr(page, "_sync_panel")
        page.on_show()
        _tk_root.update()
    finally:
        page.destroy()
