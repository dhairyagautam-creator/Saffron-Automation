"""Persists an imported Excel workbook into the database.

Every row inserted is tagged with the `import_id` of this import (see
_ensure_import_id_column), which is how the rest of the app knows which
rows belong to the "active session file" — see app/session_state.py.
"""

from datetime import datetime

import pandas as pd
from loguru import logger
from sqlalchemy import inspect, text

from app.timing import get_current_report
from database.connection import get_data_engine, get_session
from database.models import ImportHistory

RAW_VISITS_TABLE = "raw_visits"
IMPORT_ID_COLUMN = "import_id"


def _ensure_import_id_column() -> None:
    if not inspect(get_data_engine()).has_table(RAW_VISITS_TABLE):
        return
    with get_data_engine().begin() as conn:
        existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({RAW_VISITS_TABLE})"))}
        if IMPORT_ID_COLUMN not in existing:
            conn.execute(text(f"ALTER TABLE {RAW_VISITS_TABLE} ADD COLUMN {IMPORT_ID_COLUMN} INTEGER"))


def save_import(df: pd.DataFrame, file_name: str) -> dict:
    """Deduplicate `df` against itself, append it to `raw_visits` tagged
    with a new import_id, and record the import in import_history.

    Returns a dict with `rows_imported`, `duplicates_removed`, and
    `import_id` (the id of the new import_history row — callers use this to
    make the import the active session, see app/session_state.py).
    """
    with get_current_report().timed("Data persistence"):
        original_count = len(df)
        deduped_df = df.drop_duplicates().reset_index(drop=True)
        duplicates_removed = original_count - len(deduped_df)
        rows_imported = len(deduped_df)

        logger.info(
            f"'{file_name}': {original_count} row(s) read, "
            f"{duplicates_removed} duplicate(s) removed, "
            f"{rows_imported} row(s) to insert into '{RAW_VISITS_TABLE}'"
        )

        session = get_session()
        try:
            history = ImportHistory(
                file_name=file_name,
                imported_at=datetime.now(),
                rows_imported=rows_imported,
                duplicates_removed=duplicates_removed,
            )
            session.add(history)
            session.commit()
            import_id = history.id
        finally:
            session.close()

        _ensure_import_id_column()
        deduped_df = deduped_df.copy()
        deduped_df[IMPORT_ID_COLUMN] = import_id
        deduped_df.to_sql(RAW_VISITS_TABLE, con=get_data_engine(), if_exists="append", index=False)
        logger.info(f"Inserted {rows_imported} row(s) into '{RAW_VISITS_TABLE}' (import_id={import_id})")

    return {
        "rows_imported": rows_imported,
        "duplicates_removed": duplicates_removed,
        "import_id": import_id,
    }
