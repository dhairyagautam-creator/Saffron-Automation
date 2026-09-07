"""SQLite database connection setup using SQLAlchemy.

One engine, one database file. Configuration (app_settings, rule_parameters)
and data (uploads, findings, caches, emails, session state) live together in
the single file at app.config.DATABASE_PATH.

`get_config_session()`/`get_config_engine()` and `get_data_session()`/
`get_data_engine()` are synonyms of `get_session()`/`get_engine()`, kept only
so the ~370 existing call sites keep working; they no longer mean anything
different from each other.
"""

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_PATH

Base = declarative_base()


def utcnow() -> datetime:
    """Naive UTC now -- the exact shape every bookkeeping timestamp column
    already stores (no tzinfo), just backed by UTC instead of local time.
    Use this (never datetime.now()) for created_at/updated_at/last_updated/
    uploaded_at/sent_at/activated_at/imported_at and similar -- any
    timestamp that could ever be compared across machines with different
    clocks/timezones once sync exists (see docs/SYNC_DESIGN.md). This
    codebase already hit exactly this bug once (Milestone 53: a naive local
    timestamp compared against Supabase's naive-UTC one, permanently
    "losing" every comparison). Deliberately not datetime.utcnow() (or a
    timezone-aware datetime.now(timezone.utc)) -- the former is deprecated
    since Python 3.12, the latter would carry tzinfo that older naive rows
    written before this fix don't have, breaking any direct Python
    comparison between them."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local(dt: datetime | None) -> datetime | None:
    """The inverse of utcnow(): a stored (naive UTC) bookkeeping timestamp,
    converted to whatever timezone THIS machine is actually running in --
    via the OS, not a hardcoded offset, so display stays correct even if
    this app is ever run somewhere other than where its data was written.
    None passes through as None. Use this at every point a bookkeeping
    timestamp is shown to a user (a label, a treeview cell, an exported
    column) -- never render a stored value's raw .strftime() directly, or
    it displays as UTC wall-clock time instead of the viewer's own."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


DB_PATH = DATABASE_PATH

_engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)


@event.listens_for(_engine, "connect")
def _enable_wal_mode(dbapi_connection, connection_record) -> None:
    """WAL instead of SQLite's default rollback-journal mode -- readers
    never block on a writer (and vice versa), which matters once more than
    one process/thread can touch this file at a time. Set per-connection
    (SQLite pragmas aren't persistent across connections in every driver
    configuration), so every new connection gets it, not just the first."""
    dbapi_connection.execute("PRAGMA journal_mode=WAL")


_Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


def get_engine():
    return _engine


def get_session():
    return _Session()


# --- Synonyms retained for existing call sites -------------------------
get_config_engine = get_engine
get_data_engine = get_engine
get_config_session = get_session
get_data_session = get_session


def init_db() -> None:
    """Create any missing tables on the single database."""
    from database import models  # noqa: F401  (registers models on Base.metadata)

    Base.metadata.create_all(bind=_engine)
    logger.info(f"Database initialized at {DB_PATH}")
