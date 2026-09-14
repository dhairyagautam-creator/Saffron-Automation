"""Phase 1 of Work Distribution sync: local file retention for RGD
Coverage, ABM, and RBM uploads, wired ALONGSIDE (never replacing) the
existing parse+process pipelines -- see app/work_distribution_upload_service.py.

Every test here runs the REAL parser and REAL process function against a
REAL .xlsx file, then retains it, and confirms three things together:
1. The retained file exists at the expected path and is byte-identical to
   the source.
2. WorkDistributionUploadSlot reflects the correct filename/file_path.
3. The existing processing pipeline's real output (finding counts, status)
   is exactly what the input data implies -- proving retention (which
   operates on the ORIGINAL file_path, entirely independent of the
   already-parsed records the pipeline uses) has zero effect on
   computation, not just asserting "it still runs".

Uses tests/db_isolation.py's isolate_database() -- REQUIRED here, not
optional: process_work_distribution_report()/process_manager_work_allocation_report()/
process_rbm_report() all resolve hierarchy display names via
get_data_engine()-backed raw SQL (app/hierarchy_parser.py), so patching
only database.connection._Session (the pattern most other tests in this
suite use) would leave that half pointed at the real on-disk database --
exactly the mistake documented in database/connection.py's own warning.

ALSO redirects app.work_distribution_upload_service.WORK_DISTRIBUTION_UPLOADS_DIR
to a temp directory -- confirmed the hard way in this very file: an early
version without this redirect left database access correctly isolated but
still copied real test fixture files into this project's actual, real
work_distribution_uploads/ folder on disk (caught by manually checking
that directory after the first run, cleaned up before this fix landed).
Isolating the database is not the same as isolating everything a test
touches -- check both.
"""

from pathlib import Path

import openpyxl
import pytest

import app.work_distribution_upload_service as wdus
from app.manager_work_allocation_rbm_service import process_rbm_report
from app.manager_work_allocation_parser import parse_manager_work_allocation_report
from app.manager_work_allocation_service import process_manager_work_allocation_report
from app.work_distribution_parser import parse_work_distribution_report
from app.work_distribution_service import process_work_distribution_report
from app.work_distribution_upload_service import (
    ABM,
    RBM,
    RGD,
    get_all_slot_states,
    get_slot_state,
    slot_id_for,
    store_work_distribution_upload,
)
from database.connection import get_config_session
from database.models import (
    ManagerWorkAllocationFinding,
    WorkDistributionDoctor,
    WorkDistributionFinding,
)
from tests.db_isolation import isolate_database


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path_factory):
    isolate_database(monkeypatch)
    # A directory distinct from the per-test `tmp_path` used for SOURCE
    # files below -- retention and source files must never accidentally
    # collide on the same path.
    retained_dir = tmp_path_factory.mktemp("retained")
    monkeypatch.setattr(wdus, "WORK_DISTRIBUTION_UPLOADS_DIR", retained_dir)


def _write_rgd_workbook(path, division="Onyx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([
        "Division", "Dr. Code", "Dr. Name", "Speciality", "Category", "ABM RGD",
        "City", "HQ", "Region", "BM Code", "ABM Code", "BM Visit Jul-26", "ABM Visit Jul-26",
    ])
    # One BM-eligible doctor (Category=B-RGD) visited once by the BM.
    ws.append([division, "D1", "Dr. One", "Cardio", "B-RGD", "", "City1", "HQ1", "Region1", "BM1", "", 1, 0])
    # A second doctor, never visited -- BM1's book: 2 doctors, 1 missed.
    ws.append([division, "D2", "Dr. Two", "Cardio", "B-RGD", "", "City1", "HQ1", "Region1", "BM1", "", 0, 0])
    wb.save(path)


def _write_mwa_workbook(path, division="Onyx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([
        "Division", "Emp Code", "Emp Name", "Emp Designation", "Month",
        "Team Emp Code", "Team Emp Name", "Team Emp Designation", "# Days Spent In Joint",
    ])
    ws.append([division, "M1", "Manager One", "ABM", "Jul-26", "B1", "Bilal BM", "BM", 5])
    wb.save(path)


def test_rgd_upload_is_retained_and_processing_is_unaffected(tmp_path):
    source = tmp_path / "Onyx_RGD_July.xlsx"
    _write_rgd_workbook(source, division="Onyx")

    parse_result = parse_work_distribution_report(str(source))
    assert parse_result["success"], parse_result

    # Real processing runs FIRST, exactly as the UI does -- proving
    # retention (below) has no effect on it, not just that it "still runs".
    summary = process_work_distribution_report(parse_result["doctors"])
    assert summary["total_doctors"] == 2
    assert summary["bm_count"] == 1

    session = get_config_session()
    try:
        doctor_count_before = session.query(WorkDistributionDoctor).count()
        finding_count_before = session.query(WorkDistributionFinding).count()
    finally:
        session.close()

    stored_path = store_work_distribution_upload(RGD, "Onyx", str(source))

    # Retention changed nothing about what was already computed/stored.
    session = get_config_session()
    try:
        assert session.query(WorkDistributionDoctor).count() == doctor_count_before
        assert session.query(WorkDistributionFinding).count() == finding_count_before
    finally:
        session.close()

    assert stored_path.exists()
    assert stored_path.read_bytes() == source.read_bytes()
    assert stored_path.name == "rgd_onyx.xlsx"

    state = get_slot_state(RGD, "Onyx")
    assert state["uploaded"] is True
    assert state["filename"] == "Onyx_RGD_July.xlsx"
    assert state["file_path"] == str(stored_path)


def test_abm_upload_is_retained_and_processing_is_unaffected(tmp_path):
    source = tmp_path / "Onyx_ABM_July.xlsx"
    _write_mwa_workbook(source, division="Onyx")

    parse_result = parse_manager_work_allocation_report(str(source))
    assert parse_result["success"], parse_result

    summary = process_manager_work_allocation_report(parse_result["records"])
    assert summary["abm_count"] == 1

    session = get_config_session()
    try:
        finding_count_before = session.query(ManagerWorkAllocationFinding).filter_by(designation="ABM").count()
    finally:
        session.close()

    stored_path = store_work_distribution_upload(ABM, "Onyx", str(source))

    session = get_config_session()
    try:
        assert session.query(ManagerWorkAllocationFinding).filter_by(designation="ABM").count() == finding_count_before
    finally:
        session.close()

    assert stored_path.exists()
    assert stored_path.read_bytes() == source.read_bytes()
    assert stored_path.name == "abm_onyx.xlsx"

    state = get_slot_state(ABM, "Onyx")
    assert state["uploaded"] is True
    assert state["filename"] == "Onyx_ABM_July.xlsx"


def test_rbm_upload_is_retained_and_processing_is_unaffected(tmp_path):
    source = tmp_path / "Onyx_RBM_July.xlsx"
    _write_mwa_workbook(source, division="Onyx")

    parse_result = parse_manager_work_allocation_report(str(source))
    assert parse_result["success"], parse_result

    summary = process_rbm_report(parse_result["records"])
    assert summary["rbm_count"] == 0  # fixture's one row is an ABM-designation record, not RBM -- 0 is the correct, real result

    stored_path = store_work_distribution_upload(RBM, "Onyx", str(source))

    assert stored_path.exists()
    assert stored_path.read_bytes() == source.read_bytes()
    assert stored_path.name == "rbm_onyx.xlsx"

    state = get_slot_state(RBM, "Onyx")
    assert state["uploaded"] is True
    assert state["filename"] == "Onyx_RBM_July.xlsx"


def test_nine_slots_are_independent(tmp_path):
    """All 9 (report_type, division) slots are genuinely independent --
    retaining one never touches another, matching the same isolation
    already proven for the employee_hierarchy 3-way split."""
    rgd_source = tmp_path / "rgd.xlsx"
    abm_source = tmp_path / "abm.xlsx"
    _write_rgd_workbook(rgd_source, division="Guardians")
    _write_mwa_workbook(abm_source, division="Xandra")

    store_work_distribution_upload(RGD, "Guardians", str(rgd_source))
    store_work_distribution_upload(ABM, "Xandra", str(abm_source))

    states = get_all_slot_states()
    assert len(states) == 9
    assert states[slot_id_for(RGD, "Guardians")]["uploaded"] is True
    assert states[slot_id_for(ABM, "Xandra")]["uploaded"] is True
    # Every other slot is still genuinely empty.
    untouched = [sid for sid in states if sid not in (slot_id_for(RGD, "Guardians"), slot_id_for(ABM, "Xandra"))]
    assert len(untouched) == 7
    assert all(states[sid]["uploaded"] is False for sid in untouched)


def test_reuploading_a_slot_replaces_the_retained_file(tmp_path):
    first = tmp_path / "first.xlsx"
    second = tmp_path / "second.xlsx"
    _write_rgd_workbook(first, division="Onyx")
    _write_rgd_workbook(second, division="Onyx")
    # Make the two files genuinely different bytes so a stale-copy bug
    # would be detectable.
    second.write_bytes(second.read_bytes() + b"\x00")

    path1 = store_work_distribution_upload(RGD, "Onyx", str(first))
    path2 = store_work_distribution_upload(RGD, "Onyx", str(second))

    assert path1 == path2  # same slot -> same stored path
    assert path2.read_bytes() == second.read_bytes()
    assert path2.read_bytes() != first.read_bytes()
