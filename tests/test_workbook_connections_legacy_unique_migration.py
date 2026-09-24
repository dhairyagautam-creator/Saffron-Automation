"""Regression test for a real production bug: on any database created
before the module_key split, workbook_connections still carries its
original `workbook_name UNIQUE` column constraint (baked into the table's
own DDL by SQLite, not a droppable index -- see git history of
database/models.py's WorkbookConnection). Two different modules both
naming their own workbook connection "Onyx" is the documented, supported
case (see that class's docstring), but on such a database, saving the
second module's "Onyx" row raised sqlite3.IntegrityError: UNIQUE
constraint failed: workbook_connections.workbook_name -- reproduced live
against a real installation.

Follows tests/test_migrations_fresh_install.py's pattern: a real on-disk
sqlite file hand-built to match the pre-migration schema, run through the
actual init_db() + run_startup_migrations() sequence main.py runs before
ever opening a window.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import database.connection as db_connection


def _point_at_legacy_file(tmp_path, monkeypatch):
    """A sqlite FILE hand-built to match workbook_connections' schema from
    BEFORE module_key existed: workbook_name is UNIQUE on its own, with
    one pre-existing row (as any real installation would have from
    connecting a workbook before the module-scoping feature shipped)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE workbook_connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workbook_name VARCHAR NOT NULL UNIQUE,
                    file_path VARCHAR,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text("INSERT INTO workbook_connections (workbook_name, file_path) VALUES ('Onyx', 'C:/old/onyx.xlsx')")
        )
    monkeypatch.setattr(db_connection, "_engine", engine)
    monkeypatch.setattr(db_connection, "_Session", sessionmaker(bind=engine, autoflush=False, autocommit=False))
    return engine


def test_two_modules_can_both_connect_a_workbook_of_the_same_name(tmp_path, monkeypatch):
    """The actual bug: after startup migrations run on a legacy database, a
    second module (e.g. Work Distribution) must be able to save its own
    "Onyx" connection without the pre-existing, differently-scoped "Onyx"
    row (module_key='', from before scoping existed) blocking it."""
    _point_at_legacy_file(tmp_path, monkeypatch)

    from database.migrations import run_startup_migrations
    from app.workbook_connections import get_connection, set_connection

    db_connection.init_db()
    run_startup_migrations()  # must not raise

    set_connection("work_distribution", "Onyx", "C:/new/onyx.xlsx")  # must not raise IntegrityError

    assert get_connection("work_distribution", "Onyx") == "C:/new/onyx.xlsx"
    assert get_connection("", "Onyx") == "C:/old/onyx.xlsx"  # legacy row untouched


def test_legacy_migration_is_idempotent(tmp_path, monkeypatch):
    """Running startup migrations twice (a second launch) against an
    already-rebuilt table must be a safe no-op."""
    _point_at_legacy_file(tmp_path, monkeypatch)

    from database.migrations import run_startup_migrations

    db_connection.init_db()
    run_startup_migrations()
    run_startup_migrations()  # must not raise

    from app.workbook_connections import get_connection

    assert get_connection("", "Onyx") == "C:/old/onyx.xlsx"


def test_fresh_install_never_had_legacy_constraint(tmp_path, monkeypatch):
    """A brand-new database (current model, no legacy unique=True) must
    pass through the rebuild migration as a no-op and still enforce the
    real composite uniqueness (same module_key + workbook_name rejected)."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(db_connection, "_engine", engine)
    monkeypatch.setattr(db_connection, "_Session", sessionmaker(bind=engine, autoflush=False, autocommit=False))

    from database.migrations import run_startup_migrations
    from app.workbook_connections import set_connection

    db_connection.init_db()
    run_startup_migrations()

    set_connection("employee_module", "Onyx", "C:/a.xlsx")
    set_connection("work_distribution", "Onyx", "C:/b.xlsx")  # different module, same name: must succeed

    from app.workbook_connections import get_connection

    assert get_connection("employee_module", "Onyx") == "C:/a.xlsx"
    assert get_connection("work_distribution", "Onyx") == "C:/b.xlsx"
