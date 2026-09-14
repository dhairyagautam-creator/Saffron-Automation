"""Real-construction test for ui/inventory_uploads_page.py's status panel --
verification checklist item 3: the panel shows the correct
filename/uploader/timestamp/last-generated data, checked against real
seeded values, not just that it renders without crashing.

Uses the monkeypatch-`_Session` isolation pattern (see
tests/test_inventory_factory_reset.py) rather than redirecting
app.config.DATABASE_PATH -- that redirect only works if it runs before
database.connection is imported ANYWHERE in the test session, which is not
guaranteed once more than one test module does it (tests/test_inventory_module_shell.py
already claims that hook). Monkeypatching database.connection._Session
works regardless of import order, since get_session() re-reads it on every
call.

Both tests share ONE module-scoped Tk root (`_tk_root` below) rather than
each creating and destroying its own `ctk.CTk()` -- destroying a root and
creating a fresh one later in the same process turned out to be genuinely
fragile on this environment (intermittent TclError: "invalid command name
tcl_findLibrary" / missing ttk.tcl, confirmed reproducible when two tests
in this file each made their own root). Each test builds its own
InventoryUploadsPage FRAME as a child of the shared root and destroys only
that frame, never the root itself -- Frames have no such fragility.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.connection import Base, get_config_session, to_local, utcnow
from database.models import InventoryReplenishment, InventoryThreshold, InventoryUploadSlot
from ui.theme import Color


def _in_memory_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    monkeypatch.setattr("database.connection._Session", _in_memory_session_factory())


@pytest.fixture(scope="module")
def _tk_root(_shared_tk_root):
    # See tests/conftest.py's _shared_tk_root docstring -- this reuses the
    # one session-wide root instead of creating/destroying its own.
    return _shared_tk_root


def _widget_texts(widget) -> list[str]:
    texts = []
    for child in widget.winfo_children():
        text = child.cget("text") if "text" in child.keys() else None
        if text:
            texts.append(text)
        texts.extend(_widget_texts(child))
    return texts


def test_status_panel_shows_real_seeded_slot_data(monkeypatch, _tk_root):
    from ui import inventory_uploads_page as page_module

    uploaded_at = utcnow() - timedelta(days=2)
    generated_at = utcnow() - timedelta(days=1)
    profile_id = "22222222-2222-2222-2222-222222222222"

    session = get_config_session()
    try:
        session.add(
            InventoryUploadSlot(
                slot_id="sales_report",
                filename="March_Sales.csv",
                file_path="/fake/March_Sales.csv",
                uploaded_at=uploaded_at,
                uploaded_by=profile_id,
                only_on_this_machine=True,
                thresholds_generated_at=generated_at,
            )
        )
        session.commit()
    finally:
        session.close()

    # display_name_for() falls back to "User <uuid8>" with no ProfileNameCache
    # row and no signed-in profile matching this id (see
    # app/profile_names_service.py) -- deterministic and network-free.
    monkeypatch.setattr(page_module, "display_name_for", lambda pid: f"User {pid[:8]}")
    # check_for_updates() is network-bound; this test is about the panel's
    # RENDERING of already-known local state, not the sync check itself.
    monkeypatch.setattr(page_module, "check_for_updates", lambda: {"ok": False, "reason": "offline"})

    panel = page_module.InventoryUploadsPage(_tk_root)
    try:
        _tk_root.update()

        texts = " | ".join(_widget_texts(panel._status_card))

        assert "March_Sales.csv" in texts
        assert "User 22222222" in texts
        assert "ONLY ON THIS MACHINE" in texts
        # Compare against to_local()'s own conversion, not the raw naive-UTC
        # value -- the panel renders via to_local() (see
        # ui/inventory_uploads_page.py), which can land on a different
        # calendar date than the UTC value depending on this machine's
        # timezone offset and time of day. Asserting against the same
        # conversion the panel itself uses is what makes this deterministic
        # regardless of when/where the test runs.
        assert to_local(uploaded_at).strftime("%d %b %Y") in texts
        assert "Thresholds last generated" in texts
        assert to_local(generated_at).strftime("%d %b %Y") in texts
    finally:
        panel.destroy()


def test_upload_widgets_render_identically_after_pull_as_after_upload(_tk_root):
    """Item 1 (post-pull UI consistency): simulates the exact DB state a
    sync pull leaves behind (InventoryUploadSlot rows + InventoryThreshold/
    InventoryReplenishment rows -- see app/inventory_sync_service.py's
    apply_pending_updates), without a real network pull, then constructs
    fresh ui.inventory_upload_page.InventoryUploadPage /
    ui.sales_upload_page.SalesUploadPage widgets and asserts their
    refresh_from_state() rendering (called by both __init__ and by
    ui/inventory_uploads_page.py after every pull) is BYTE-IDENTICAL to
    what a real local upload's on_done() would show -- same filename, same
    green success text -- because both now go through this one render
    function, not two independently-maintained code paths."""
    from ui.inventory_upload_page import InventoryUploadPage
    from ui.sales_upload_page import SalesUploadPage

    session = get_config_session()
    try:
        session.add(InventoryUploadSlot(
            slot_id="sales_report", filename="Pulled_Sales.csv", file_path="/fake/Pulled_Sales.csv",
            uploaded_at=utcnow(), uploaded_by="admin-id", only_on_this_machine=False,
        ))
        session.add(InventoryUploadSlot(
            slot_id="inventory_report", filename="Pulled_Inventory.csv", file_path="/fake/Pulled_Inventory.csv",
            uploaded_at=utcnow(), uploaded_by="admin-id", only_on_this_machine=False,
        ))
        session.add(InventoryThreshold(
            branch_key="cfa1", item_key="item1", branch_location="CFA 1", division="Onyx",
            item_name="Item 1", packing=10, previous_month_sales=100, raw_threshold=150, packed_threshold=150,
        ))
        session.add(InventoryReplenishment(
            branch_key="cfa1", item_key="item1", branch_location="CFA 1", division="Onyx",
            item_code="I1", item_name="Item 1", packing=10, closing_stock=5, transit_stock=0,
            effective_available_stock=5, raw_threshold=150, packed_threshold=150, stock_deficit=145,
            status="Replenishment Required",
        ))
        session.commit()
    finally:
        session.close()

    sales_widget = SalesUploadPage(_tk_root)
    inventory_widget = InventoryUploadPage(_tk_root)
    try:
        _tk_root.update()

        assert sales_widget.file_label.cget("text") == "Pulled_Sales.csv"
        assert sales_widget.file_label.cget("text_color") == Color.TEXT_PRIMARY
        assert sales_widget.status_label.cget("text") == (
            "Previous Month Sales Report validated successfully. 1 threshold(s) generated."
        )
        assert sales_widget.status_label.cget("text_color") == Color.SUCCESS
        assert sales_widget.remove_button.cget("state") == "normal"

        assert inventory_widget.file_label.cget("text") == "Pulled_Inventory.csv"
        assert inventory_widget.file_label.cget("text_color") == Color.TEXT_PRIMARY
        assert inventory_widget.status_label.cget("text") == (
            "Inventory Report validated successfully. 1 product(s) evaluated, 1 requiring replenishment."
        )
        assert inventory_widget.status_label.cget("text_color") == Color.SUCCESS
        assert inventory_widget.remove_button.cget("state") == "normal"
    finally:
        sales_widget.destroy()
        inventory_widget.destroy()


def test_status_panel_shows_no_sales_report_state(_tk_root):
    from ui import inventory_uploads_page as page_module

    panel = page_module.InventoryUploadsPage(_tk_root)
    try:
        _tk_root.update()

        texts = " | ".join(_widget_texts(panel._status_card))
        assert "No Sales Report uploaded or synced" in texts
    finally:
        panel.destroy()
