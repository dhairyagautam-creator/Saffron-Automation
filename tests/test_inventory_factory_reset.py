"""Tests for the one-time company-wide Inventory data factory reset
(app/inventory_factory_reset.py). Every DB operation runs against a real
(in-memory) SQLite database, exercising the actual model classes and
queries, not a second, test-only reimplementation.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.inventory_factory_reset as reset_mod
from database.connection import Base, get_config_session
from database.models import (
    AppSettings,
    CwhStock,
    InventoryEmailRecipient,
    InventoryParameter,
    InventoryReplenishment,
    InventoryThreshold,
    WorkDistributionFinding,
)


def _in_memory_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    monkeypatch.setattr("database.connection._ConfigSession", _in_memory_session_factory())


def _seed_inventory_data():
    session = get_config_session()
    try:
        session.add(InventoryThreshold(
            branch_key="cfa1", item_key="item1", branch_location="CFA 1", division="Onyx",
            item_name="Item 1", packing=10, previous_month_sales=100, raw_threshold=50, packed_threshold=50,
        ))
        session.add(InventoryReplenishment(
            branch_key="cfa1", item_key="item1", branch_location="CFA 1", division="Onyx",
            item_code="I1", item_name="Item 1", packing=10, closing_stock=5, transit_stock=0,
            effective_available_stock=5, raw_threshold=50, packed_threshold=50, stock_deficit=45,
            status="Replenishment Required",
        ))
        session.add(CwhStock(
            item_code="I1", item_name="Item 1", item_key="item1", closing_stock=5, transit_stock=0,
        ))
        session.commit()
    finally:
        session.close()


def _seed_inventory_settings():
    session = get_config_session()
    try:
        session.add(InventoryParameter(parameter_name="replenishment_multiplier", parameter_value="1.5"))
        session.add(InventoryEmailRecipient(name="Ops Team", email="ops@example.com", divisions="Onyx"))
        session.commit()
    finally:
        session.close()


def _seed_other_module_data():
    session = get_config_session()
    try:
        session.add(WorkDistributionFinding(
            employee_code="BM01", employee_name="Rahul Sharma", designation="BM", division="Onyx",
            total_doctors=10, total_calls=50, missed_doctors=0, poor_coverage_doctors=0,
            status="Healthy", reason="Meets all KPI targets",
        ))
        session.commit()
    finally:
        session.close()


def _counts():
    session = get_config_session()
    try:
        return {
            "thresholds": session.query(InventoryThreshold).count(),
            "replenishment": session.query(InventoryReplenishment).count(),
            "cwh": session.query(CwhStock).count(),
            "parameters": session.query(InventoryParameter).count(),
            "recipients": session.query(InventoryEmailRecipient).count(),
            "other_module": session.query(WorkDistributionFinding).count(),
        }
    finally:
        session.close()


def _marker() -> bool:
    session = get_config_session()
    try:
        row = session.query(AppSettings).filter_by(environment="user").first()
        return bool(row and row.inventory_data_reset_completed)
    finally:
        session.close()


# --- 1. Existing Inventory data is removed ---------------------------------

def test_reset_clears_all_three_inventory_tables():
    _seed_inventory_data()

    reset_mod.run_inventory_factory_reset_if_needed()

    counts = _counts()
    assert counts["thresholds"] == 0
    assert counts["replenishment"] == 0
    assert counts["cwh"] == 0


# --- 2. Other module data is untouched --------------------------------------

def test_reset_preserves_other_module_data():
    _seed_inventory_data()
    _seed_other_module_data()

    reset_mod.run_inventory_factory_reset_if_needed()

    assert _counts()["other_module"] == 1


# --- 3. Application settings (Inventory's own config) are untouched --------

def test_reset_preserves_inventory_parameters_and_recipients():
    _seed_inventory_data()
    _seed_inventory_settings()

    reset_mod.run_inventory_factory_reset_if_needed()

    counts = _counts()
    assert counts["parameters"] == 1
    assert counts["recipients"] == 1


# --- 4. The reset marker prevents the reset from running again -------------

def test_reset_runs_once_and_marker_prevents_a_second_run():
    _seed_inventory_data()

    reset_mod.run_inventory_factory_reset_if_needed()
    assert _marker() is True

    # Re-seed as if new data had somehow appeared, then run again -- a
    # completed reset must be a permanent no-op, never touching data again.
    _seed_inventory_data()
    reset_mod.run_inventory_factory_reset_if_needed()

    assert _counts()["thresholds"] == 1  # the re-seeded row survives untouched


# --- 5. Fresh Inventory uploads after the migration persist normally -------

def test_fresh_upload_after_completed_reset_is_never_touched():
    _seed_inventory_data()
    reset_mod.run_inventory_factory_reset_if_needed()
    assert _marker() is True

    # A brand-new upload happens after the reset has completed -- this is
    # exactly what app.threshold_service/replenishment_service/cwh_service
    # do on a real upload (insert fresh rows).
    _seed_inventory_data()
    assert _counts()["thresholds"] == 1

    # Any later launch's call is now a guaranteed no-op (marker already
    # set) -- the fresh upload must survive it untouched.
    reset_mod.run_inventory_factory_reset_if_needed()
    assert _counts()["thresholds"] == 1
    assert _counts()["replenishment"] == 1
    assert _counts()["cwh"] == 1
