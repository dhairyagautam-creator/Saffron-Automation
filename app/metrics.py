"""Computes per-employee, per-day movement metrics for the active session file.

`employee_daily_metrics` always reflects only the currently active import —
it's fully replaced each time `calculate_metrics` runs, scoped to that
import's rows, so previously imported files never inflate these numbers.
"""

import pandas as pd
from loguru import logger
from sqlalchemy import inspect, text

from app.geo_utils import haversine_km
from app.timing import get_current_report
from database.connection import get_data_engine
from database.import_service import IMPORT_ID_COLUMN, RAW_VISITS_TABLE

METRICS_TABLE = "employee_daily_metrics"

EMPLOYEE_NAME_COLUMN = "Employee Name"
EMPLOYEE_CODE_COLUMN = "Employee Code"
DATE_COLUMN = "Date"
VISIT_TIME_COLUMN = "Visit Registration Time"
DOCTOR_CODE_COLUMN = "Code"

METRICS_COLUMNS = [
    "employee_name",
    "employee_code",
    "visit_date",
    "total_calls",
    "unique_doctors",
    "first_visit_time",
    "last_visit_time",
    "total_distance_km",
    "valid_coordinate_visits",
    "invalid_coordinate_visits",
]


def _compute_group_metrics(group: pd.DataFrame) -> pd.Series:
    group = group.sort_values("_visit_time_sort", na_position="last")

    has_coords = group["latitude"].notna() & group["longitude"].notna()
    valid = group[has_coords]
    invalid_count = len(group) - len(valid)

    coords = list(zip(valid["latitude"], valid["longitude"]))
    total_distance_km = sum(
        haversine_km(lat1, lon1, lat2, lon2)
        for (lat1, lon1), (lat2, lon2) in zip(coords, coords[1:])
    )

    return pd.Series(
        {
            "total_calls": len(group),
            "unique_doctors": group[DOCTOR_CODE_COLUMN].nunique(),
            "first_visit_time": group[VISIT_TIME_COLUMN].iloc[0],
            "last_visit_time": group[VISIT_TIME_COLUMN].iloc[-1],
            "total_distance_km": round(total_distance_km, 3),
            "valid_coordinate_visits": len(valid),
            "invalid_coordinate_visits": invalid_count,
        }
    )


def _read_active_visits(import_id: int) -> pd.DataFrame:
    if not inspect(get_data_engine()).has_table(RAW_VISITS_TABLE):
        return pd.DataFrame()
    with get_data_engine().connect() as conn:
        existing_cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({RAW_VISITS_TABLE})"))}
    if IMPORT_ID_COLUMN not in existing_cols:
        return pd.DataFrame()
    return pd.read_sql_query(
        text(f"SELECT * FROM {RAW_VISITS_TABLE} WHERE {IMPORT_ID_COLUMN} = :iid"),
        con=get_data_engine(),
        params={"iid": import_id},
    )


def calculate_metrics(import_id: int) -> dict:
    """Recompute `employee_daily_metrics` for only the given import's rows,
    replacing the table's previous contents entirely.

    Returns a dict with `total_visits`, `total_employees`, and
    `average_distance_km` for this import.
    """
    logger.info(f"Computing employee daily metrics for import_id={import_id}")
    with get_current_report().timed("Metrics calculation"):
        df = _read_active_visits(import_id)

        if df.empty:
            logger.warning(f"No rows found for import_id={import_id}; clearing metrics")
            empty = pd.DataFrame(columns=METRICS_COLUMNS)
            empty.to_sql(METRICS_TABLE, con=get_data_engine(), if_exists="replace", index=False)
            return {"total_visits": 0, "total_employees": 0, "average_distance_km": 0.0}

        df["_visit_date"] = pd.to_datetime(df[DATE_COLUMN], format="%d-%m-%Y", errors="coerce").dt.date
        df["_visit_time_sort"] = pd.to_datetime(df[VISIT_TIME_COLUMN], format="mixed", errors="coerce")

        grouped = df.groupby([EMPLOYEE_NAME_COLUMN, EMPLOYEE_CODE_COLUMN, "_visit_date"], dropna=False)
        logger.info(f"Grouped into {grouped.ngroups} employee/day combination(s)")

        metrics = grouped.apply(_compute_group_metrics, include_groups=False).reset_index()
        metrics = metrics.rename(
            columns={
                EMPLOYEE_NAME_COLUMN: "employee_name",
                EMPLOYEE_CODE_COLUMN: "employee_code",
                "_visit_date": "visit_date",
            }
        )
        metrics = metrics[METRICS_COLUMNS]

        metrics.to_sql(METRICS_TABLE, con=get_data_engine(), if_exists="replace", index=False)
        logger.info(f"Wrote {len(metrics)} row(s) to '{METRICS_TABLE}' for import_id={import_id}")

        total_visits = len(df)
        total_employees = df[EMPLOYEE_CODE_COLUMN].nunique()
        average_distance_km = float(metrics["total_distance_km"].mean()) if len(metrics) else 0.0

        logger.info(
            f"Metrics summary: {total_visits} visit(s) processed, "
            f"{total_employees} employee(s) analyzed, "
            f"average distance {average_distance_km:.1f} km"
        )

    return {
        "total_visits": total_visits,
        "total_employees": total_employees,
        "average_distance_km": average_distance_km,
    }
