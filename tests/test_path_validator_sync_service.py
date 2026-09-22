"""Pass 1 (fake-Supabase-client, automated) tests for
app/path_validator_sync_service.py -- the full 6-slot Path Validator sync
build (3 daily-report slots, 3 hierarchy slots).

Covers the behaviors genuinely new in this build (see that module's own
docstring for the reasoning):
- All 6 slots register under module="path_validator".
- The hierarchy module-key fix (app/hierarchy_upload_service.py) never
  collides with Work Distribution's own identically-divisioned hierarchy
  slots, on disk or in the database.
- The hard hierarchy gate blocks BOTH the combined auto-run and correctly
  reports itself closed/open (hierarchy_gate_open()).
- A single division's daily-report pull never triggers analysis alone; a
  pull that completes all 3 divisions AND has the gate open auto-runs
  analysis exactly once, appending a new import_id without touching any
  prior one (raw_visits/import_history stay additive -- no deletion).
- No email-sending code path is reachable from this module.

Uses tests/db_isolation.py's isolate_database() (both _Session and
_engine) plus redirects both retention directories to temp dirs.
"""

import hashlib

import openpyxl
import pandas as pd
import pytest

import app.hierarchy_upload_service as hus
import app.path_validator_sync_service as pvs
import app.path_validator_upload_service as pvus
import app.work_distribution_sync_service as wds
import app.work_distribution_upload_service as wdus
from database.connection import get_config_session
from database.models import HierarchyUploadSlot, ImportHistory, SyncState
from tests.db_isolation import isolate_database

_PV_HIERARCHY_MODULE_KEY = "employee_module"


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path_factory):
    isolate_database(monkeypatch)
    monkeypatch.setattr(pvus, "PATH_VALIDATOR_UPLOADS_DIR", tmp_path_factory.mktemp("pv_retained"))
    monkeypatch.setattr(hus, "HIERARCHY_UPLOADS_DIR", tmp_path_factory.mktemp("hierarchy_retained"))
    # Work Distribution's own retention dir too -- several tests below
    # exercise BOTH modules together to prove no cross-module collision.
    monkeypatch.setattr(wdus, "WORK_DISTRIBUTION_UPLOADS_DIR", tmp_path_factory.mktemp("wd_retained"))


REQUIRED_COLUMNS = [
    "Division", "Zone", "Region", "Employee Name", "Employee Code", "Reporting HQ", "Desig.",
    "Reporting Sr. Name", "Reporting Sr. Emp Code", "Reporting Sr. Desig.", "Date", "Day",
    "Customer Type", "Code", "Name", "Category", "Specialty", "City/Place",
    "Visit Registration Time", "Visit Registration Date", "Actual Lat-Long",
]


def _write_daily_report_workbook(path, division="Onyx", emp_code="E1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(REQUIRED_COLUMNS)
    row = {
        "Division": division, "Zone": "Z1", "Region": "R1", "Employee Name": "Emp One",
        "Employee Code": emp_code, "Reporting HQ": "HQ1", "Desig.": "BM",
        "Reporting Sr. Name": "Sr One", "Reporting Sr. Emp Code": "S1", "Reporting Sr. Desig.": "RBM",
        "Date": "15-07-2026", "Day": "Wednesday", "Customer Type": "Doctor", "Code": "D1",
        "Name": "Dr One", "Category": "A", "Specialty": "Cardio", "City/Place": "City1",
        "Visit Registration Time": "10:00", "Visit Registration Date": "15-07-2026",
        "Actual Lat-Long": "19.0760,72.8777",
    }
    ws.append([row[c] for c in REQUIRED_COLUMNS])
    wb.save(path)


def _write_invalid_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Not", "The", "Right", "Columns"])
    ws.append([1, 2, 3, 4])
    wb.save(path)


def _write_hierarchy_workbook(path, division="Onyx", emp_code="E1"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Emp Code", "Name", "Designation", "Mobile", "Email", "DOJ"])
    ws.append([emp_code, "Emp One", "BM", "9990000000", "emp1@example.com", ""])
    wb.save(path)


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
        row = {**self._payload, "seq": seq, "uploaded_at": "2026-09-15T00:00:00+00:00"}
        self._backing_rows.append(row)
        return _FakeResponse([row])


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

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


def _manifest_row(seq, slot_key, filename, data: bytes, module="path_validator", app_version="0.0.1") -> dict:
    return {
        "seq": seq,
        "module": module,
        "slot_key": slot_key,
        "storage_path": f"{module}/{slot_key}/{hashlib.sha256(data).hexdigest()}.xlsx",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "filename": filename,
        "app_version": app_version,
        "uploaded_by": "11111111-1111-1111-1111-111111111111",
        "uploaded_at": "2026-09-11T00:00:00+00:00",
    }


# --- 1. Slot registration ---------------------------------------------------

def test_all_six_slots_defined_under_one_module():
    assert len(pvs.ALL_SLOTS) == 6
    assert len(set(pvs.ALL_SLOTS)) == 6
    assert set(pvs.DAILY_REPORT_SLOTS) == {"daily_report_onyx", "daily_report_guardians", "daily_report_xandra"}
    assert set(pvs.HIERARCHY_SLOTS) == {"hierarchy_onyx", "hierarchy_guardians", "hierarchy_xandra"}
    assert pvs.MODULE == "path_validator"
    # Confirmed against app/hierarchy_parser.py's actual HIERARCHY_TABLES --
    # NOT the same string as MODULE, a real mistake this build had to avoid.
    assert pvs._HIERARCHY_MODULE_KEY == "employee_module"


# --- 2. Hierarchy module-key fix: no collision with Work Distribution ------

def test_path_validator_hierarchy_slot_never_collides_with_work_distribution(monkeypatch, tmp_path):
    """Both modules use the identical slot_id string "hierarchy_onyx" --
    proves the module_key column + module-prefixed filename actually keep
    them apart, on disk and in the database."""
    wd_file = tmp_path / "wd_onyx.xlsx"
    pv_file = tmp_path / "pv_onyx.xlsx"
    _write_hierarchy_workbook(wd_file, division="Onyx", emp_code="WD-BM1")
    _write_hierarchy_workbook(pv_file, division="Onyx", emp_code="PV-BM1")

    monkeypatch.setattr(wds, "current_profile", lambda: _FakeProfile())
    monkeypatch.setattr(pvs, "current_profile", lambda: _FakeProfile())
    monkeypatch.setattr(wds, "get_supabase_client", lambda: _FakeClient())
    monkeypatch.setattr(pvs, "get_supabase_client", lambda: _FakeClient())

    wds.upload_and_sync("hierarchy_onyx", str(wd_file))
    pvs.upload_and_sync("hierarchy_onyx", str(pv_file))

    wd_state = hus.get_slot_state("work_distribution", "Onyx")
    pv_state = hus.get_slot_state("employee_module", "Onyx")
    assert wd_state["uploaded"] is True
    assert pv_state["uploaded"] is True
    assert wd_state["file_path"] != pv_state["file_path"]  # different files on disk

    # Both physical files independently exist with their own real content.
    from pathlib import Path

    assert Path(wd_state["file_path"]).read_bytes() == wd_file.read_bytes()
    assert Path(pv_state["file_path"]).read_bytes() == pv_file.read_bytes()

    session = get_config_session()
    try:
        rows = session.query(HierarchyUploadSlot).filter_by(slot_id="hierarchy_onyx").all()
        assert len(rows) == 2
        assert {r.module_key for r in rows} == {"work_distribution", "employee_module"}
    finally:
        session.close()


def test_work_distribution_hierarchy_pull_unaffected_by_path_validator_existing(monkeypatch, tmp_path):
    """A Work Distribution hierarchy pull for Onyx still works correctly
    even when Path Validator already has its OWN Onyx hierarchy slot
    filled -- proves check_for_updates()/apply_pending_updates() on each
    side only ever see their own module's manifest rows."""
    from app.hierarchy_parser import refresh_hierarchy

    pv_file = tmp_path / "pv_onyx.xlsx"
    _write_hierarchy_workbook(pv_file, division="Onyx", emp_code="PV-BM1")
    monkeypatch.setattr(pvs, "current_profile", lambda: _FakeProfile())
    monkeypatch.setattr(pvs, "get_supabase_client", lambda: _FakeClient())
    pvs.upload_and_sync("hierarchy_onyx", str(pv_file))
    # upload_and_sync (local upload path) never auto-refreshes -- that
    # stays a separate manual action, same as today. Refresh explicitly,
    # matching what a real user would do next.
    refresh_hierarchy(_PV_HIERARCHY_MODULE_KEY)

    wd_file = tmp_path / "wd_onyx.xlsx"
    _write_hierarchy_workbook(wd_file, division="Onyx", emp_code="WD-BM1")
    data = wd_file.read_bytes()
    manifest_rows = [_manifest_row(1, "hierarchy_onyx", "wd_onyx.xlsx", data, module="work_distribution")]
    files = {manifest_rows[0]["storage_path"]: data}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(wds, "get_supabase_client", lambda: fake_client)

    result = wds.apply_pending_updates(["hierarchy_onyx"])
    assert result["applied"] == ["hierarchy_onyx"]

    from app.hierarchy_parser import find_by_employee_code
    assert find_by_employee_code("work_distribution", "WD-BM1") is not None
    assert find_by_employee_code("employee_module", "PV-BM1") is not None


# --- 3. Hard hierarchy gate --------------------------------------------------

def test_gate_closed_with_no_hierarchy_table_at_all():
    assert pvs.hierarchy_gate_open() is False


def test_gate_opens_once_hierarchy_is_populated(tmp_path):
    from app.hierarchy_parser import refresh_hierarchy
    from app.workbook_connections import set_connection

    hier_path = tmp_path / "onyx.xlsx"
    _write_hierarchy_workbook(hier_path, division="Onyx", emp_code="E1")
    set_connection(_PV_HIERARCHY_MODULE_KEY, "Onyx", str(hier_path))
    refresh_hierarchy(_PV_HIERARCHY_MODULE_KEY)

    assert pvs.hierarchy_gate_open() is True


def test_auto_run_blocked_when_gate_closed_even_with_all_three_divisions_present(monkeypatch, tmp_path):
    for division in ("Onyx", "Guardians", "Xandra"):
        p = tmp_path / f"{division}.xlsx"
        _write_daily_report_workbook(p, division=division)
        pvus.store_daily_report_upload(division, str(p))

    assert pvs.hierarchy_gate_open() is False
    pvs._maybe_process_analysis()

    session = get_config_session()
    try:
        assert session.query(ImportHistory).count() == 0
    finally:
        session.close()


def test_auto_run_proceeds_once_gate_opens_after_all_three_already_retained(tmp_path):
    """All 3 daily reports arrive (gate closed, nothing runs), THEN
    hierarchy gets synced/refreshed -- the next check must auto-run using
    the CURRENT retained daily reports, not require re-uploading them."""
    from app.hierarchy_parser import refresh_hierarchy
    from app.workbook_connections import set_connection

    for division in ("Onyx", "Guardians", "Xandra"):
        p = tmp_path / f"{division}.xlsx"
        _write_daily_report_workbook(p, division=division, emp_code=f"E-{division}")
        pvus.store_daily_report_upload(division, str(p))

    pvs._maybe_process_analysis()
    session = get_config_session()
    try:
        assert session.query(ImportHistory).count() == 0
    finally:
        session.close()

    hier_path = tmp_path / "onyx_hierarchy.xlsx"
    _write_hierarchy_workbook(hier_path, division="Onyx", emp_code="E-Onyx")
    set_connection(_PV_HIERARCHY_MODULE_KEY, "Onyx", str(hier_path))
    refresh_hierarchy(_PV_HIERARCHY_MODULE_KEY)
    assert pvs.hierarchy_gate_open() is True

    pvs._maybe_process_analysis()
    session = get_config_session()
    try:
        assert session.query(ImportHistory).count() == 1
        history = session.query(ImportHistory).first()
        assert history.rows_imported == 3  # one row per division
    finally:
        session.close()


# --- 4. Single-division pull never triggers analysis alone -----------------

def test_single_division_pull_never_triggers_analysis(monkeypatch, tmp_path):
    from app.hierarchy_parser import refresh_hierarchy
    from app.workbook_connections import set_connection

    hier_path = tmp_path / "onyx_hierarchy.xlsx"
    _write_hierarchy_workbook(hier_path, division="Onyx", emp_code="E1")
    set_connection(_PV_HIERARCHY_MODULE_KEY, "Onyx", str(hier_path))
    refresh_hierarchy(_PV_HIERARCHY_MODULE_KEY)
    assert pvs.hierarchy_gate_open() is True

    onyx_report = tmp_path / "onyx_report.xlsx"
    _write_daily_report_workbook(onyx_report, division="Onyx")
    data = onyx_report.read_bytes()
    manifest_rows = [_manifest_row(1, "daily_report_onyx", "onyx_report.xlsx", data)]
    files = {manifest_rows[0]["storage_path"]: data}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(pvs, "get_supabase_client", lambda: fake_client)

    result = pvs.apply_pending_updates(["daily_report_onyx"])
    assert result["applied"] == ["daily_report_onyx"]

    session = get_config_session()
    try:
        assert session.query(ImportHistory).count() == 0  # still waiting on 2 more divisions
    finally:
        session.close()


# --- 5. Completing all 3 (gate open) auto-runs; additive, no deletion ------

def test_completing_all_three_divisions_auto_runs_and_never_deletes_prior_import(monkeypatch, tmp_path):
    from app.hierarchy_parser import refresh_hierarchy
    from app.workbook_connections import set_connection
    from database.import_service import save_import

    hier_path = tmp_path / "onyx_hierarchy.xlsx"
    _write_hierarchy_workbook(hier_path, division="Onyx", emp_code="E1")
    set_connection(_PV_HIERARCHY_MODULE_KEY, "Onyx", str(hier_path))
    refresh_hierarchy(_PV_HIERARCHY_MODULE_KEY)

    # A prior, unrelated import already exists -- must survive untouched.
    # Goes through parse_coordinates() first, same as every real import
    # (including this test's own auto-run below) -- raw_visits' schema is
    # created from whichever DataFrame's columns save_import() sees FIRST
    # (to_sql(if_exists="append") on a fresh table), so skipping this step
    # here would create an unrealistic latitude/longitude-less table no
    # real import ever produces.
    from app.coordinates import parse_coordinates

    prior_df = pd.DataFrame([{c: "x" for c in REQUIRED_COLUMNS}])
    parse_coordinates(prior_df)
    prior_stats = save_import(prior_df, "prior_import.xlsx")
    prior_import_id = prior_stats["import_id"]

    manifest_rows = []
    files = {}
    for division in ("Onyx", "Guardians", "Xandra"):
        p = tmp_path / f"{division}.xlsx"
        _write_daily_report_workbook(p, division=division, emp_code=f"E-{division}")
        data = p.read_bytes()
        row = _manifest_row(1, f"daily_report_{division.lower()}", f"{division}.xlsx", data)
        manifest_rows.append(row)
        files[row["storage_path"]] = data

    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(pvs, "get_supabase_client", lambda: fake_client)

    slot_ids = [f"daily_report_{d.lower()}" for d in ("Onyx", "Guardians", "Xandra")]
    result = pvs.apply_pending_updates(slot_ids)
    assert set(result["applied"]) == set(slot_ids)

    session = get_config_session()
    try:
        histories = session.query(ImportHistory).order_by(ImportHistory.id).all()
        assert len(histories) == 2  # prior + the new auto-run one
        assert histories[0].id == prior_import_id  # prior untouched, not deleted
        new_import = histories[1]
        assert new_import.rows_imported == 3
    finally:
        session.close()

    from app.session_state import get_active_import_id
    assert get_active_import_id() == histories[1].id  # new import became active


# --- 6. No email-sending code path reachable --------------------------------

def test_no_email_sending_import_anywhere_in_this_module():
    """AST-based, not a substring search on the raw source -- this
    module's own docstring deliberately NAMES notification_service (to
    explain it's NOT imported), which a naive `"notification_service" not
    in source` check would trip over as a false failure."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(pvs))
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
            imported_names.update(f"{node.module}.{alias.name}" for alias in node.names)

    assert not any("notification_service" in name for name in imported_names)
    assert "send_all_emails" not in pvs.__dict__
    assert "send_notification_batch" not in pvs.__dict__


# --- Invalid upload never reaches Storage/manifest -------------------------

def test_invalid_daily_report_upload_never_reaches_storage_or_manifest(monkeypatch, tmp_path):
    bad_path = tmp_path / "bad.xlsx"
    _write_invalid_workbook(bad_path)
    fake_client = _FakeClient()
    monkeypatch.setattr(pvs, "get_supabase_client", lambda: fake_client)

    result = pvs.upload_and_sync("daily_report_onyx", str(bad_path))

    assert result["success"] is False
    assert result.get("synced") is False
    assert fake_client._manifest_rows == []
    assert fake_client.storage._files == {}
