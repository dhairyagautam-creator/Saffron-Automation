"""Regression: sync_active_session must not churn on active_session
clock-skew noise.

Reproduces the REAL failure path observed at runtime -- reconcile keeps
reporting the cloud active-session pointer as "newer" (server-UTC updated_at
vs local naive activated_at), pointing at the SAME import we're already on.
Before the fix, sync_active_session re-adopted it and returned changed=True on
every 15s tick, reloading the page and surfacing the transient
just-analyzed/not-yet-suppressed window. After the fix it is a no-op.

Offline: reconcile_rows, the session, and set_active_import are stubbed.
"""

from datetime import datetime

import app.import_sync_service as iss
import app.session_state as session_state
from app.sync_service import ReconcileResult
from database.models import ActiveSession, ImportHistory


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Query:
    def __init__(self, obj):
        self._obj = obj

    def filter_by(self, **kw):
        return self

    def first(self):
        return self._obj


class _Session:
    """Returns a preset object per model class (ActiveSession / ImportHistory)."""

    def __init__(self, by_model):
        self._by_model = by_model

    def query(self, model):
        return _Query(self._by_model.get(model))

    def commit(self):
        pass

    def close(self):
        pass


def _run(cloud_import_cloud_id, local_active, matching_import):
    """Drive sync_active_session with stubs; return (result, adopted_ids)."""
    adopted = []
    saved = (iss.is_developer_mode, iss.reconcile_rows, iss.get_session, session_state.set_active_import)
    iss.is_developer_mode = lambda: False
    iss.reconcile_rows = lambda *a, **k: ReconcileResult(
        success=True, to_pull=[{"id": 1, "import_cloud_id": cloud_import_cloud_id}], to_push=[]
    )
    iss.get_session = lambda: _Session({ActiveSession: local_active, ImportHistory: matching_import})
    session_state.set_active_import = lambda import_id: adopted.append(import_id)
    try:
        result = iss.sync_active_session()
    finally:
        iss.is_developer_mode, iss.reconcile_rows, iss.get_session, session_state.set_active_import = saved
    return result, adopted


def test_no_churn_when_already_on_cloud_import():
    # Cloud points at the SAME import we're already on -> must be a no-op.
    local = _Obj(id=1, import_cloud_id="CID", activated_at=datetime(2026, 8, 10, 6, 0, 0))
    result, adopted = _run("CID", local_active=local, matching_import=_Obj(id=21))
    assert result is False          # not reported as a change -> no page reload
    assert adopted == []            # never re-adopted the active session


def test_genuine_switch_still_works():
    # Cloud points at a DIFFERENT import that exists locally -> still switch.
    local = _Obj(id=1, import_cloud_id="CID", activated_at=datetime(2026, 8, 10, 6, 0, 0))
    result, adopted = _run("OTHER", local_active=local, matching_import=_Obj(id=99))
    assert result is True
    assert adopted == [99]          # legit cross-machine switch preserved


if __name__ == "__main__":
    test_no_churn_when_already_on_cloud_import()
    test_genuine_switch_still_works()
    print("active session churn: all checks passed")
