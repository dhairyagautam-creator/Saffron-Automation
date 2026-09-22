"""Startup migrations against a genuinely fresh, never-launched database
(a real empty sqlite file with NO tables yet -- a brand-new
SAFFRON_DATA_DIR's first launch), AND against an EXISTING database that
predates the parameter-sync project's Phase 1 app_settings column drop,
upgrading through it for the first time -- a different code path from
the fresh-install one (see
test_existing_database_upgrading_through_phase1_column_drop_runs_cleanly's
own docstring for the real bug this second scenario catches). Both go
through the real init_db() + run_startup_migrations() sequence main.py
runs before ever opening a window.

This is deliberately NOT tests/db_isolation.py's isolate_database() --
that helper calls Base.metadata.create_all() itself before a test ever
runs, which means every migration written to backfill/rebuild schema
that predates the CURRENT model (e.g. the app_settings seed INSERT in
backfill_bookkeeping_timestamps_to_utc(), or the now-deleted
ensure_app_settings_master_email_column() add/drop loop this class of
bug is named after) never actually executes under that helper. Hence the
existing 500+ passing tests never caught a first-run migration bug: none
of them build a database the way a real fresh install does."""

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import database.connection as db_connection


def _point_at_fresh_file(tmp_path, monkeypatch):
    """Redirect both database.connection singletons (see that module's own
    note on why both must move together) to a brand-new sqlite FILE with
    no schema at all -- not :memory:, so it behaves exactly like a real
    on-disk first launch."""
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(db_connection, "_engine", engine)
    monkeypatch.setattr(db_connection, "_Session", sessionmaker(bind=engine, autoflush=False, autocommit=False))
    return engine


def _point_at_pre_phase1_file(tmp_path, monkeypatch):
    """Redirect both database.connection singletons to a sqlite FILE whose
    app_settings table is hand-built to match the schema from BEFORE the
    parameter-sync project's Phase 1 cleanup (see git history of
    database/models.py's AppSettings class): automatic_email_enabled
    NOT NULL with no DB-level default, plus the already-superseded
    master_email_address -- and deliberately ZERO rows, matching the real
    database (manual_test_data/machine_b) this bug was reproduced
    against. Every other table is left to init_db()'s own
    Base.metadata.create_all(), same as a real upgrade."""
    engine = create_engine(f"sqlite:///{tmp_path / 'pre_phase1.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE app_settings (
                    id INTEGER NOT NULL PRIMARY KEY,
                    sender_gmail_address VARCHAR,
                    gmail_app_password VARCHAR,
                    automatic_email_enabled INTEGER NOT NULL,
                    master_email_address VARCHAR,
                    geoapify_api_key VARCHAR,
                    setup_completed INTEGER NOT NULL,
                    inventory_data_reset_completed INTEGER NOT NULL,
                    timestamps_backfilled_to_utc INTEGER NOT NULL,
                    updated_at DATETIME
                )
                """
            )
        )
    monkeypatch.setattr(db_connection, "_engine", engine)
    monkeypatch.setattr(db_connection, "_Session", sessionmaker(bind=engine, autoflush=False, autocommit=False))
    return engine


def test_fresh_install_startup_migrations_run_cleanly(tmp_path, monkeypatch):
    """init_db() + run_startup_migrations() -- the exact sequence main.py
    runs before ever opening a window -- must complete without raising
    against a database that has never been touched before."""
    engine = _point_at_fresh_file(tmp_path, monkeypatch)

    from database.migrations import run_startup_migrations

    db_connection.init_db()
    run_startup_migrations()  # must not raise

    with engine.connect() as conn:
        row_count = conn.execute(text("SELECT COUNT(*) FROM app_settings")).scalar()
    assert row_count == 1

    columns = {c["name"] for c in inspect(engine).get_columns("app_settings")}
    assert "automatic_email_enabled" not in columns
    assert "master_email_address" not in columns


def test_fresh_install_startup_migrations_are_idempotent(tmp_path, monkeypatch):
    """Running startup migrations twice against the same now-migrated
    database (e.g. the user launches the app a second time) must be a
    safe no-op -- same guarantee the master_email_address fix already
    relied on, now checked for the app_settings seed path too."""
    engine = _point_at_fresh_file(tmp_path, monkeypatch)

    from database.migrations import run_startup_migrations

    db_connection.init_db()
    run_startup_migrations()
    run_startup_migrations()  # must not raise, must not duplicate the row

    with engine.connect() as conn:
        row_count = conn.execute(text("SELECT COUNT(*) FROM app_settings")).scalar()
    assert row_count == 1


def test_existing_database_upgrading_through_phase1_column_drop_runs_cleanly(tmp_path, monkeypatch):
    """The actual bug (reproduced live against manual_test_data/machine_b):
    an EXISTING database that predates the Phase 1 cleanup still has
    automatic_email_enabled/master_email_address as real NOT NULL/nullable
    columns, with zero rows in app_settings (the seed row was never
    written under whatever earlier code last touched this file). Startup
    must drop those columns (drop_dead_automatic_email_flags()) BEFORE
    backfill_bookkeeping_timestamps_to_utc()'s own seed INSERT runs --
    that INSERT's column list matches the CURRENT (post-drop) schema, so
    if the drop hasn't happened yet the INSERT violates
    automatic_email_enabled's NOT NULL constraint. This is the ordering
    bug run_startup_migrations() must get right."""
    engine = _point_at_pre_phase1_file(tmp_path, monkeypatch)

    from database.migrations import run_startup_migrations

    db_connection.init_db()
    run_startup_migrations()  # must not raise IntegrityError

    columns = {c["name"] for c in inspect(engine).get_columns("app_settings")}
    assert "automatic_email_enabled" not in columns
    assert "master_email_address" not in columns

    with engine.connect() as conn:
        row_count = conn.execute(text("SELECT COUNT(*) FROM app_settings")).scalar()
    assert row_count == 1

    run_startup_migrations()  # second launch: safe no-op
    with engine.connect() as conn:
        row_count = conn.execute(text("SELECT COUNT(*) FROM app_settings")).scalar()
    assert row_count == 1


def test_investigation_findings_first_flagged_at_backfills_from_created_at_and_is_idempotent(tmp_path, monkeypatch):
    """ensure_investigation_findings_first_flagged_at_column() (the
    re-notification bug fix, see app/notification_service.py's
    STALE_FINDING_AGE_DAYS) against an EXISTING investigation_findings
    table that predates the column: existing rows must be backfilled from
    their own created_at (the best available approximation for a case
    already in flight -- a correct value only starts accumulating from
    that row's next rule re-run onward, via the carry-forward in
    rules/same_location.py / rules/hours_worked.py). Running it twice
    must be a safe no-op that never overwrites an already-set value."""
    engine = create_engine(f"sqlite:///{tmp_path / 'pre_first_flagged_at.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE investigation_findings (
                    finding_id INTEGER NOT NULL PRIMARY KEY,
                    import_id INTEGER,
                    employee_name VARCHAR NOT NULL,
                    employee_code VARCHAR NOT NULL,
                    visit_date DATE NOT NULL,
                    rule_name VARCHAR NOT NULL,
                    message VARCHAR NOT NULL,
                    notification_status VARCHAR,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO investigation_findings "
                "(finding_id, import_id, employee_name, employee_code, visit_date, rule_name, message, created_at) "
                "VALUES (1, 1, 'Emp One', 'E1', '2026-07-15', 'SAME_LOCATION', 'msg', '2026-07-15 09:00:00')"
            )
        )
    monkeypatch.setattr(db_connection, "_engine", engine)
    monkeypatch.setattr(db_connection, "_Session", sessionmaker(bind=engine, autoflush=False, autocommit=False))

    from database.migrations import ensure_investigation_findings_first_flagged_at_column

    ensure_investigation_findings_first_flagged_at_column()

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT created_at, first_flagged_at FROM investigation_findings WHERE finding_id = 1")
        ).one()
    assert row.first_flagged_at == row.created_at

    # Idempotent: a second run must not raise (column already exists) and
    # must not clobber the now-set value.
    ensure_investigation_findings_first_flagged_at_column()
    with engine.connect() as conn:
        row_again = conn.execute(
            text("SELECT first_flagged_at FROM investigation_findings WHERE finding_id = 1")
        ).one()
    assert row_again.first_flagged_at == row.first_flagged_at
