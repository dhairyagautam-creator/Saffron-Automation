"""SQLite database connection setup using SQLAlchemy.

One engine, one database file. Configuration (app_settings, rule_parameters)
and data (uploads, findings, caches, emails, session state) live together in
the single file at app.config.DATABASE_PATH.

`get_config_session()`/`get_config_engine()` and `get_data_session()`/
`get_data_engine()` are synonyms of `get_session()`/`get_engine()`, kept only
so the ~370 existing call sites keep working; they no longer mean anything
different from each other.
"""

from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_PATH

Base = declarative_base()

DB_PATH = DATABASE_PATH

_engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
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
