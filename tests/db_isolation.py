"""The ONE shared database isolation helper for this test suite -- see
database/connection.py's own note above `_engine`/`_Session` for why this
exists: those are two SEPARATE module-level singletons (get_engine()/
get_data_engine()/get_config_engine() read `_engine` directly;
get_session()/get_data_session()/get_config_session() go through
`_Session`), and a test that patches only one leaves the other pointed at
the REAL on-disk database. Confirmed the hard way once already (an early
version of test_hierarchy_module_split.py patched only `_Session` and
ended up writing fake rows into this project's real production database
via app/hierarchy_parser.py's `_engine`-based raw SQL).

Usage -- either form patches BOTH singletons together in one call, so no
test has to remember the split by hand:

    def test_something(monkeypatch):
        isolate_database(monkeypatch)
        ...

or, as a fixture:

    def test_something(isolated_db):
        ...
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.connection import Base


def isolate_database(monkeypatch):
    """Redirects BOTH database.connection._Session and
    database.connection._engine to the same fresh in-memory SQLite
    database. Returns the engine, for a caller that wants to seed rows
    into it directly. This is the ONLY place in the test suite that makes
    this guarantee -- every other test-local "in-memory session factory"
    helper only ever covered the _Session half.

    `import database.models` below is NOT unused -- Base.metadata only
    knows about a model class once its module has been imported somewhere
    (SQLAlchemy's declarative registration is an import-time side effect),
    and this function must not depend on some OTHER already-collected test
    file having imported database.models first. Confirmed the hard way:
    a test file whose own imports are all deferred inside test bodies (a
    pattern used throughout this suite) hit "no such table" for a model
    that genuinely exists in database/models.py, purely because nothing
    had imported that module yet by the time create_all() ran here."""
    import database.models  # noqa: F401

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("database.connection._Session", sessionmaker(bind=engine, autoflush=False, autocommit=False))
    monkeypatch.setattr("database.connection._engine", engine)
    return engine


@pytest.fixture
def isolated_db(monkeypatch):
    """Fixture form of isolate_database() -- depend on this directly in a
    test's signature, or from your own autouse fixture if you need to seed
    data before other fixtures run."""
    return isolate_database(monkeypatch)
