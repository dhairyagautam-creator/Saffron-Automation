"""Phase 1 sync-reliability proofs (Path Validator findings).

Offline: reconcile_rows' network read (pull_rows) and the CAS client are
stubbed, so these run with no Supabase and no emails. They prove the version/
dirty rules directly.
"""

from datetime import date

import app.sync_service as ss
from app.findings_sync_service import classify_cas_result
from app.sync_service import RowsSyncResult
from database.models import InvestigationFinding


# --- helpers ---------------------------------------------------------------

def _local(cloud_id, acknowledged_version, dirty, **cols):
    row = {
        "cloud_id": cloud_id,
        "updated_at": acknowledged_version,   # the cloud version this row last acknowledged
        "_dirty": dirty,
        "_expected_version": acknowledged_version,
    }
    row.update(cols)
    return row


def _cloud(cloud_id, version, **cols):
    row = {"cloud_id": cloud_id, "updated_at": version}
    row.update(cols)
    return row


def _reconcile(local_rows, cloud_rows):
    """reconcile_rows in version/dirty mode with a stubbed cloud pull."""
    original = ss.pull_rows
    ss.pull_rows = lambda table, *, filters=None, **kw: RowsSyncResult(success=True, rows=list(cloud_rows))
    try:
        return ss.reconcile_rows(
            "path_validator_findings",
            local_rows,
            key_columns=["cloud_id"],
            dirty_column="_dirty",
            version_column="updated_at",
        )
    finally:
        ss.pull_rows = original


# --- 1. dirty local cannot be overwritten by an older cloud ----------------

def test_dirty_local_not_overwritten_by_older_cloud():
    local = [_local("A", "2026-08-10T06:00:00+00:00", True, notification_status="Suppressed - Region Rule")]
    cloud = [_cloud("A", "2026-08-10T05:00:00+00:00", notification_status=None)]
    plan = _reconcile(local, cloud)
    assert [r["cloud_id"] for r in plan.to_pull] == []          # never pulled over
    assert [r["cloud_id"] for r in plan.to_push] == ["A"]       # pushed instead


# --- 2. clock skew cannot reverse the decision -----------------------------

def test_clock_skew_cannot_reverse_decision():
    # Cloud version LOOKS far newer as a timestamp, but local is dirty -> push.
    plan = _reconcile([_local("A", "2000-01-01T00:00:00+00:00", True)],
                      [_cloud("A", "2099-12-31T23:59:59+00:00")])
    assert [r["cloud_id"] for r in plan.to_push] == ["A"] and plan.to_pull == []
    # Clean local whose acknowledged version equals the cloud's -> no-op,
    # regardless of what any wall clock would say.
    same = "2050-06-01T12:00:00+00:00"
    plan2 = _reconcile([_local("B", same, False)], [_cloud("B", same)])
    assert plan2.to_pull == [] and plan2.to_push == []


# --- 3. a newer cloud change on a clean local row is pulled ----------------

def test_clean_local_pulls_newer_cloud():
    plan = _reconcile([_local("A", "V1", False)], [_cloud("A", "V2", notification_status="Sent")])
    assert [r["cloud_id"] for r in plan.to_pull] == ["A"]
    assert plan.to_push == []


# --- 4. simultaneous local + cloud change is detected as a conflict --------

class _FakeQuery:
    def __init__(self, store):
        self.store = store
        self._eqs = {}

    def update(self, row):
        return self

    def eq(self, col, val):
        self._eqs[col] = val
        return self

    def execute(self):
        key = self._eqs.get("cloud_id")
        expected = self._eqs.get("updated_at")
        current = self.store.get(key)
        if current is None or current != expected:      # version moved -> no row updated
            return type("R", (), {"data": []})()
        bumped = current + "-bumped"
        self.store[key] = bumped
        return type("R", (), {"data": [{"cloud_id": key, "updated_at": bumped}]})()


class _FakeClient:
    def __init__(self, store):
        self.store = store

    def table(self, _name):
        return _FakeQuery(self.store)


def test_simultaneous_change_detected_as_conflict():
    original = ss.get_supabase_client
    store = {"A": "Vserver"}   # cloud is currently at Vserver
    ss.get_supabase_client = lambda: _FakeClient(store)
    try:
        # We still think it's at the stale "Vlocal" -> CAS matches nothing -> conflict.
        res = ss.update_row_cas("t", key_column="cloud_id", key_value="A",
                                version_column="updated_at", expected_version="Vlocal", row={"x": 1})
        assert classify_cas_result(res) == ("conflict", None)
        assert store["A"] == "Vserver"   # cloud NOT overwritten on conflict
        # Correct expected version -> acknowledged with the server's new version.
        res2 = ss.update_row_cas("t", key_column="cloud_id", key_value="A",
                                 version_column="updated_at", expected_version="Vserver", row={"x": 1})
        assert classify_cas_result(res2) == ("acknowledged", "Vserver-bumped")
    finally:
        ss.get_supabase_client = original


# --- 5. "Suppressed - Region Rule" survives the 15s refresh ----------------

def test_suppression_survives_15s_refresh():
    # The exact slingshot: suppression set locally (dirty), cloud still holds
    # the stale import-time snapshot (same version token, notif=None).
    version = "2026-08-10T06:41:13+00:00"
    local = [_local("F", version, True, notification_status="Suppressed - Region Rule")]
    cloud = [_cloud("F", version, notification_status=None)]
    plan = _reconcile(local, cloud)                      # == what the 15s poller runs
    assert [r["cloud_id"] for r in plan.to_pull] == []   # refresh cannot revert it
    pushed = {r["cloud_id"]: r for r in plan.to_push}
    assert "F" in pushed and pushed["F"]["notification_status"] == "Suppressed - Region Rule"


# --- 6. repeated syncs are idempotent --------------------------------------

def test_repeated_sync_is_idempotent():
    version = "Vx"
    for _ in range(2):
        plan = _reconcile([_local("A", version, False, notification_status="Sent")],
                          [_cloud("A", version, notification_status="Sent")])
        assert plan.to_pull == [] and plan.to_push == []


# --- 7. a dirty finding survives restart and is retried --------------------

def test_dirty_and_cloud_version_are_persistent_columns():
    cols = InvestigationFinding.__table__.columns.keys()
    assert "dirty" in cols and "cloud_version" in cols


def test_dirty_round_trips_across_sessions():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from database.connection import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    s = Session()
    s.add(InvestigationFinding(
        employee_name="A", employee_code="E", visit_date=date(2026, 8, 7),
        rule_name="HOURS_WORKED", message="m", status="Open", dirty=1, cloud_version="V1",
    ))
    s.commit()
    s.close()

    s2 = Session()   # simulate a restart -- fresh session reads persisted state
    reloaded = s2.query(InvestigationFinding).first()
    assert reloaded.dirty == 1 and reloaded.cloud_version == "V1"
    s2.close()
    # And a reloaded-dirty finding is retried (pushed), never pulled over.
    plan = _reconcile([_local("A", "V1", True)], [_cloud("A", "V1")])
    assert [r["cloud_id"] for r in plan.to_push] == ["A"] and plan.to_pull == []


# --- 8. a failed push leaves the row dirty ---------------------------------

def test_failed_push_leaves_dirty():
    # error (network) and conflict both classify as "not acknowledged" -> the
    # caller only clears dirty on "acknowledged", so a failed push stays dirty.
    assert classify_cas_result(RowsSyncResult(success=False, error_message="net")) == ("error", None)
    assert classify_cas_result(RowsSyncResult(success=True, rows=[])) == ("conflict", None)
    assert classify_cas_result(RowsSyncResult(success=True, rows=[{"updated_at": "Vnew"}])) == ("acknowledged", "Vnew")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("Sync reliability: all checks passed")
