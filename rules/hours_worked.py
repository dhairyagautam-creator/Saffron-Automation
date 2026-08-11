"""Hours Worked Rule.

Flags an employee's day when the span between their earliest and latest
recorded visit time is below a configurable minimum-hours threshold —
i.e. the working day looks too short.

Mirrors rules/same_location.py exactly in shape: evaluated only against the
active session file's rows (import_id), re-running for the same import_id
replaces only that import's HOURS_WORKED findings and carries forward any
reviewer status, and the threshold is read from rule_parameters so it can be
changed on the Parameters page with no code change.

Unlike Same Location, this rule applies to EVERY employee/day (no BM/ABM
designation filter) — that's the stated spec for Hours Worked.
"""

from datetime import datetime

import pandas as pd
from loguru import logger
from sqlalchemy import inspect, text

from app.rule_parameters import get_parameters
from app.timing import get_current_report
from database.connection import get_data_engine, get_session
from database.import_service import IMPORT_ID_COLUMN, RAW_VISITS_TABLE
from database.models import InvestigationFinding

RULE_NAME = "HOURS_WORKED"

EMPLOYEE_NAME_COLUMN = "Employee Name"
EMPLOYEE_CODE_COLUMN = "Employee Code"
DATE_COLUMN = "Date"
VISIT_TIME_COLUMN = "Visit Registration Time"  # same column app/metrics.py orders on
DIVISION_COLUMN = "Division"  # display only

DEFAULT_MIN_HOURS = 6.0  # fallback if ensure_defaults() hasn't seeded the param yet


def _hours_worked(times: pd.Series) -> float | None:
    """Span from earliest to latest parseable visit time, in hours.
    None if the group has no parseable times. A single visit -> 0.0h."""
    times = times.dropna()
    if times.empty:
        return None
    return (times.max() - times.min()).total_seconds() / 3600.0


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


def evaluate(import_id: int) -> dict:
    """Recompute HOURS_WORKED findings for only the given import's rows.
    Findings from a different import_id are left untouched. Returns a dict
    with `findings_count`."""
    params = get_parameters(RULE_NAME)
    min_hours = float(params.get("minimum_hours_threshold", DEFAULT_MIN_HOURS))

    logger.info(f"Evaluating {RULE_NAME} for import_id={import_id}: minimum_hours={min_hours}")

    with get_current_report().timed("Hours worked"):
        df = _read_active_visits(import_id)

        findings = []
        if not df.empty:
            df["_visit_date"] = pd.to_datetime(df[DATE_COLUMN], format="%d-%m-%Y", errors="coerce").dt.date
            df["_visit_time"] = pd.to_datetime(df[VISIT_TIME_COLUMN], format="mixed", errors="coerce")

            for (emp_name, emp_code, visit_date), group in df.groupby(
                [EMPLOYEE_NAME_COLUMN, EMPLOYEE_CODE_COLUMN, "_visit_date"], dropna=False
            ):
                hours = _hours_worked(group["_visit_time"])
                if hours is None:
                    continue  # no parseable times this day -- can't judge

                if hours < min_hours:
                    earliest = group["_visit_time"].min()
                    latest = group["_visit_time"].max()
                    division = group[DIVISION_COLUMN].iloc[0] if DIVISION_COLUMN in group.columns else None
                    message = (
                        f"Worked {hours:.1f}h ({earliest:%H:%M}–{latest:%H:%M}), "
                        f"below the {min_hours:g}h minimum. Review Required."
                    )
                    findings.append(
                        {
                            "employee_name": emp_name,
                            "employee_code": emp_code,
                            "visit_date": visit_date,
                            "message": message,
                            "division": division,
                        }
                    )

        session = get_session()
        try:
            # Carry forward any reviewer status for a still-matching finding,
            # same as rules/same_location.py.
            existing_status = {
                (row.employee_code, row.visit_date): row.status
                for row in session.query(InvestigationFinding)
                .filter_by(rule_name=RULE_NAME, import_id=import_id)
                .all()
            }
            session.query(InvestigationFinding).filter_by(
                rule_name=RULE_NAME, import_id=import_id
            ).delete()
            for finding in findings:
                status = existing_status.get((finding["employee_code"], finding["visit_date"]), "Open")
                session.add(
                    InvestigationFinding(
                        import_id=import_id,
                        employee_name=finding["employee_name"],
                        employee_code=finding["employee_code"],
                        visit_date=finding["visit_date"],
                        rule_name=RULE_NAME,
                        message=finding["message"],
                        division=finding["division"],
                        status=status,
                        created_at=datetime.now(),
                        updated_at=datetime.now(),
                    )
                )
            session.commit()
        finally:
            session.close()

    logger.info(f"{RULE_NAME}: {len(findings)} finding(s) generated for import_id={import_id}")
    return {"findings_count": len(findings)}


def _demo() -> None:
    """Self-check for the pure hours computation + threshold decision."""
    t = lambda s: pd.to_datetime(s, format="mixed")
    day = pd.Series([t("2026-08-07 10:00"), t("2026-08-07 15:30"), pd.NaT])
    assert abs(_hours_worked(day) - 5.5) < 1e-9
    assert _hours_worked(pd.Series([t("2026-08-07 09:00")])) == 0.0  # single visit
    assert _hours_worked(pd.Series([pd.NaT, pd.NaT])) is None  # nothing parseable
    assert _hours_worked(pd.Series([], dtype="datetime64[ns]")) is None
    print("hours_worked self-check passed")


if __name__ == "__main__":
    _demo()
