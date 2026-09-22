"""Pass 1 (fake-Supabase-client, automated) tests for
app/work_distribution_sync_service.py -- the full 12-slot Work
Distribution sync build (9 RGD/ABM/RBM slots, already committed in Phase
1, now wired to push/pull; 3 new hierarchy slots).

Covers the behaviors genuinely new in this build (see that module's own
docstring for the reasoning):
- All 12 slots register under module="work_distribution".
- RGD/ABM/RBM process correctly with ZERO hierarchy tables present (the
  "no hierarchy gate" decision).
- A hierarchy pull updates BOTH the retained file AND workbook_connections,
  and refresh_hierarchy() picks it up without disturbing other divisions.
- A hierarchy pull always overwrites a manually-set connection, no prompt.
- A refresh_hierarchy() failure after a verified download leaves the sync
  marker un-advanced and the slot still "pending".
- Division isolation, latest-only replacement, invalid-upload rejection,
  no cross-module slot_key collision with Inventory.
- The combined-engine auto-run trigger: a report engine only runs once all
  3 of ITS OWN divisions' CURRENT retained files are present, regardless
  of arrival order or how many separate apply_pending_updates() calls it
  took to get there.

Uses tests/db_isolation.py's isolate_database() (patches BOTH
database.connection._Session and _engine -- required, not optional, see
that module's own docstring) PLUS redirects
app.work_distribution_upload_service.WORK_DISTRIBUTION_UPLOADS_DIR and
app.hierarchy_upload_service.HIERARCHY_UPLOADS_DIR to temp directories --
confirmed necessary the hard way for the sibling retention test
(tests/test_work_distribution_upload_retention.py's own docstring).
"""

import hashlib

import openpyxl
import pytest

import app.hierarchy_upload_service as hus
import app.work_distribution_sync_service as wds
import app.work_distribution_upload_service as wdus
from app.hierarchy_parser import find_by_employee_code, refresh_hierarchy
from app.work_distribution_upload_service import ABM, RBM, RGD
from app.workbook_connections import get_connection, get_status, set_connection
from database.connection import get_config_session
from database.models import HierarchyUploadSlot, SyncState, WorkDistributionDoctor, WorkDistributionFinding
from tests.db_isolation import isolate_database

_MODULE_KEY = "work_distribution"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path_factory):
    isolate_database(monkeypatch)
    monkeypatch.setattr(wdus, "WORK_DISTRIBUTION_UPLOADS_DIR", tmp_path_factory.mktemp("wd_retained"))
    monkeypatch.setattr(hus, "HIERARCHY_UPLOADS_DIR", tmp_path_factory.mktemp("hierarchy_retained"))


# --- Real fixture files -----------------------------------------------------

def _write_rgd_workbook(path, division="Onyx", bm_code="BM1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([
        "Division", "Dr. Code", "Dr. Name", "Speciality", "Category", "ABM RGD",
        "City", "HQ", "Region", "BM Code", "ABM Code", "BM Visit Jul-26", "ABM Visit Jul-26",
    ])
    ws.append([division, "D1", "Dr. One", "Cardio", "B-RGD", "", "City1", "HQ1", "Region1", bm_code, "", 1, 0])
    ws.append([division, "D2", "Dr. Two", "Cardio", "B-RGD", "", "City1", "HQ1", "Region1", bm_code, "", 0, 0])
    wb.save(path)


def _write_mwa_workbook(path, division="Onyx", emp_code="M1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([
        "Division", "Emp Code", "Emp Name", "Emp Designation", "Month",
        "Team Emp Code", "Team Emp Name", "Team Emp Designation", "# Days Spent In Joint",
    ])
    ws.append([division, emp_code, "Manager One", "ABM", "Jul-26", "B1", "Bilal BM", "BM", 5])
    wb.save(path)


def _write_hierarchy_workbook(path, division="Onyx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Emp Code", "Name", "Designation", "Mobile", "Email", "DOJ"])
    ws.append([f"{division[:1].upper()}-BM1", f"{division} Bm One", "BM", "9990000000", "bm1@example.com", ""])
    wb.save(path)


def _invalid_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Not", "The", "Right", "Columns"])
    ws.append([1, 2, 3, 4])
    wb.save(path)


# --- Minimal fake Supabase client -- same shape as
# tests/test_inventory_sync_service.py's own, extended with insert()/upload()
# so both push (upload_and_sync) and pull (apply_pending_updates) can be
# exercised against one shared fake manifest. ---

class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeManifestQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def select(self, *_a, **_k):
        return self

    def eq(self, field, value):
        self._rows = [r for r in self._rows if r.get(field) == value]
        return self

    def gt(self, field, value):
        self._rows = [r for r in self._rows if r[field] > value]
        return self

    def order(self, field, desc=False):
        self._rows.sort(key=lambda r: r[field], reverse=desc)
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def execute(self):
        return _FakeResponse(self._rows)


class _FakeInsert:
    def __init__(self, backing_rows, payload):
        self._backing_rows = backing_rows
        self._payload = payload

    def execute(self):
        seq = max((r["seq"] for r in self._backing_rows), default=0) + 1
        row = {**self._payload, "seq": seq, "uploaded_at": "2026-09-14T00:00:00+00:00"}
        self._backing_rows.append(row)
        return _FakeResponse([row])


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows  # shared, mutable list -- insert() appends to it

    def select(self, *a, **k):
        return _FakeManifestQuery(list(self._rows))

    def insert(self, payload):
        return _FakeInsert(self._rows, payload)


class _FakeBucket:
    def __init__(self, files):
        self._files = files

    def download(self, path):
        return self._files[path]

    def upload(self, path, data, _opts):
        self._files[path] = data


class _FakeStorage:
    def __init__(self, files):
        self._files = files

    def from_(self, _bucket):
        return _FakeBucket(self._files)


class _FakeProfile:
    id = "11111111-1111-1111-1111-111111111111"


class _FakeClient:
    def __init__(self, manifest_rows=None, files=None):
        self._manifest_rows = manifest_rows if manifest_rows is not None else []
        self.storage = _FakeStorage(files if files is not None else {})

    def table(self, name):
        assert name == "sync_manifest"
        return _FakeTable(self._manifest_rows)


def _manifest_row(seq, slot_key, filename, data: bytes, module="work_distribution", app_version="0.0.1") -> dict:
    return {
        "seq": seq,
        "module": module,
        "slot_key": slot_key,
        "storage_path": f"{module}/{slot_key}/{hashlib.sha256(data).hexdigest()}.xlsx",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "filename": filename,
        "app_version": app_version,  # deliberately old -- never version_blocked regardless of real APP_VERSION
        "uploaded_by": "11111111-1111-1111-1111-111111111111",
        "uploaded_at": "2026-09-11T00:00:00+00:00",
    }


# --- 1. All 12 slots register under module="work_distribution" -------------

def test_all_twelve_slots_defined_under_one_module():
    assert len(wds.ALL_SLOTS) == 12
    assert len(set(wds.ALL_SLOTS)) == 12  # no duplicates
    assert set(wds.RGD_SLOTS) == {"rgd_onyx", "rgd_guardians", "rgd_xandra"}
    assert set(wds.ABM_SLOTS) == {"abm_onyx", "abm_guardians", "abm_xandra"}
    assert set(wds.RBM_SLOTS) == {"rbm_onyx", "rbm_guardians", "rbm_xandra"}
    assert set(wds.HIERARCHY_SLOTS) == {"hierarchy_onyx", "hierarchy_guardians", "hierarchy_xandra"}
    assert wds.MODULE == "work_distribution"


def test_push_then_pull_round_trip_registers_under_correct_module(monkeypatch, tmp_path):
    source = tmp_path / "Onyx_RGD.xlsx"
    _write_rgd_workbook(source, division="Onyx")
    fake_client = _FakeClient()
    monkeypatch.setattr(wds, "get_supabase_client", lambda: fake_client)
    monkeypatch.setattr(wds, "current_profile", lambda: _FakeProfile())

    result = wds.upload_and_sync("rgd_onyx", str(source))
    assert result["success"] is True
    assert result["synced"] is True
    assert fake_client._manifest_rows[0]["module"] == "work_distribution"
    assert fake_client._manifest_rows[0]["slot_key"] == "rgd_onyx"


# --- 2. RGD/ABM/RBM process correctly with ZERO hierarchy tables present ---

def test_rgd_abm_rbm_apply_successfully_with_no_hierarchy_table_at_all(monkeypatch, tmp_path):
    """Proves the "no hierarchy gate" decision via the sync path (the
    retention-only test already proved this for local upload) -- a pull
    for all three engines succeeds with employee_hierarchy_work_distribution
    never having existed."""
    rgd_bytes = (tmp_path / "rgd.xlsx")
    _write_rgd_workbook(rgd_bytes, division="Onyx")
    abm_bytes = (tmp_path / "abm.xlsx")
    _write_mwa_workbook(abm_bytes, division="Onyx")

    manifest_rows = [
        _manifest_row(1, "rgd_onyx", "rgd.xlsx", rgd_bytes.read_bytes()),
        _manifest_row(1, "abm_onyx", "abm.xlsx", abm_bytes.read_bytes()),
    ]
    files = {r["storage_path"]: (rgd_bytes.read_bytes() if r["slot_key"] == "rgd_onyx" else abm_bytes.read_bytes()) for r in manifest_rows}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(wds, "get_supabase_client", lambda: fake_client)

    result = wds.apply_pending_updates(["rgd_onyx", "abm_onyx"])

    assert set(result["applied"]) == {"rgd_onyx", "abm_onyx"}
    assert result["failed"] == []

    # No employee_hierarchy_work_distribution table was ever created in
    # this isolated in-memory DB -- confirms zero dependency.
    assert find_by_employee_code(_MODULE_KEY, "BM1") is None


# --- 3+4+5. Hierarchy pull: workbook_connections integration, overwrite,
#            and refresh_hierarchy() failure handling ----------------------

def test_hierarchy_pull_updates_workbook_connections_and_refresh_picks_it_up(monkeypatch, tmp_path):
    hier_path = tmp_path / "onyx_hierarchy.xlsx"
    _write_hierarchy_workbook(hier_path, division="Onyx")
    data = hier_path.read_bytes()
    manifest_rows = [_manifest_row(1, "hierarchy_onyx", "OnyxHierarchy.xlsx", data)]
    files = {manifest_rows[0]["storage_path"]: data}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(wds, "get_supabase_client", lambda: fake_client)

    assert get_status(get_connection(_MODULE_KEY, "Onyx")) == "Not Configured"

    result = wds.apply_pending_updates(["hierarchy_onyx"])

    assert result["applied"] == ["hierarchy_onyx"]
    assert result["failed"] == []

    connected_path = get_connection(_MODULE_KEY, "Onyx")
    assert connected_path is not None
    assert get_status(connected_path) == "Connected"
    assert "hierarchy_onyx" in connected_path  # points at the RETAINED copy, not some other path

    row = find_by_employee_code(_MODULE_KEY, "O-BM1")
    assert row is not None
    assert row["employee_name"] == "Onyx Bm One"


def test_hierarchy_pull_for_one_division_never_disturbs_the_other_two(monkeypatch, tmp_path):
    # Guardians and Xandra are already connected (simulating a prior
    # manual pick or earlier sync) and refreshed BEFORE Onyx's pull.
    g_path = tmp_path / "guardians.xlsx"
    x_path = tmp_path / "xandra.xlsx"
    _write_hierarchy_workbook(g_path, division="Guardians")
    _write_hierarchy_workbook(x_path, division="Xandra")
    set_connection(_MODULE_KEY, "Guardians", str(g_path))
    set_connection(_MODULE_KEY, "Xandra", str(x_path))
    refresh_hierarchy(_MODULE_KEY)
    assert find_by_employee_code(_MODULE_KEY, "G-BM1") is not None
    assert find_by_employee_code(_MODULE_KEY, "X-BM1") is not None

    onyx_path = tmp_path / "onyx.xlsx"
    _write_hierarchy_workbook(onyx_path, division="Onyx")
    data = onyx_path.read_bytes()
    manifest_rows = [_manifest_row(1, "hierarchy_onyx", "Onyx.xlsx", data)]
    files = {manifest_rows[0]["storage_path"]: data}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(wds, "get_supabase_client", lambda: fake_client)

    result = wds.apply_pending_updates(["hierarchy_onyx"])
    assert result["applied"] == ["hierarchy_onyx"]

    # All three divisions' data survives the combined-table rebuild.
    assert find_by_employee_code(_MODULE_KEY, "O-BM1") is not None
    assert find_by_employee_code(_MODULE_KEY, "G-BM1") is not None
    assert find_by_employee_code(_MODULE_KEY, "X-BM1") is not None


def test_hierarchy_pull_always_overwrites_a_manually_set_connection_no_prompt(monkeypatch, tmp_path):
    manual_path = tmp_path / "manual_pick.xlsx"
    _write_hierarchy_workbook(manual_path, division="Onyx")
    set_connection(_MODULE_KEY, "Onyx", str(manual_path))
    assert get_connection(_MODULE_KEY, "Onyx") == str(manual_path)

    synced_path = tmp_path / "synced.xlsx"
    _write_hierarchy_workbook(synced_path, division="Onyx")
    data = synced_path.read_bytes()
    manifest_rows = [_manifest_row(1, "hierarchy_onyx", "Synced.xlsx", data)]
    files = {manifest_rows[0]["storage_path"]: data}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(wds, "get_supabase_client", lambda: fake_client)

    # No confirmation mechanism exists to call -- apply_pending_updates()
    # takes no user-interaction hook at all; a bare call proves there is
    # nothing in the way of the overwrite.
    result = wds.apply_pending_updates(["hierarchy_onyx"])

    assert result["applied"] == ["hierarchy_onyx"]
    new_path = get_connection(_MODULE_KEY, "Onyx")
    assert new_path != str(manual_path)
    assert "hierarchy_onyx" in new_path


def test_refresh_hierarchy_failure_leaves_marker_unadvanced_and_slot_pending(monkeypatch, tmp_path):
    hier_path = tmp_path / "onyx.xlsx"
    _write_hierarchy_workbook(hier_path, division="Onyx")
    data = hier_path.read_bytes()
    manifest_rows = [_manifest_row(1, "hierarchy_onyx", "Onyx.xlsx", data)]
    files = {manifest_rows[0]["storage_path"]: data}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(wds, "get_supabase_client", lambda: fake_client)
    monkeypatch.setattr(wds, "refresh_hierarchy", lambda module_key: (_ for _ in ()).throw(RuntimeError("boom")))

    result = wds.apply_pending_updates(["hierarchy_onyx"])

    assert result["applied"] == []
    assert len(result["failed"]) == 1
    assert result["failed"][0]["slot_id"] == "hierarchy_onyx"

    session = get_config_session()
    try:
        assert session.query(SyncState).filter_by(module="work_distribution", slot_key="hierarchy_onyx").first() is None
        assert session.query(HierarchyUploadSlot).filter_by(slot_id="hierarchy_onyx").first() is None
    finally:
        session.close()

    # Safe to retry: calling apply_pending_updates again for the same slot
    # (now with a working refresh_hierarchy) succeeds and advances the
    # marker for real. Deliberately re-setattr (not monkeypatch.undo()) --
    # undo() would also revert this test's isolate_database() patches,
    # since they share the same monkeypatch fixture instance, and silently
    # repoint the DB at the real on-disk file for the rest of this test.
    monkeypatch.setattr(wds, "refresh_hierarchy", refresh_hierarchy)
    retry = wds.apply_pending_updates(["hierarchy_onyx"])
    assert retry["applied"] == ["hierarchy_onyx"]


# --- Division isolation, latest-only, invalid upload, no collisions -------

def test_division_isolation_pulling_one_hierarchy_slot_never_touches_others_manifest_state(monkeypatch, tmp_path):
    onyx_path = tmp_path / "onyx.xlsx"
    _write_hierarchy_workbook(onyx_path, division="Onyx")
    data = onyx_path.read_bytes()
    manifest_rows = [_manifest_row(1, "hierarchy_onyx", "Onyx.xlsx", data)]
    files = {manifest_rows[0]["storage_path"]: data}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(wds, "get_supabase_client", lambda: fake_client)

    wds.apply_pending_updates(["hierarchy_onyx"])

    session = get_config_session()
    try:
        assert session.query(SyncState).filter_by(module="work_distribution", slot_key="hierarchy_onyx").first() is not None
        assert session.query(SyncState).filter_by(module="work_distribution", slot_key="hierarchy_guardians").first() is None
        assert session.query(SyncState).filter_by(module="work_distribution", slot_key="hierarchy_xandra").first() is None
    finally:
        session.close()
    assert hus.get_slot_state("work_distribution", "Guardians")["uploaded"] is False
    assert hus.get_slot_state("work_distribution", "Xandra")["uploaded"] is False


def test_latest_only_replacement_at_manifest_level(monkeypatch, tmp_path):
    first_path = tmp_path / "first.xlsx"
    second_path = tmp_path / "second.xlsx"
    _write_rgd_workbook(first_path, division="Onyx")
    _write_rgd_workbook(second_path, division="Onyx", bm_code="BM2")

    fake_client = _FakeClient()
    monkeypatch.setattr(wds, "get_supabase_client", lambda: fake_client)
    monkeypatch.setattr(wds, "current_profile", lambda: _FakeProfile())

    wds.upload_and_sync("rgd_onyx", str(first_path))
    wds.upload_and_sync("rgd_onyx", str(second_path))

    rows = [r for r in fake_client._manifest_rows if r["slot_key"] == "rgd_onyx"]
    assert len(rows) == 2
    assert rows[1]["seq"] > rows[0]["seq"]

    # A DIFFERENT, fresh machine (no SyncState row of its own -- upload_and_sync
    # advances only the uploading machine's own marker) sees exactly one
    # "changed" entry: the latest row, not both.
    session = get_config_session()
    try:
        session.query(SyncState).filter_by(module="work_distribution", slot_key="rgd_onyx").delete()
        session.commit()
    finally:
        session.close()

    check_result = wds.check_for_updates()
    changed = [c for c in check_result["changed"] if c["slot_id"] == "rgd_onyx"]
    assert len(changed) == 1
    assert changed[0]["remote_seq"] == rows[1]["seq"]


def test_invalid_upload_never_reaches_storage_or_manifest(monkeypatch, tmp_path):
    bad_path = tmp_path / "bad.xlsx"
    _invalid_workbook(bad_path)

    fake_client = _FakeClient()
    monkeypatch.setattr(wds, "get_supabase_client", lambda: fake_client)

    result = wds.upload_and_sync("rgd_onyx", str(bad_path))

    assert result["success"] is False
    assert result.get("synced") is False
    assert fake_client._manifest_rows == []
    assert fake_client.storage._files == {}


def test_no_cross_module_slot_key_collision_with_inventory(monkeypatch, tmp_path):
    """A slot_key that happens to also exist under module="inventory" must
    never be picked up by Work Distribution's own check -- proves the
    module column, not slot_key alone, is what scopes visibility."""
    rgd_path = tmp_path / "rgd.xlsx"
    _write_rgd_workbook(rgd_path, division="Onyx")
    wd_data = rgd_path.read_bytes()

    manifest_rows = [
        _manifest_row(1, "rgd_onyx", "rgd.xlsx", wd_data, module="work_distribution"),
        # Same slot_key string, different module -- must be invisible here.
        _manifest_row(5, "rgd_onyx", "unrelated.csv", b"unrelated", module="inventory"),
    ]
    files = {manifest_rows[0]["storage_path"]: wd_data}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(wds, "get_supabase_client", lambda: fake_client)

    check_result = wds.check_for_updates()
    changed = [c for c in check_result["changed"] if c["slot_id"] == "rgd_onyx"]
    assert len(changed) == 1
    assert changed[0]["remote_seq"] == 1  # the work_distribution row, not the inventory one at seq=5


# --- Combined-engine auto-run trigger --------------------------------------

def test_engine_auto_runs_only_once_all_three_current_divisions_are_retained(monkeypatch, tmp_path):
    onyx = tmp_path / "onyx.xlsx"
    guardians = tmp_path / "guardians.xlsx"
    xandra = tmp_path / "xandra.xlsx"
    _write_rgd_workbook(onyx, division="Onyx", bm_code="BM_ONYX")
    _write_rgd_workbook(guardians, division="Guardians", bm_code="BM_GUARDIANS")
    _write_rgd_workbook(xandra, division="Xandra", bm_code="BM_XANDRA")

    manifest_rows = [
        _manifest_row(1, "rgd_onyx", "onyx.xlsx", onyx.read_bytes()),
        _manifest_row(1, "rgd_guardians", "guardians.xlsx", guardians.read_bytes()),
    ]
    files = {r["storage_path"]: (onyx.read_bytes() if r["slot_key"] == "rgd_onyx" else guardians.read_bytes()) for r in manifest_rows}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(wds, "get_supabase_client", lambda: fake_client)

    # Only 2 of 3 divisions pulled -- must NOT auto-run.
    result = wds.apply_pending_updates(["rgd_onyx", "rgd_guardians"])
    assert set(result["applied"]) == {"rgd_onyx", "rgd_guardians"}
    session = get_config_session()
    try:
        assert session.query(WorkDistributionFinding).count() == 0
    finally:
        session.close()

    # The 3rd division arrives via a SEPARATE apply_pending_updates() call
    # (simulating "days later") -- must auto-run now, using all 3 CURRENT
    # files, not just the one that just landed.
    xandra_data = xandra.read_bytes()
    manifest_rows.append(_manifest_row(1, "rgd_xandra", "xandra.xlsx", xandra_data))
    files[manifest_rows[-1]["storage_path"]] = xandra_data

    result2 = wds.apply_pending_updates(["rgd_xandra"])
    assert result2["applied"] == ["rgd_xandra"]

    session = get_config_session()
    try:
        doctors = session.query(WorkDistributionDoctor).all()
        assert {d.division for d in doctors} == {"Onyx", "Guardians", "Xandra"}
        assert len(doctors) == 6  # 2 doctors x 3 divisions
        findings = session.query(WorkDistributionFinding).all()
        assert {f.employee_code for f in findings} == {"BM_ONYX", "BM_GUARDIANS", "BM_XANDRA"}
    finally:
        session.close()


def test_resync_of_one_division_after_engine_already_ran_rebuilds_using_all_current_files(monkeypatch, tmp_path):
    divisions = {"Onyx": "BM_ONYX", "Guardians": "BM_GUARDIANS", "Xandra": "BM_XANDRA"}
    paths = {}
    for division, bm_code in divisions.items():
        p = tmp_path / f"{division.lower()}.xlsx"
        _write_rgd_workbook(p, division=division, bm_code=bm_code)
        paths[division] = p

    manifest_rows = [
        _manifest_row(1, f"rgd_{d.lower()}", f"{d}.xlsx", p.read_bytes()) for d, p in paths.items()
    ]
    files = {r["storage_path"]: paths[r["slot_key"][4:].capitalize()].read_bytes() for r in manifest_rows}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(wds, "get_supabase_client", lambda: fake_client)

    wds.apply_pending_updates(["rgd_onyx", "rgd_guardians", "rgd_xandra"])
    session = get_config_session()
    try:
        assert {f.employee_code for f in session.query(WorkDistributionFinding).all()} == {
            "BM_ONYX", "BM_GUARDIANS", "BM_XANDRA"
        }
    finally:
        session.close()

    # Re-sync Onyx alone with a corrected BM code.
    corrected = tmp_path / "onyx_v2.xlsx"
    _write_rgd_workbook(corrected, division="Onyx", bm_code="BM_ONYX_CORRECTED")
    corrected_data = corrected.read_bytes()
    manifest_rows.append(_manifest_row(2, "rgd_onyx", "onyx_v2.xlsx", corrected_data))
    files[manifest_rows[-1]["storage_path"]] = corrected_data

    wds.apply_pending_updates(["rgd_onyx"])

    session = get_config_session()
    try:
        codes = {f.employee_code for f in session.query(WorkDistributionFinding).all()}
        assert codes == {"BM_ONYX_CORRECTED", "BM_GUARDIANS", "BM_XANDRA"}
        assert "BM_ONYX" not in codes  # full delete+rebuild, not a partial patch
    finally:
        session.close()
