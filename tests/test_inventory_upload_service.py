"""Tests for app/inventory_upload_service.py -- local retention + the
recompute-then-evaluate pipeline both the local upload path
(ui/inventory_upload_page.py, ui/sales_upload_page.py) and the sync
pull-apply path (app/inventory_sync_service.py) share.

Every DB operation runs against a real (in-memory) SQLite database,
exercising the actual model classes/service functions, not a
reimplementation -- same convention as tests/test_inventory_factory_reset.py.
File retention is redirected to a temp directory per test so nothing here
ever touches this machine's real inventory_uploads/ folder.
"""

import tempfile
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.inventory_upload_service as ius
from database.connection import Base, get_config_session
from database.models import InventoryReplenishment, InventoryThreshold


def _in_memory_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.setattr("database.connection._Session", _in_memory_session_factory())
    monkeypatch.setattr(ius, "INVENTORY_UPLOADS_DIR", tmp_path)


def _write_sales_csv(path: Path) -> None:
    pd.DataFrame(
        {
            "Division": ["Tablets"],
            "CFA": ["MUMBAI (HEAD OFFICE) ( MUM )"],
            "Item Name": ["Widget 30-Pack"],
            "Packing": ["30"],
            "Sales": [100],
        }
    ).to_csv(path, index=False)


def _write_inventory_csv(path: Path, total_qty: int = 50) -> None:
    pd.DataFrame(
        {
            "BranchLocation": ["MUMBAI (HEAD OFFICE) ( MUM )"],
            "Item Group": ["Tablets"],
            "Item Code": ["ITM001"],
            "Item Name": ["Widget 30-Pack"],
            "TotalQty": [total_qty],
            "Transit Stock": [0],
        }
    ).to_csv(path, index=False)


def test_store_inventory_upload_retains_file_and_records_state(tmp_path):
    source = tmp_path / "source" / "Sales.csv"
    source.parent.mkdir()
    _write_sales_csv(source)

    stored = ius.store_inventory_upload(ius.SALES_REPORT_SLOT, str(source))

    assert stored.exists()
    state = ius.get_slot_state(ius.SALES_REPORT_SLOT)
    assert state["uploaded"] is True
    assert state["filename"] == "Sales.csv"
    assert state["file_path"] == str(stored)


def test_apply_sales_report_generates_real_thresholds(tmp_path):
    source = tmp_path / "Sales.csv"
    _write_sales_csv(source)

    result = ius.apply_sales_report(str(source))

    assert result["success"] is True
    assert result["threshold_stats"]["unique_combinations"] == 1

    session = get_config_session()
    try:
        rows = session.query(InventoryThreshold).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.item_key == "widget 30-pack"
        assert row.previous_month_sales == 100.0
        assert row.packed_threshold == 150.0  # ceil(100 * 1.5 default multiplier / 30 packing) * 30
    finally:
        session.close()


def test_apply_inventory_report_with_no_sales_retained_is_silent_zero(tmp_path):
    """Confirms docs/INVENTORY_SYNC_CONTEXT.md §3 (verified by real
    execution) is unchanged for the LOCAL upload path: no Sales Report
    retained -> success, 0 evaluated, never an error. This is the
    single-machine behavior the recompute rule deliberately leaves alone
    -- only the sync PULL path (app/inventory_sync_service.py) gates on
    this condition."""
    source = tmp_path / "Inventory.csv"
    _write_inventory_csv(source)

    result = ius.apply_inventory_report(str(source))

    assert result["success"] is True
    assert result["error"] is None
    assert result["replenishment_stats"]["evaluated"] == 0
    assert result["replenishment_stats"]["skipped_no_threshold"] == 1


def test_apply_inventory_report_recomputes_thresholds_from_retained_sales(tmp_path):
    """The recompute rule, end to end: store a Sales Report (so it's
    RETAINED, not just processed once), then apply an Inventory Report --
    thresholds must be regenerated fresh from the retained file and
    produce real (non-zero) replenishment numbers, not the silent-zero
    result from the test above."""
    sales_source = tmp_path / "Sales.csv"
    _write_sales_csv(sales_source)
    ius.store_inventory_upload(ius.SALES_REPORT_SLOT, str(sales_source))
    # store_inventory_upload only retains -- it does not itself generate
    # thresholds (that's apply_sales_report's job on the upload path).
    # Recompute must work purely from what's retained on disk, so
    # deliberately do NOT call apply_sales_report/generate_thresholds here.

    inventory_source = tmp_path / "Inventory.csv"
    _write_inventory_csv(inventory_source, total_qty=50)  # 50 < packed_threshold 150 -> Replenishment Required

    result = ius.apply_inventory_report(str(inventory_source))

    assert result["success"] is True
    assert result["replenishment_stats"]["evaluated"] == 1
    assert result["replenishment_stats"]["replenishment_required"] == 1
    assert result["replenishment_stats"]["skipped_no_threshold"] == 0

    session = get_config_session()
    try:
        thresholds = session.query(InventoryThreshold).all()
        assert len(thresholds) == 1  # recompute really ran, not a stale/empty table
        replenishment = session.query(InventoryReplenishment).all()
        assert len(replenishment) == 1
        assert replenishment[0].stock_deficit == 100.0  # 150 threshold - 50 stock
    finally:
        session.close()

    sales_state = ius.get_slot_state(ius.SALES_REPORT_SLOT)
    assert sales_state["thresholds_generated_at"] is not None


def test_remove_inventory_file_is_local_only(tmp_path):
    """Same behavior as app.review_upload_service.remove_review_file:
    clears the local slot row + retained file, but never touches
    InventoryThreshold/InventoryReplenishment (no cascade) -- matches
    Review System's own Remove button semantics exactly."""
    sales_source = tmp_path / "Sales.csv"
    _write_sales_csv(sales_source)
    ius.store_inventory_upload(ius.SALES_REPORT_SLOT, str(sales_source))
    ius.apply_sales_report(str(sales_source))

    assert ius.has_sales_report_retained() is True
    session = get_config_session()
    try:
        threshold_count_before = session.query(InventoryThreshold).count()
    finally:
        session.close()
    assert threshold_count_before == 1

    stored_path = ius.get_slot_state(ius.SALES_REPORT_SLOT)["file_path"]
    assert Path(stored_path).exists()

    ius.remove_inventory_file(ius.SALES_REPORT_SLOT)

    assert ius.has_sales_report_retained() is False
    assert ius.get_slot_state(ius.SALES_REPORT_SLOT)["uploaded"] is False
    assert not Path(stored_path).exists()  # local file actually deleted

    # No cascade -- InventoryThreshold rows generated before the remove
    # are completely untouched by it.
    session = get_config_session()
    try:
        threshold_count_after = session.query(InventoryThreshold).count()
    finally:
        session.close()
    assert threshold_count_after == threshold_count_before


def test_has_sales_report_retained(tmp_path):
    assert ius.has_sales_report_retained() is False

    source = tmp_path / "Sales.csv"
    _write_sales_csv(source)
    ius.store_inventory_upload(ius.SALES_REPORT_SLOT, str(source))

    assert ius.has_sales_report_retained() is True
