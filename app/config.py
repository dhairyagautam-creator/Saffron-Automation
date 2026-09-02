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


if getattr(sys, "frozen", False):
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

if DATA_DIR_ERROR is None:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        REVIEW_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        DATA_DIR_ERROR = f"Could not create application data folders under {DATA_DIR}: {exc!r}"


def _migrate_legacy_data_next_to_exe() -> None:
    """One-time copy of database files from the old next-to-the-exe location
    (BASE_DIR) into the new writable DATA_DIR, for anyone upgrading from a
    build that predates this split. No-op (and never raises) once the
    writable copy exists or there's nothing legacy to migrate."""
    if BASE_DIR == DATA_DIR:
        return  # running from source; there is no "legacy" location
    legacy_db_dir = BASE_DIR / "database"
    if not legacy_db_dir.is_dir():
        return
    try:
        for legacy_db in legacy_db_dir.glob("*.db"):
            target = DATABASE_PATH.parent / legacy_db.name
            if target.exists():
                continue
            shutil.copy2(legacy_db, target)
            logger.info(f"Migrated existing database {legacy_db.name} to writable location: {target}")
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
