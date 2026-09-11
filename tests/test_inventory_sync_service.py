"""Tests for app/inventory_sync_service.py's apply_pending_updates() --
specifically the two behaviors this feature actually adds on top of the
proven app/review_sync_service.py mechanism (see that module and
docs/SYNC_DESIGN.md): the no-sales-retained GATE on inventory_report, and
the recompute-then-evaluate pipeline running correctly through a pulled
(not locally uploaded) file, in slot order regardless of input order.

app.review_sync_service.py's own test suite explicitly does not cover its
network-bound apply_pending_updates/check_for_updates (see that file's
test_review_sync_service.py docstring: covered only by manual two-machine
verification against a real Supabase project). This file goes one step
further for the two behaviors that are genuinely new here, using a small
fake Supabase client (real postgrest/Storage chaining shape, no network)
so the gate and the recompute pipeline are verified by automated,
repeatable execution rather than manual testing alone. Real
cross-machine/live-Supabase verification is still a separate, manual step
-- see the PR/handoff notes.
"""

import hashlib

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.inventory_sync_service as isync
import app.inventory_upload_service as ius
from database.connection import Base, get_config_session
from database.models import InventoryReplenishment, InventoryUploadSlot, SyncState


def _in_memory_session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    monkeypatch.setattr("database.connection._Session", _in_memory_session_factory())
    monkeypatch.setattr(ius, "INVENTORY_UPLOADS_DIR", tmp_path)


def _sales_csv_bytes() -> bytes:
    return pd.DataFrame(
        {
            "Division": ["Tablets"],
            "CFA": ["MUMBAI (HEAD OFFICE) ( MUM )"],
            "Item Name": ["Widget 30-Pack"],
            "Packing": ["30"],
            "Sales": [100],
        }
    ).to_csv(index=False).encode("utf-8")


def _inventory_csv_bytes(total_qty: int = 50) -> bytes:
    return pd.DataFrame(
        {
            "BranchLocation": ["MUMBAI (HEAD OFFICE) ( MUM )"],
            "Item Group": ["Tablets"],
            "Item Code": ["ITM001"],
            "Item Name": ["Widget 30-Pack"],
            "TotalQty": [total_qty],
            "Transit Stock": [0],
        }
    ).to_csv(index=False).encode("utf-8")


# --- Minimal fake Supabase client -- just enough of the real chained
# postgrest/Storage shape for apply_pending_updates() to run unmodified. ---

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


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *a, **k):
        return _FakeManifestQuery(self._rows)


class _FakeBucket:
    def __init__(self, files):
        self._files = files

    def download(self, path):
        return self._files[path]


class _FakeStorage:
    def __init__(self, files):
        self._files = files

    def from_(self, _bucket):
        return _FakeBucket(self._files)


class _FakeClient:
    def __init__(self, manifest_rows, files):
        self._manifest_rows = manifest_rows
        self.storage = _FakeStorage(files)

    def table(self, name):
        assert name == "sync_manifest"
        return _FakeTable(self._manifest_rows)


def _manifest_row(seq, slot_key, filename, data: bytes) -> dict:
    return {
        "seq": seq,
        "module": "inventory",
        "slot_key": slot_key,
        "storage_path": f"inventory/{slot_key}/{hashlib.sha256(data).hexdigest()}{'.csv'}",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "filename": filename,
        "app_version": "0.0.1",  # deliberately old -- never version_blocked regardless of real APP_VERSION
        "uploaded_by": "11111111-1111-1111-1111-111111111111",
        "uploaded_at": "2026-09-11T00:00:00+00:00",
    }


def test_gate_blocks_inventory_report_pull_when_no_sales_retained(monkeypatch):
    """Verification checklist item 1: a machine with no Sales Report ever
    pulled/uploaded attempts an Inventory Report pull -> correctly
    blocked, clear message, not a silent success."""
    inv_bytes = _inventory_csv_bytes()
    manifest_rows = [_manifest_row(1, ius.INVENTORY_REPORT_SLOT, "Inventory.csv", inv_bytes)]
    files = {manifest_rows[0]["storage_path"]: inv_bytes}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(isync, "get_supabase_client", lambda: fake_client)

    assert ius.has_sales_report_retained() is False

    result = isync.apply_pending_updates([ius.INVENTORY_REPORT_SLOT])

    assert result["applied"] == []
    assert len(result["failed"]) == 1
    assert result["failed"][0]["slot_id"] == ius.INVENTORY_REPORT_SLOT
    assert "no sales report" in result["failed"][0]["reason"].lower()

    # Blocked, not silently skipped -- nothing was written locally at all.
    session = get_config_session()
    try:
        assert session.query(InventoryUploadSlot).filter_by(slot_id=ius.INVENTORY_REPORT_SLOT).first() is None
        assert session.query(SyncState).filter_by(module="inventory", slot_key=ius.INVENTORY_REPORT_SLOT).first() is None
    finally:
        session.close()


def test_sales_pulled_first_opens_gate_and_inventory_report_pull_recomputes_real_numbers(monkeypatch):
    """Verification checklist item 2: Sales pulled first -> gate opens ->
    Inventory Report pull triggers full recompute correctly -> real
    numbers, not zeros. Slots are deliberately passed in the WRONG order
    (inventory_report before sales_report) to prove apply_pending_updates
    normalizes to the safe order itself rather than trusting the caller."""
    sales_bytes = _sales_csv_bytes()
    inv_bytes = _inventory_csv_bytes(total_qty=50)
    manifest_rows = [
        _manifest_row(1, ius.SALES_REPORT_SLOT, "Sales.csv", sales_bytes),
        _manifest_row(1, ius.INVENTORY_REPORT_SLOT, "Inventory.csv", inv_bytes),
    ]
    files = {r["storage_path"]: (sales_bytes if r["slot_key"] == ius.SALES_REPORT_SLOT else inv_bytes) for r in manifest_rows}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(isync, "get_supabase_client", lambda: fake_client)

    result = isync.apply_pending_updates([ius.INVENTORY_REPORT_SLOT, ius.SALES_REPORT_SLOT])

    assert set(result["applied"]) == {ius.SALES_REPORT_SLOT, ius.INVENTORY_REPORT_SLOT}
    assert result["failed"] == []

    session = get_config_session()
    try:
        rows = session.query(InventoryReplenishment).all()
        assert len(rows) == 1
        assert rows[0].status == "Replenishment Required"
        assert rows[0].stock_deficit == 100.0  # real number: 150 packed_threshold - 50 stock, not 0
    finally:
        session.close()

    sales_state = ius.get_slot_state(ius.SALES_REPORT_SLOT)
    assert sales_state["uploaded"] is True
    assert sales_state["uploaded_by"] == "11111111-1111-1111-1111-111111111111"
    assert sales_state["thresholds_generated_at"] is not None

    inventory_state = ius.get_slot_state(ius.INVENTORY_REPORT_SLOT)
    assert inventory_state["uploaded"] is True

    session = get_config_session()
    try:
        for slot_key in (ius.SALES_REPORT_SLOT, ius.INVENTORY_REPORT_SLOT):
            state_row = session.query(SyncState).filter_by(module="inventory", slot_key=slot_key).first()
            assert state_row is not None
            assert state_row.applied_seq == 1
    finally:
        session.close()


def test_gate_does_not_block_sales_report_pull_itself(monkeypatch):
    """The gate is inventory_report-specific -- a Sales Report pull on a
    completely fresh machine must never be blocked by its own gate."""
    sales_bytes = _sales_csv_bytes()
    manifest_rows = [_manifest_row(1, ius.SALES_REPORT_SLOT, "Sales.csv", sales_bytes)]
    files = {manifest_rows[0]["storage_path"]: sales_bytes}
    fake_client = _FakeClient(manifest_rows, files)
    monkeypatch.setattr(isync, "get_supabase_client", lambda: fake_client)

    result = isync.apply_pending_updates([ius.SALES_REPORT_SLOT])

    assert result["applied"] == [ius.SALES_REPORT_SLOT]
    assert result["failed"] == []
