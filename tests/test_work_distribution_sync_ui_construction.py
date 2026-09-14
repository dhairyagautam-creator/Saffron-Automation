"""Real Tk construction + on_show() smoke test for the two pages this
build's sync UI landed in -- ui/work_distribution_upload_page.py (9
report slots' banner) and ui/work_distribution_email_center_page.py (3
hierarchy slots' banner + advisory notes).

Neither page's existing construction test (test_hierarchy_pages_construction.py
only constructs WorkDistributionEmailCenterPage, never calls on_show())
exercises the new _run_check() path this build adds. This file does.

run_in_background() is monkeypatched to run synchronously (call work_fn
then on_done immediately, no real thread) -- same established pattern
tests/test_inventory_uploads_page_panel.py already uses for this exact
reason: real background-thread-plus-Tk-.after() completion is flaky to
poll for reliably in a test process, and the actual thing worth proving
here is the ON_DONE LOGIC (does the page handle an unreachable Supabase
client without crashing), not Tkinter's own thread-marshalling, which
ui/background_task.py already has no test-specific behavior in.

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


def test_work_distribution_upload_page_on_show_survives_no_supabase_client(_tk_root, monkeypatch):
    import app.work_distribution_sync_service as wds
    import ui.work_distribution_upload_page as page_module

    monkeypatch.setattr(wds, "get_supabase_client", lambda: (_ for _ in ()).throw(RuntimeError("not signed in")))
    monkeypatch.setattr(page_module, "run_in_background", _run_synchronously)

    page = page_module.WorkDistributionUploadPage(_tk_root)
    try:
        page.on_show()
        _tk_root.update()
        assert page._checking is False
        # RuntimeError is not a network exception (see _NETWORK_EXCEPTIONS
        # in app/work_distribution_sync_service.py) -- it lands in
        # check_for_updates()'s generic error branch, "Last check failed",
        # not the offline-specific message. Either way: no crash.
        assert "Last check failed" in page._sync_status_label.cget("text")
    finally:
        page.destroy()


def test_work_distribution_email_center_page_on_show_survives_no_supabase_client(_tk_root, monkeypatch):
    import app.work_distribution_sync_service as wds
    import ui.work_distribution_email_center_page as page_module

    monkeypatch.setattr(wds, "get_supabase_client", lambda: (_ for _ in ()).throw(RuntimeError("not signed in")))
    monkeypatch.setattr(page_module, "run_in_background", _run_synchronously)

    page = page_module.WorkDistributionEmailCenterPage(_tk_root)
    try:
        page.on_show()
        _tk_root.update()
        assert page._checking is False
        assert "Last check failed" in page._sync_status_label.cget("text")
        # The advisory note is visible for every division -- none is
        # Connected on a fresh isolated database.
        for name in ("Onyx", "Guardians", "Xandra"):
            assert bool(page.advisory_labels[name].winfo_ismapped())
    finally:
        page.destroy()


def test_advisory_note_clears_once_a_division_is_connected(_tk_root, monkeypatch, tmp_path):
    import app.work_distribution_sync_service as wds
    import ui.work_distribution_email_center_page as page_module
    from app.workbook_connections import set_connection

    monkeypatch.setattr(wds, "get_supabase_client", lambda: (_ for _ in ()).throw(RuntimeError("not signed in")))
    monkeypatch.setattr(page_module, "run_in_background", _run_synchronously)

    # get_status() checks os.path.isfile() -- a real (if empty) file is
    # needed for "Connected", not just a plausible-looking path string.
    onyx_file = tmp_path / "onyx_hierarchy.xlsx"
    onyx_file.write_bytes(b"")
    set_connection("work_distribution", "Onyx", str(onyx_file))

    page = page_module.WorkDistributionEmailCenterPage(_tk_root)
    try:
        page.on_show()
        _tk_root.update()
        assert bool(page.advisory_labels["Onyx"].winfo_ismapped()) is False
        assert bool(page.advisory_labels["Guardians"].winfo_ismapped()) is True
        assert bool(page.advisory_labels["Xandra"].winfo_ismapped()) is True
    finally:
        page.destroy()
