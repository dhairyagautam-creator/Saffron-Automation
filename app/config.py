"""Central configuration for paths used across the application.

Path resolution has to work in two very different situations:

- Running from source (`python main.py`): everything is relative to the
  project directory, found via `__file__`. Always writable (it's a dev
  checkout), so writable data stays there too.
- Running as an installed PyInstaller executable (`sys.frozen` is set):
  `BASE_DIR` (next to the .exe) is READ-ONLY to a standard user once the
  installer places it under Program Files — only an admin can write there.
  Writable data (the database, logs, reports) therefore must NOT live next
  to the executable; it goes under the per-user, always-writable
  `%LOCALAPPDATA%/Saffron Validator/` instead, exactly like every other
  well-behaved Windows desktop app. Read-only bundled resources (assets/)
  are unpacked by PyInstaller under `sys._MEIPASS` and are read from there
  regardless.

  A one-time migration copies any database this app previously wrote next
  to the .exe (from before this split existed, or from a machine where it
  happened to run elevated) into the new writable location, so no existing
  user's settings/history are lost.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

from loguru import logger

# Set if even the fallback (%TEMP%) turns out unwritable -- astronomically
# unlikely (that would mean the current user can't write anywhere at all),
# but main.py checks this before touching the database so that case produces
# a clear message instead of a raw sqlite3.OperationalError traceback.
DATA_DIR_ERROR: str | None = None


def _resolve_writable_data_dir(preferred: Path) -> Path:
    """Return the first of [preferred, %TEMP%\\Saffron Validator] that is
    actually creatable AND writable by the current user -- not just
    creatable, since a directory can exist and still reject writes. Verified
    with a real probe file rather than trusting mkdir() alone, because
    mkdir(exist_ok=True) on a read-only directory that already exists
    succeeds without ever touching the filesystem's write permission."""
    global DATA_DIR_ERROR
    candidates = [preferred, Path(tempfile.gettempdir()) / "Saffron Validator"]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate
        except Exception as exc:
            logger.warning(f"Data directory candidate not writable ({candidate}): {exc!r}")
            continue
    DATA_DIR_ERROR = (
        f"Could not find any writable location for application data.\n"
        f"Tried: {', '.join(str(c) for c in candidates)}"
    )
    return preferred  # nothing usable; caller (main.py) checks DATA_DIR_ERROR before proceeding


_DATA_DIR_OVERRIDE = os.environ.get("SAFFRON_DATA_DIR")

if _DATA_DIR_OVERRIDE:
    # Manual multi-instance testing only (run two real `python main.py`
    # processes side by side against completely separate data -- see
    # sign_in_for_test_inventory_sync.py's docstring for the matching
    # keyring-isolation half of this, SAFFRON_KEYRING_ACCOUNT in
    # app/auth_service.py). Unset in every normal install; only DATA_DIR
    # (and everything derived from it below) is redirected -- BASE_DIR/
    # ASSETS_DIR still resolve from the source layout normally.
    BASE_DIR = Path(__file__).resolve().parent.parent
    ASSETS_DIR = BASE_DIR / "assets"
    DATA_DIR = Path(_DATA_DIR_OVERRIDE).resolve()
elif getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    ASSETS_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR)) / "assets"
    DATA_DIR = _resolve_writable_data_dir(Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Saffron Validator")
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    ASSETS_DIR = BASE_DIR / "assets"
    DATA_DIR = BASE_DIR

LOGS_DIR = DATA_DIR / "logs"
DATABASE_PATH = DATA_DIR / "database" / "saffron_validator.db"
REPORTS_DIR = DATA_DIR / "reports"
# Physical copies of Review System uploads (see app/review_upload_service.py)
# -- one file per slot, named by slot_id so a replace just overwrites it.
# Never committed (see .gitignore) -- only the file path + validation state
# are ever recorded in the database.
REVIEW_UPLOADS_DIR = DATA_DIR / "review_uploads"
# Physical copies of the Inventory sync slice's two retained uploads
# (Sales Report, Inventory Report) -- see app/inventory_upload_service.py.
# Same convention as REVIEW_UPLOADS_DIR above: never committed (.gitignore),
# only the file path + metadata are ever recorded in the database.
INVENTORY_UPLOADS_DIR = DATA_DIR / "inventory_uploads"
# Physical copies of Work Distribution's retained uploads -- one file per
# (report_type, division) slot: RGD Coverage x3 divisions, ABM x3, RBM x3
# (9 total) -- see app/work_distribution_upload_service.py. Same convention
# as REVIEW_UPLOADS_DIR/INVENTORY_UPLOADS_DIR above: never committed
# (.gitignore), only the file path + metadata are ever recorded in the
# database.
WORK_DISTRIBUTION_UPLOADS_DIR = DATA_DIR / "work_distribution_uploads"
# Physical copies of Work Distribution's hierarchy workbook uploads -- one
# per division (Onyx/Guardians/Xandra), see app/hierarchy_upload_service.py.
# Previously hierarchy connections stored only a raw OS path (see
# app/workbook_connections.py) with no local copy at all; this is that
# missing retention layer, same convention as the other *_UPLOADS_DIR
# constants above: never committed (.gitignore), only the file path +
# metadata are ever recorded in the database.
HIERARCHY_UPLOADS_DIR = DATA_DIR / "hierarchy_uploads"
# Physical copies of Path Validator's retained daily-call-report uploads --
# one per division (Onyx/Guardians/Xandra), see
# app/path_validator_upload_service.py. Same convention as the other
# *_UPLOADS_DIR constants above: never committed (.gitignore), only the
# file path + metadata are ever recorded in the database.
PATH_VALIDATOR_UPLOADS_DIR = DATA_DIR / "path_validator_uploads"

if DATA_DIR_ERROR is None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        REVIEW_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        INVENTORY_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        WORK_DISTRIBUTION_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        HIERARCHY_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        PATH_VALIDATOR_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        DATA_DIR_ERROR = f"Could not create application data folders under {DATA_DIR}: {exc!r}"


def _migrate_legacy_data_next_to_exe() -> None:
    """One-time copy of database files from the old next-to-the-exe location
    (BASE_DIR) into the new writable DATA_DIR, for anyone upgrading from a
    build that predates this split. No-op (and never raises) once the
    writable copy exists or there's nothing legacy to migrate.

    Copies the WAL/SHM sidecar files alongside the main .db, not just the
    .db itself -- the connection is opened in WAL mode (see
    database/connection.py), and if the old install's last run didn't shut
    down cleanly, recent writes can still be sitting in -wal, not yet
    checkpointed into the .db file. Copying only the .db would silently
    leave that data behind. A -wal paired with its .db is valid at any
    path, so SQLite replays it normally the first time the new location is
    opened.

    Also skipped under SAFFRON_DATA_DIR (see above) -- that override makes
    BASE_DIR != DATA_DIR on purpose, for manual multi-instance testing on a
    dev machine, which is NOT a frozen build upgrading from its old
    next-to-the-exe location. Without this check, every fresh override
    folder's first run would silently copy this machine's real database
    (sitting at BASE_DIR/database/ in a source checkout) into the test
    folder -- confirmed the hard way once; never again."""
    if BASE_DIR == DATA_DIR or _DATA_DIR_OVERRIDE:
        return  # running from source (or a deliberate test override); there is no "legacy" location
    legacy_db_dir = BASE_DIR / "database"
    if not legacy_db_dir.is_dir():
        return
    try:
        for pattern in ("*.db", "*.db-wal", "*.db-shm"):
            for legacy_file in legacy_db_dir.glob(pattern):
                target = DATABASE_PATH.parent / legacy_file.name
                if target.exists():
                    continue
                shutil.copy2(legacy_file, target)
                logger.info(f"Migrated existing database file {legacy_file.name} to writable location: {target}")
    except Exception as exc:
        logger.warning(f"Legacy database migration skipped due to an error: {exc!r}")


_migrate_legacy_data_next_to_exe()

# Columns that must be present in an EQbit daily Excel export for it to be
# considered valid.
REQUIRED_COLUMNS = [
    "Division",
    "Zone",
    "Region",
    "Employee Name",
    "Employee Code",
    "Reporting HQ",
    "Desig.",
    "Reporting Sr. Name",
    "Reporting Sr. Emp Code",
    "Reporting Sr. Desig.",
    "Date",
    "Day",
    "Customer Type",
    "Code",
    "Name",
    "Category",
    "Specialty",
    "City/Place",
    "Visit Registration Time",
    "Visit Registration Date",
    "Actual Lat-Long",
]
