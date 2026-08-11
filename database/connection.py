"""SQLite database connection setup using SQLAlchemy.

Two-engine design for complete User Mode / Developer Mode isolation
(see app/mode_state.py):

- **Config engine** — always the main database file. Holds the
  per-environment *configuration* tables (app_settings, rule_parameters,
  feature_flags) plus the User environment's own *data* tables. Config
  services (email/geoapify settings, rule parameters, feature flags, dev
  auth, publish) always use this engine so the settings system is reachable
  regardless of which mode is active.

- **Data engine** — mode-dependent. In User Mode it *is* the config/main
  engine, so production data lives exactly where it always has (nothing
  changes for existing installs). In Developer Mode it points at a
  completely separate database file, so every uploaded workbook, hierarchy,
  cache, finding, email-queue row, and session-state row created in
  Developer Mode is physically isolated — nothing done in one mode is ever
  visible in the other. The only thing that crosses over is an explicit
  Publish to User Mode, which copies configuration (never data).

Rule of thumb at call sites: configuration -> get_config_session() /
get_config_engine(); everything else (uploads, findings, caches, emails,
session) -> get_session()/get_data_session()/get_data_engine(), which
follow the current mode automatically.
"""

from loguru import logger
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_PATH
from app.mode_state import current_environment

Base = declarative_base()

# The main file: configuration for every environment + the User (production)
# environment's data.
CONFIG_DB_PATH = DATABASE_PATH
# The Developer environment's data lives entirely on its own.
DEV_DATA_DB_PATH = DATABASE_PATH.parent / "saffron_validator_dev.db"

_config_engine = create_engine(f"sqlite:///{CONFIG_DB_PATH}", echo=False)
_dev_data_engine = create_engine(f"sqlite:///{DEV_DATA_DB_PATH}", echo=False)

# In User Mode the data engine and the config/main engine are the same file
# (existing production data stays put). In Developer Mode data is fully
# separate.
_DATA_ENGINES = {"user": _config_engine, "developer": _dev_data_engine}

_ConfigSession = sessionmaker(bind=_config_engine, autoflush=False, autocommit=False)


def get_config_engine():
    """The main-database engine — configuration and the User environment's
    data. Always the same file regardless of mode."""
    return _config_engine


def get_data_engine():
    """The current mode's data engine (User = main file, Developer = its own
    separate file)."""
    return _DATA_ENGINES[current_environment()]


def get_config_session():
    """A session bound to the config/main database — used by the settings/
    feature-flag/dev-auth/publish services."""
    return _ConfigSession()


def get_data_session():
    """A session bound to the current mode's data database."""
    return sessionmaker(bind=get_data_engine(), autoflush=False, autocommit=False)()


def get_session():
    """Backward-compatible default — the current mode's DATA session. Most
    callers (uploads, findings, caches, emails, session state) are data
    callers; configuration services call get_config_session() explicitly."""
    return get_data_session()


def init_db() -> None:
    """Create tables on both databases if they don't already exist — the
    main/config database and the separate Developer-data database — so
    switching modes always finds a ready schema."""
    from database import models  # noqa: F401  (registers models on Base.metadata)

    Base.metadata.create_all(bind=_config_engine)
    Base.metadata.create_all(bind=_dev_data_engine)
    logger.info(f"Databases initialized: config/user at {CONFIG_DB_PATH}, developer data at {DEV_DATA_DB_PATH}")
