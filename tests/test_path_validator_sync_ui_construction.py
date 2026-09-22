"""Real Tk construction + on_show() smoke test for the two pages this
build's sync UI landed in -- ui/operations_page.py (3 daily-report slots'
banner + the hard hierarchy gate banner) and ui/organization_data_page.py
(3 hierarchy slots' banner).

run_in_background() is monkeypatched to run synchronously -- same
established pattern as tests/test_work_distribution_sync_ui_construction.py
uses, for the same reason (real background-thread-plus-Tk-.after()
completion is flaky to poll for reliably in a test process; the ON_DONE
LOGIC is what's worth proving, not Tkinter's own thread-marshalling).

Uses tests/db_isolation.py's isolate_database() (both _Session and
_engine) plus the shared _shared_tk_root fixture from tests/conftest.py.
"""

import pytest

from tests.db_isolation import isolate_database


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    isolate_database(monkeypatch)


@pytest.fixture(scope="module")
def _tk_root(_shared_tk_root):
    return _shared_tk_root


def _run_synchronously(_widget, work_fn, on_progress=None, on_done=None):
    try:
        result = work_fn(lambda *_a: None)
    except Exception as exc:
        if on_done is not None:
            on_done(None, exc)
    else:
        if on_done is not None:
            on_done(result, None)


def test_operations_page_shows_hard_gate_banner_when_hierarchy_empty(_tk_root, monkeypatch):
    import app.path_validator_sync_service as pvs
    import ui.operations_page as page_module

    monkeypatch.setattr(pvs, "get_supabase_client", lambda: (_ for _ in ()).throw(RuntimeError("not signed in")))
    monkeypatch.setattr(page_module, "run_in_background", _run_synchronously)

    page = page_module.OperationsPage(_tk_root)
    try:
        _tk_root.update()
        # Hard gate: no hierarchy at all on a fresh isolated database --
        # unconditional, checked on every on_show(), including construction.
        assert "Organization Data hierarchy is empty" in page._gate_banner.cget("text")

        # The sync check itself only ever runs via an explicit Refresh
        # click (see ui/operations_page.py's own on_show() docstring) --
        # calling it directly here, exactly what clicking Refresh does.
        page._run_check()
        _tk_root.update()
        assert page._checking is False
        assert "Last check failed" in page._sync_status_label.cget("text")
    finally:
        page.destroy()


def test_operations_page_on_show_never_triggers_a_sync_check(_tk_root, monkeypatch):
    """Checking the manifest is Refresh-button-only, by explicit design --
    on_show() (fired on every page visit/navigation, not just construction)
    must never auto-check. Confirmed via a real user report: a file synced
    on one machine appeared to get picked up on another with no Refresh/
    Pull Updates click at all, simply from having the page open."""
    import app.path_validator_sync_service as pvs
    import ui.operations_page as page_module

    monkeypatch.setattr(page_module, "run_in_background", _run_synchronously)
    calls = []
    monkeypatch.setattr(pvs, "get_supabase_client", lambda: calls.append(1) or (_ for _ in ()).throw(RuntimeError("should not be called")))

    page = page_module.OperationsPage(_tk_root)
    try:
        _tk_root.update()
        page.on_show()  # simulates navigating back to this page
        page.on_show()
        _tk_root.update()
        assert calls == [], "on_show() triggered a sync check -- checking must be Refresh-button-only"
    finally:
        page.destroy()


def test_operations_page_construction_never_touches_supabase(_tk_root, monkeypatch):
    """Regression test for a real incident: OperationsPage.__init__()
    already unconditionally called its own on_show() (for local history
    refresh) before this build existed. Wiring the sync check into that
    same on_show() meant EVERY app launch made a real network call during
    ui/path_validator_module.py's eager construction of every page --
    inside ui/main_window.py's MainWindow.__init__(), before the window
    had even appeared. On a slow network this looked exactly like the app
    hanging on startup with no window and no explanation. Confirmed the
    hard way with a real timestamped trace showing the network call
    firing 8+ seconds before MainWindow() had finished constructing, even
    with an after_idle()/after(0, ...) defer -- customtkinter's own
    internal update_idletasks() calls during widget construction flush
    queued idle/after callbacks immediately, synchronously, so that defer
    does not actually wait for mainloop(). get_supabase_client itself must
    never be called at all during __init__ -- not "called and returns
    fast", not "scheduled for later" -- literally never invoked until a
    real on_show() happens after construction has fully returned."""
    import app.path_validator_sync_service as pvs
    import ui.operations_page as page_module

    calls = []
    monkeypatch.setattr(pvs, "get_supabase_client", lambda: calls.append(1) or (_ for _ in ()).throw(RuntimeError("should not be called")))

    page = page_module.OperationsPage(_tk_root)
    try:
        _tk_root.update()
        _tk_root.update_idletasks()
        assert calls == [], "get_supabase_client() was called during construction -- the startup-hang bug is back"
    finally:
        page.destroy()


def test_operations_page_gate_banner_clears_once_hierarchy_populated(_tk_root, monkeypatch, tmp_path):
    import app.path_validator_sync_service as pvs
    import ui.operations_page as page_module
    from app.hierarchy_parser import refresh_hierarchy
    from app.workbook_connections import set_connection

    monkeypatch.setattr(pvs, "get_supabase_client", lambda: (_ for _ in ()).throw(RuntimeError("not signed in")))
    monkeypatch.setattr(page_module, "run_in_background", _run_synchronously)

    import openpyxl

    hier_path = tmp_path / "onyx.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Emp Code", "Name", "Designation"])
    ws.append(["E1", "Emp One", "BM"])
    wb.save(hier_path)
    set_connection("employee_module", "Onyx", str(hier_path))
    refresh_hierarchy("employee_module")

    page = page_module.OperationsPage(_tk_root)
    try:
        _tk_root.update()
        assert page._gate_banner.cget("text") == ""
    finally:
        page.destroy()


def test_run_analysis_blocked_by_gate_shows_error_not_silent(_tk_root, monkeypatch):
    from tkinter import messagebox

    import app.path_validator_sync_service as pvs
    import ui.operations_page as page_module

    monkeypatch.setattr(pvs, "get_supabase_client", lambda: (_ for _ in ()).throw(RuntimeError("not signed in")))
    monkeypatch.setattr(page_module, "run_in_background", _run_synchronously)

    shown = []
    monkeypatch.setattr(messagebox, "showerror", lambda title, msg: shown.append((title, msg)))

    page = page_module.OperationsPage(_tk_root)
    try:
        _tk_root.update()
        # Pretend all 3 divisions are loaded (bypassing real file browsing)
        # so the gate check itself is what's under test, not the loaded-count guard.
        page._loaded_dfs = {"Onyx": None, "Guardians": None, "Xandra": None}
        page._on_run_analysis_clicked()
        assert len(shown) == 1
        assert "Organization Data" in shown[0][0]
    finally:
        page.destroy()


def test_organization_data_page_hierarchy_banner_survives_no_supabase_client(_tk_root, monkeypatch):
    import app.path_validator_sync_service as pvs
    import ui.organization_data_page as page_module

    monkeypatch.setattr(pvs, "get_supabase_client", lambda: (_ for _ in ()).throw(RuntimeError("not signed in")))
    monkeypatch.setattr(page_module, "run_in_background", _run_synchronously)

    page = page_module.OrganizationDataPage(_tk_root)
    try:
        page.on_show()
        page._run_check()  # what clicking Refresh does -- on_show() alone never checks
        _tk_root.update()
        assert page._checking is False
        assert "Last check failed" in page._sync_status_label.cget("text")
    finally:
        page.destroy()


def test_organization_data_page_on_show_never_triggers_a_sync_check(_tk_root, monkeypatch):
    import app.path_validator_sync_service as pvs
    import ui.organization_data_page as page_module

    monkeypatch.setattr(page_module, "run_in_background", _run_synchronously)
    calls = []
    monkeypatch.setattr(pvs, "get_supabase_client", lambda: calls.append(1) or (_ for _ in ()).throw(RuntimeError("should not be called")))

    page = page_module.OrganizationDataPage(_tk_root)
    try:
        page.on_show()
        page.on_show()
        _tk_root.update()
        assert calls == [], "on_show() triggered a sync check -- checking must be Refresh-button-only"
    finally:
        page.destroy()
