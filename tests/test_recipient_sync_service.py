"""Tests for app/recipient_sync_service.py's union-merge logic, plus each
recipient service's own create/update/delete_synced wrappers and
apply_new_recipients() -- MasterEmailRecipient and InventoryEmailRecipient.

Uses the same fake Supabase client as tests/test_parameter_sync_service.py
(module_configurations is the SAME table recipient lists sync through,
just under their own module_keys storing {"recipients": [...]} instead of
scalar key/value pairs).
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
def _signed_in_and_isolated_db(monkeypatch):
    isolate_database(monkeypatch)
    rbac_state.set_current_profile(
        rbac_state.Profile(
            id="u1", email="user@example.com", full_name="Test User",
            active=True, is_super_admin=True, module_keys=frozenset(),
        )
    )
    yield
    rbac_state.clear_current_profile()


# --- app.recipient_sync_service's generic dedup/merge logic ---------------

def test_check_for_recipient_update_finds_only_missing_by_email(fake_supabase):
    from app.recipient_sync_service import check_for_recipient_update

    pss.push_config("some_recipients", {"recipients": [
        {"name": "Alice", "email": "alice@x.com", "division": "ALL"},
        {"name": "Bob", "email": "BOB@x.com", "division": "ONYX"},  # case-insensitive match below
    ]})

    local = [{"name": "Bob (local spelling)", "email": "bob@x.com", "division": "ONYX"}]
    result = check_for_recipient_update("some_recipients", local)

    assert result["ok"] is True
    assert result["changed"] is True
    emails = {r["email"] for r in result["new_recipients"]}
    assert emails == {"alice@x.com"}  # Bob already present locally (case-insensitive), not "new"


def test_check_for_recipient_update_not_found_reports_unchanged(fake_supabase):
    from app.recipient_sync_service import check_for_recipient_update

    result = check_for_recipient_update("never_pushed_recipients", [])
    assert result == {"ok": True, "changed": False, "new_recipients": []}


# --- MasterEmailRecipient -------------------------------------------------

def test_master_recipient_create_synced_blocked_offline_writes_nothing_locally():
    from app.master_email_recipients_service import create_recipient_synced, get_all_recipients

    rbac_state.clear_current_profile()
    ok, error, created = create_recipient_synced("Amit", "amit@x.com", "ALL")
    assert ok is False
    assert created is None
    assert get_all_recipients() == []  # nothing written locally


def test_master_recipient_create_synced_pushes_full_list(fake_supabase):
    from app.master_email_recipients_service import MODULE_KEY, create_recipient_synced

    ok, error, created = create_recipient_synced("Amit", "amit@x.com", "ONYX")
    assert ok is True
    assert created["name"] == "Amit"

    remote = pss.pull_config(MODULE_KEY)
    assert remote["ok"] is True
    assert remote["config"]["recipients"] == [{"name": "Amit", "email": "amit@x.com", "division": "ONYX"}]


def test_master_recipient_union_merge_combines_two_machines_additions(fake_supabase):
    """The explicit Phase 6 scenario: two machines each add a different
    recipient, then sync -- both end up with the union, neither loses
    their own local-only row."""
    from app.master_email_recipients_service import (
        apply_new_recipients, check_for_update, create_recipient_synced, get_all_recipients,
    )

    # Machine A adds Alice.
    create_recipient_synced("Alice", "alice@x.com", "ALL")

    # Machine B (simulated: same process, but we snapshot/restore local
    # DB state around it) adds Bob -- pushing overwrites the remote list
    # with A's Alice + B's Bob, since create_recipient_synced always
    # pushes the FULL current local list plus the new row. To simulate
    # "B never saw Alice locally", we push directly instead of going
    # through create_recipient_synced's own "read local, append" step.
    from app.master_email_recipients_service import push_recipients
    ok, _ = push_recipients("master_email_recipients", [
        {"name": "Alice", "email": "alice@x.com", "division": "ALL"},
        {"name": "Bob", "email": "bob@x.com", "division": "ONYX"},
    ])
    assert ok is True

    # Machine A checks for updates and applies -- should merge in Bob,
    # keeping its own Alice (never deleted, never duplicated).
    result = check_for_update()
    assert result["changed"] is True
    ok, error = apply_new_recipients(result)
    assert ok is True

    local = get_all_recipients()
    emails = sorted(r["email"] for r in local)
    assert emails == ["alice@x.com", "bob@x.com"]


def test_master_recipient_pull_never_deletes_local_only_row(fake_supabase):
    """A local recipient absent from the remote list is NEVER removed by
    a pull -- union-merge only, no delete propagation (explicit
    limitation, not a bug)."""
    from app.master_email_recipients_service import (
        apply_new_recipients, check_for_update, create_recipient_synced, get_all_recipients,
    )

    create_recipient_synced("LocalOnly", "localonly@x.com", "ALL")
    # Remote has a completely disjoint recipient (simulating another
    # machine that never saw LocalOnly).
    from app.master_email_recipients_service import push_recipients
    push_recipients("master_email_recipients", [{"name": "Other", "email": "other@x.com", "division": "ALL"}])

    result = check_for_update()
    apply_new_recipients(result)

    emails = sorted(r["email"] for r in get_all_recipients())
    assert emails == ["localonly@x.com", "other@x.com"]  # both present, neither deleted


def test_master_recipient_update_synced_rejects_deleted_row_without_pushing(fake_supabase):
    from app.master_email_recipients_service import MODULE_KEY, update_recipient_synced

    ok, error = update_recipient_synced(99999, "Ghost", "ghost@x.com", "ALL")
    assert ok is False
    assert "no longer exists" in error.lower()
    # Confirm nothing was pushed for a doomed update.
    assert pss.pull_config(MODULE_KEY) == {"ok": False, "reason": "not_found"}


# --- InventoryEmailRecipient (divisions: list[str], not a single value) ---

def test_inventory_recipient_create_synced_pushes_divisions_list(fake_supabase):
    from app.inventory_email_recipients_service import MODULE_KEY, create_recipient_synced

    ok, error, created = create_recipient_synced("Priya", "priya@x.com", ["ONYX", "XANDRA"])
    assert ok is True
    assert created["divisions"] == ["ONYX", "XANDRA"]

    remote = pss.pull_config(MODULE_KEY)
    assert remote["config"]["recipients"][0]["divisions"] == ["ONYX", "XANDRA"]


def test_inventory_recipient_union_merge_skips_malformed_entries(fake_supabase):
    """A remote entry missing a required field (or with a non-list
    divisions) is skipped rather than raising -- a partial merge beats
    losing the rest of a legitimate batch."""
    from app.inventory_email_recipients_service import (
        apply_new_recipients, check_for_update, get_all_recipients, push_recipients,
    )

    push_recipients("inventory_email_recipients", [
        {"name": "Good", "email": "good@x.com", "divisions": ["ONYX"]},
        {"name": "Bad", "email": "bad@x.com", "divisions": "not-a-list"},
        {"name": "", "email": "noname@x.com", "divisions": ["XANDRA"]},
    ])

    result = check_for_update()
    ok, error = apply_new_recipients(result)
    assert ok is True

    emails = {r["email"] for r in get_all_recipients()}
    assert emails == {"good@x.com"}  # only the well-formed entry was merged
