"""Tests for the Employee Hierarchy 3-way split -- Path Validator, Work
Distribution, and Review System each maintain their own completely
independent hierarchy table and their own workbook_connections rows (see
app/hierarchy_parser.py's HIERARCHY_TABLES, app/workbook_connections.py's
module_key scoping, database/models.py's WorkbookConnection composite
unique constraint).

Every DB operation runs against a real (in-memory) SQLite database,
exercising the actual model classes/service functions, not a mock -- same
convention as the rest of this test suite. The core thing this file
proves: uploading/connecting a workbook for one module has ZERO effect on
either of the other two -- no shared table, no shared connection row.

ISOLATION NOTE (learned the hard way while writing this file, now fixed
for good): patching only `database.connection._Session` -- the pattern
every OTHER test in this suite used before today -- is NOT enough here.
app/hierarchy_parser.py's refresh_hierarchy()/find_by_*()/get_all_*() all
go through `get_data_engine()` for raw SQL (pandas.to_sql,
sqlalchemy.text()), which is a SEPARATE singleton (`database.connection.
_engine`) from the one `get_session()`/`_Session` uses -- patching only
`_Session` leaves `get_data_engine()` pointed at the REAL production
database. An earlier version of this file without the `_engine` patch
actually wrote fake employee rows into this machine's real database
(caught immediately via a direct raw-sqlite3 check against the real file,
then cleaned up). See tests/db_isolation.py -- the shared helper this file
now uses -- for the permanent fix.
"""

import pytest

from app.hierarchy_parser import (
    HIERARCHY_TABLES,
    find_by_employee_code,
    find_by_employee_name,
    get_all_designations,
    get_all_doj,
    refresh_hierarchy,
    table_for_module,
)
from app.workbook_connections import get_connection, get_connections, set_connection
from database.connection import get_config_session
from database.models import WorkbookConnection
from tests.db_isolation import isolate_database

PATH_VALIDATOR = "employee_module"
WORK_DISTRIBUTION = "work_distribution"
REVIEW_SYSTEM = "review_system"


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    isolate_database(monkeypatch)


def _write_org_data_workbook(path, rows):
    """A minimal real .xlsx Organization Data workbook -- same shape
    app.hierarchy_parser.parse_workbook expects."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Emp Code", "Name", "Designation", "Mobile", "Email-Id", "DOJ"])
    for row in rows:
        ws.append(row)
    wb.save(path)


def test_table_for_module_maps_all_three_distinctly():
    assert table_for_module(PATH_VALIDATOR) == "employee_hierarchy_path_validator"
    assert table_for_module(WORK_DISTRIBUTION) == "employee_hierarchy_work_distribution"
    assert table_for_module(REVIEW_SYSTEM) == "employee_hierarchy_review_system"
    assert len(set(HIERARCHY_TABLES.values())) == 3  # genuinely three distinct table names


def test_workbook_connections_are_isolated_per_module(tmp_path):
    """The same workbook NAME ("Onyx"), connected to a DIFFERENT file for
    each of the three modules, must never collide or overwrite -- proves
    the composite (module_key, workbook_name) scoping actually works, not
    just that the column exists."""
    pv_file = str(tmp_path / "pv_onyx.xlsx")
    wd_file = str(tmp_path / "wd_onyx.xlsx")
    rs_file = str(tmp_path / "rs_onyx.xlsx")

    set_connection(PATH_VALIDATOR, "Onyx", pv_file)
    set_connection(WORK_DISTRIBUTION, "Onyx", wd_file)
    set_connection(REVIEW_SYSTEM, "Onyx", rs_file)

    assert get_connection(PATH_VALIDATOR, "Onyx") == pv_file
    assert get_connection(WORK_DISTRIBUTION, "Onyx") == wd_file
    assert get_connection(REVIEW_SYSTEM, "Onyx") == rs_file

    # Three genuinely separate rows, not one row three modules raced over.
    session = get_config_session()
    try:
        assert session.query(WorkbookConnection).filter_by(workbook_name="Onyx").count() == 3
    finally:
        session.close()

    # Re-connecting Path Validator's own "Onyx" must not touch WD's or Review's.
    new_pv_file = str(tmp_path / "pv_onyx_v2.xlsx")
    set_connection(PATH_VALIDATOR, "Onyx", new_pv_file)
    assert get_connection(PATH_VALIDATOR, "Onyx") == new_pv_file
    assert get_connection(WORK_DISTRIBUTION, "Onyx") == wd_file
    assert get_connection(REVIEW_SYSTEM, "Onyx") == rs_file


def test_refresh_hierarchy_writes_only_to_its_own_module_table(tmp_path):
    """The actual end-to-end proof: three different workbooks, one per
    module, refreshed independently -- each module's table gets exactly
    its own data, and reading through any OTHER module's key finds
    nothing, even though all three ran against the same process/DB."""
    pv_file = str(tmp_path / "pv.xlsx")
    wd_file = str(tmp_path / "wd.xlsx")
    rs_file = str(tmp_path / "rs.xlsx")

    _write_org_data_workbook(pv_file, [["PV1", "Path Validator Employee", "BM", None, "pv1@x.com", ""]])
    _write_org_data_workbook(wd_file, [["WD1", "Work Distribution Employee", "BM", None, "wd1@x.com", ""]])
    _write_org_data_workbook(rs_file, [["RS1", "Review System Employee", "BM", None, "rs1@x.com", ""]])

    set_connection(PATH_VALIDATOR, "Onyx", pv_file)
    set_connection(WORK_DISTRIBUTION, "Onyx", wd_file)
    set_connection(REVIEW_SYSTEM, "Onyx", rs_file)

    stats_pv = refresh_hierarchy(PATH_VALIDATOR)
    stats_wd = refresh_hierarchy(WORK_DISTRIBUTION)
    stats_rs = refresh_hierarchy(REVIEW_SYSTEM)

    assert stats_pv["employees_loaded"] == 1
    assert stats_wd["employees_loaded"] == 1
    assert stats_rs["employees_loaded"] == 1

    # Each module finds ONLY its own employee, by code and by name.
    assert find_by_employee_code(PATH_VALIDATOR, "PV1") is not None
    assert find_by_employee_code(PATH_VALIDATOR, "WD1") is None
    assert find_by_employee_code(PATH_VALIDATOR, "RS1") is None

    assert find_by_employee_code(WORK_DISTRIBUTION, "WD1") is not None
    assert find_by_employee_code(WORK_DISTRIBUTION, "PV1") is None
    assert find_by_employee_code(WORK_DISTRIBUTION, "RS1") is None

    assert find_by_employee_code(REVIEW_SYSTEM, "RS1") is not None
    assert find_by_employee_code(REVIEW_SYSTEM, "PV1") is None
    assert find_by_employee_code(REVIEW_SYSTEM, "WD1") is None

    assert find_by_employee_name(PATH_VALIDATOR, "Work Distribution Employee") == []
    assert get_all_designations(WORK_DISTRIBUTION) == {"WD1": "BM"}
    assert get_all_designations(PATH_VALIDATOR) == {"PV1": "BM"}


def test_refreshing_one_module_never_touches_another_modules_table(tmp_path):
    """Refresh Work Distribution's hierarchy AFTER Path Validator's is
    already populated -- Path Validator's table/data must be completely
    untouched (full-replace is scoped to Work Distribution's own table
    only, never a shared one)."""
    pv_file = str(tmp_path / "pv.xlsx")
    _write_org_data_workbook(pv_file, [["PV1", "Path Validator Employee", "BM", None, "pv1@x.com", ""]])
    set_connection(PATH_VALIDATOR, "Onyx", pv_file)
    refresh_hierarchy(PATH_VALIDATOR)

    assert find_by_employee_code(PATH_VALIDATOR, "PV1") is not None

    wd_file = str(tmp_path / "wd.xlsx")
    _write_org_data_workbook(wd_file, [["WD1", "Work Distribution Employee", "ABM", None, "wd1@x.com", ""]])
    set_connection(WORK_DISTRIBUTION, "Onyx", wd_file)
    refresh_hierarchy(WORK_DISTRIBUTION)

    # Path Validator's own employee is still there, completely unaffected.
    assert find_by_employee_code(PATH_VALIDATOR, "PV1") is not None
    assert find_by_employee_code(PATH_VALIDATOR, "WD1") is None


def test_get_all_doj_is_scoped_per_module(tmp_path):
    pv_file = str(tmp_path / "pv.xlsx")
    wd_file = str(tmp_path / "wd.xlsx")
    _write_org_data_workbook(pv_file, [["PV1", "PV Employee", "BM", None, "pv1@x.com", "2026-01-15"]])
    _write_org_data_workbook(wd_file, [["WD1", "WD Employee", "BM", None, "wd1@x.com", "2026-02-20"]])
    set_connection(PATH_VALIDATOR, "Onyx", pv_file)
    set_connection(WORK_DISTRIBUTION, "Onyx", wd_file)
    refresh_hierarchy(PATH_VALIDATOR)
    refresh_hierarchy(WORK_DISTRIBUTION)

    assert get_all_doj(PATH_VALIDATOR) == {"PV1": "2026-01-15"}
    assert get_all_doj(WORK_DISTRIBUTION) == {"WD1": "2026-02-20"}
