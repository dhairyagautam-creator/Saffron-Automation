"""Tests for app/parameter_sync_service.py -- the generic push_config()/
pull_config()/check_for_config_update()/try_push_and_apply() layer every
module's own scalar-parameter sync is built on.

Uses a small fake Supabase client backing only the `module_configurations`
table (the one table this module ever touches), instead of a real network
call -- Pass 1's fake-Supabase-client discipline. `app.rbac_state` is a
real in-memory singleton (see tests/test_permissions.py's own docstring),
so a profile is set/cleared per test rather than mocked.
"""

from types import SimpleNamespace

import pytest

from app import rbac_state
import app.parameter_sync_service as pss


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
    """Backs app.parameter_sync_service's own get_supabase_client() with
    an in-memory fake -- patched where it's IMPORTED (parameter_sync_service's
    own module namespace), not where get_supabase_client is defined."""
    rows: dict = {}
    monkeypatch.setattr(pss, "get_supabase_client", lambda: _FakeSupabaseClient(rows))
    monkeypatch.setattr(pss, "display_name_for", lambda user_id: "Test User")
    return rows


@pytest.fixture(autouse=True)
def _signed_in(monkeypatch):
    rbac_state.set_current_profile(
        rbac_state.Profile(
            id="u1", email="user@example.com", full_name="Test User",
            active=True, is_super_admin=True, module_keys=frozenset(),
        )
    )
    yield
    rbac_state.clear_current_profile()


def test_push_then_pull_round_trips_config(fake_supabase):
    ok, error = pss.push_config("employee_module", {"SAME_LOCATION": {"same_place_radius_meters": "50"}})
    assert ok is True
    assert error is None

    result = pss.pull_config("employee_module")
    assert result["ok"] is True
    assert result["config"] == {"SAME_LOCATION": {"same_place_radius_meters": "50"}}
    assert result["updated_by_name"] == "Test User"


def test_pull_not_found_when_never_pushed(fake_supabase):
    result = pss.pull_config("never_pushed_module")
    assert result == {"ok": False, "reason": "not_found"}


def test_push_upserts_not_duplicates(fake_supabase):
    pss.push_config("inventory_module", {"threshold_multiplier": 1.5})
    pss.push_config("inventory_module", {"threshold_multiplier": 2.0})
    result = pss.pull_config("inventory_module")
    assert result["config"] == {"threshold_multiplier": 2.0}


def test_push_blocked_when_not_signed_in(fake_supabase):
    rbac_state.clear_current_profile()
    ok, error = pss.push_config("employee_module", {"a": "b"})
    assert ok is False
    assert "sign in" in error.lower()
    # Nothing was written -- confirm push_config never reached the fake client.
    assert fake_supabase == {}


def test_push_blocked_on_network_failure(monkeypatch):
    import httpx

    def _raise_connect_error():
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(pss, "get_supabase_client", _raise_connect_error)
    ok, error = pss.push_config("employee_module", {"a": "b"})
    assert ok is False
    assert "could not reach" in error.lower()


def test_check_for_config_update_reports_changed_when_remote_differs(fake_supabase):
    pss.push_config("employee_module", {"SAME_LOCATION": {"same_place_radius_meters": "50"}})
    result = pss.check_for_config_update("employee_module", {"SAME_LOCATION": {"same_place_radius_meters": "100"}})
    assert result["ok"] is True
    assert result["changed"] is True
    assert result["config"] == {"SAME_LOCATION": {"same_place_radius_meters": "50"}}


def test_check_for_config_update_reports_unchanged_when_identical(fake_supabase):
    same = {"SAME_LOCATION": {"same_place_radius_meters": "50"}}
    pss.push_config("employee_module", same)
    result = pss.check_for_config_update("employee_module", same)
    assert result["ok"] is True
    assert result["changed"] is False


def test_try_push_and_apply_applies_locally_only_on_success(fake_supabase):
    applied = []
    ok, error = pss.try_push_and_apply("employee_module", {"a": 1}, applied.append)
    assert ok is True
    assert error is None
    assert applied == [{"a": 1}]


def test_try_push_and_apply_never_applies_locally_when_push_fails():
    """Offline-block contract: apply_fn must never be called if push_config
    itself fails -- this is what makes a blocked save leave nothing
    written locally."""
    rbac_state.clear_current_profile()
    applied = []
    ok, error = pss.try_push_and_apply("employee_module", {"a": 1}, applied.append)
    assert ok is False
    assert error is not None
    assert applied == []  # apply_fn was never called


# --- Credential/bookkeeping safety net (hard assertion, per Phase 6) ------

@pytest.mark.parametrize("forbidden_key", sorted(pss.FORBIDDEN_KEYS))
def test_push_config_refuses_every_forbidden_key(fake_supabase, forbidden_key):
    """Confirms none of the 3 credential pairs, geoapify_api_key, or the
    3 one-time bookkeeping flags can ever be pushed -- push_config raises
    loudly (never silently strips) if a config blob contains any of
    them."""
    with pytest.raises(pss.ParameterSyncBlocked):
        pss.push_config("some_module", {forbidden_key: "should never sync"})
    # Confirm it never reached the fake client either.
    assert fake_supabase == {}


def test_forbidden_keys_covers_every_credential_and_bookkeeping_field():
    """Names every field the parameter inventory identified as a
    credential or per-machine-only flag -- if a new one is ever added to
    any module's settings, this test documents what must be added here
    too, rather than silently missing coverage."""
    expected = {
        "sender_gmail_address", "gmail_app_password",
        "sender_email", "app_password",
        "inventory_sender_email", "inventory_sender_app_password",
        "work_distribution_sender_email", "work_distribution_sender_app_password",
        "geoapify_api_key",
        "setup_completed", "inventory_data_reset_completed", "timestamps_backfilled_to_utc",
    }
    assert pss.FORBIDDEN_KEYS == frozenset(expected)
