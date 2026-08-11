"""Same Location Rule.

Flags an employee's day as suspicious when a large share of their GPS-tagged
visits cluster within a small radius of each other (e.g. visits reported
from the same spot rather than actually traveling between customers).

Only BM and ABM employees are ever eligible to be flagged — RBM, AGM, SM,
GM, and anyone with no Organization Data hierarchy match at all are always
skipped, regardless of their GPS pattern. RBMs are notification recipients
only, never analysis subjects.

Evaluated only against the active session file's rows (import_id) — a
previously imported file's data never contributes to findings unless it's
the active session. Re-running the rule for the same import_id replaces
only that import's findings, using whatever parameters are currently stored
in rule_parameters, so changing a threshold on the Parameters page takes
effect on the next run without any code change.
"""

import random
from datetime import datetime

import pandas as pd
from loguru import logger
from sqlalchemy import inspect, text

from app.geo_utils import haversine_km
from app.hierarchy_parser import ANALYZABLE_DESIGNATIONS, get_all_designations
from app.rule_parameters import get_parameters
from app.timing import get_current_report
from database.connection import get_data_engine, get_session
from database.import_service import IMPORT_ID_COLUMN, RAW_VISITS_TABLE
from database.models import InvestigationFinding

RULE_NAME = "SAME_LOCATION"

EMPLOYEE_NAME_COLUMN = "Employee Name"
EMPLOYEE_CODE_COLUMN = "Employee Code"
DATE_COLUMN = "Date"
# Display-only field surfaced on the Findings page (see ui/findings_page.py)
# -- never read by any clustering or threshold logic below.
DIVISION_COLUMN = "Division"


def _largest_cluster(coords: list[tuple[float, float]], radius_km: float) -> tuple[int, float, float]:
    """Return (size, anchor_lat, anchor_lon) for the largest neighborhood:
    for each point, count how many points (including itself) fall within
    `radius_km`, and take the max. The anchor is the point that achieves
    that max — i.e. the center of the cluster that would cause a flag —
    used later by Hospital Suppression to check what's physically near
    that exact location (see app/hospital_service.py)."""
    best_count = 0
    best_anchor = coords[0]
    for lat1, lon1 in coords:
        count = sum(
            1 for lat2, lon2 in coords if haversine_km(lat1, lon1, lat2, lon2) <= radius_km
        )
        if count > best_count:
            best_count = count
            best_anchor = (lat1, lon1)
    return best_count, best_anchor[0], best_anchor[1]


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
    """Recompute SAME_LOCATION findings for only the given import's rows.

    Only findings already tagged with this import_id are replaced — findings
    belonging to a different (inactive) import are left untouched.

    Returns a dict with `findings_count`.
    """
    params = get_parameters(RULE_NAME)
    radius_m = float(params["same_place_radius_meters"])
    threshold_pct = float(params["concentration_threshold_percent"])
    min_visits = int(float(params["minimum_valid_gps_visits"]))
    radius_km = radius_m / 1000.0

    logger.info(
        f"Evaluating {RULE_NAME} for import_id={import_id}: radius={radius_m}m, "
        f"threshold={threshold_pct}%, min_visits={min_visits}"
    )

    # --- TEMPORARY DIAGNOSTIC COUNTERS (remove after debugging) ---
    # Pure observation of decisions the code below already makes -- nothing
    # here changes what gets filtered, clustered, or flagged.
    diag_total_records = 0
    diag_rows_missing_gps = 0
    diag_total_groups = 0
    diag_skipped_not_analyzable = 0
    diag_skipped_below_min_visits = 0
    diag_analyzed_records = []  # every group that passed both filters, whether or not it became a finding
    # --- END TEMPORARY DIAGNOSTIC COUNTERS ---

    with get_current_report().timed("Validation & clustering"):
        df = _read_active_visits(import_id)
        designations = get_all_designations()
        diag_total_records = len(df)

        findings = []
        if not df.empty:
            df["_visit_date"] = pd.to_datetime(
                df[DATE_COLUMN], format="%d-%m-%Y", errors="coerce"
            ).dt.date
            valid = df[df["latitude"].notna() & df["longitude"].notna()]
            diag_rows_missing_gps = len(df) - len(valid)

            for (emp_name, emp_code, visit_date), group in valid.groupby(
                [EMPLOYEE_NAME_COLUMN, EMPLOYEE_CODE_COLUMN, "_visit_date"], dropna=False
            ):
                diag_total_groups += 1

                if designations.get(emp_code) not in ANALYZABLE_DESIGNATIONS:
                    diag_skipped_not_analyzable += 1
                    continue  # not a BM/ABM (or no Organization Data match) -- never analyzed

                total_valid_visits = len(group)
                if total_valid_visits < min_visits:
                    diag_skipped_below_min_visits += 1
                    continue

                coords = list(zip(group["latitude"], group["longitude"]))
                largest_cluster, anchor_lat, anchor_lon = _largest_cluster(coords, radius_km)
                concentration_percent = largest_cluster / total_valid_visits * 100

                # Same value for every row in this employee+date group --
                # display only, does not affect clustering or the threshold
                # check above.
                division = group[DIVISION_COLUMN].iloc[0] if DIVISION_COLUMN in group.columns else None

                # --- TEMPORARY DIAGNOSTIC: record every analyzed group ---
                diag_analyzed_records.append(
                    {
                        "employee_name": emp_name,
                        "employee_code": emp_code,
                        "visit_date": visit_date,
                        "total_valid_visits": total_valid_visits,
                        "largest_cluster": largest_cluster,
                        "concentration_percent": concentration_percent,
                        "anchor_lat": anchor_lat,
                        "anchor_lon": anchor_lon,
                    }
                )
                # --- END TEMPORARY DIAGNOSTIC ---

                if concentration_percent >= threshold_pct:
                    message = (
                        f"{largest_cluster} of {total_valid_visits} GPS visits "
                        f"({concentration_percent:.0f}%) occurred within approximately "
                        f"{int(radius_m)} meters of the same location. Review Required."
                    )
                    findings.append(
                        {
                            "employee_name": emp_name,
                            "employee_code": emp_code,
                            "visit_date": visit_date,
                            "message": message,
                            "concentration_percent": round(concentration_percent, 1),
                            "valid_visit_count": total_valid_visits,
                            "matched_visit_count": largest_cluster,
                            "radius_meters": int(radius_m),
                            "threshold_percent": threshold_pct,
                            "cluster_lat": anchor_lat,
                            "cluster_lon": anchor_lon,
                            "division": division,
                        }
                    )

        # --- TEMPORARY DIAGNOSTIC SUMMARY (remove after debugging) ---
        diag_unique_employees = df[EMPLOYEE_CODE_COLUMN].nunique() if not df.empty else 0
        diag_clusters_detected = sum(1 for r in diag_analyzed_records if r["largest_cluster"] >= 2)
        diag_distinct_analyzed_employees = len({r["employee_code"] for r in diag_analyzed_records})

        logger.info(
            "[DIAGNOSTIC] ===== Same Location Rule: stage-by-stage counts for import_id="
            f"{import_id} ====="
        )
        logger.info(f"[DIAGNOSTIC] Total records loaded (raw rows for this import): {diag_total_records}")
        logger.info(f"[DIAGNOSTIC] Total unique employees (by Employee Code, in raw rows): {diag_unique_employees}")
        logger.info(f"[DIAGNOSTIC] Rows dropped for missing/invalid GPS: {diag_rows_missing_gps}")
        logger.info(f"[DIAGNOSTIC] Total employee-day groups formed (valid GPS, grouped by employee+date): {diag_total_groups}")
        logger.info(
            f"[DIAGNOSTIC] Employee-day groups skipped -- not BM/ABM designation or no Organization "
            f"Data match: {diag_skipped_not_analyzable}"
        )
        logger.info(
            f"[DIAGNOSTIC] Employee-day groups skipped -- below minimum_valid_gps_visits "
            f"({min_visits}): {diag_skipped_below_min_visits}"
        )
        logger.info(
            f"[DIAGNOSTIC] Total BM/ABM employee-day groups analyzed (clustering actually run): "
            f"{len(diag_analyzed_records)} ({diag_distinct_analyzed_employees} distinct employee(s))"
        )
        logger.info(
            f"[DIAGNOSTIC] Location clusters detected (analyzed groups where 2+ visits fell within "
            f"{int(radius_m)}m of each other, regardless of threshold): {diag_clusters_detected}"
        )
        logger.info(
            f"[DIAGNOSTIC] Employees exceeding the {threshold_pct}% threshold this run: {len(findings)} "
            f"({len({f['employee_code'] for f in findings})} distinct employee(s))"
        )
        logger.info(
            "[DIAGNOSTIC] Suppressed by nearby hospitals: not applicable at this stage -- Hospital "
            "Suppression runs later, only during email generation (app/notification_service.py), and "
            "never removes a finding from this count or from the Findings page -- it only withholds "
            "the notification for an already-flagged finding."
        )
        logger.info(f"[DIAGNOSTIC] Final flagged count (== findings written to the Findings page): {len(findings)}")

        if not findings:
            if diag_analyzed_records:
                sample = random.sample(diag_analyzed_records, min(5, len(diag_analyzed_records)))
                logger.info(
                    f"[DIAGNOSTIC] Final flagged count is 0. Sampling {len(sample)} of "
                    f"{len(diag_analyzed_records)} analyzed BM/ABM employee-day(s) for inspection:"
                )
                for r in sample:
                    logger.info(
                        f"[DIAGNOSTIC]   {r['employee_name']} ({r['employee_code']}) on {r['visit_date']}: "
                        f"total_calls={r['total_valid_visits']}, clustered_calls={r['largest_cluster']}, "
                        f"cluster_percent={r['concentration_percent']:.1f}%, "
                        f"largest_cluster_coords=({r['anchor_lat']}, {r['anchor_lon']})"
                    )
            else:
                logger.info(
                    "[DIAGNOSTIC] Final flagged count is 0, and zero BM/ABM employee-day groups were "
                    "analyzed at all -- nothing reached the clustering step. Check the counts above: "
                    "either no rows loaded, no rows had valid GPS, or every group was skipped as "
                    "not-BM/ABM / below minimum_valid_gps_visits."
                )
        logger.info("[DIAGNOSTIC] ===== end of stage-by-stage counts =====")
        # --- END TEMPORARY DIAGNOSTIC SUMMARY ---

        session = get_session()
        try:
            # Carry forward any status a reviewer already set (Reviewed/Ignored)
            # for a finding that still matches within this same import_id,
            # instead of resetting to Open every time the rule re-runs.
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
                status = existing_status.get(
                    (finding["employee_code"], finding["visit_date"]), "Open"
                )
                session.add(
                    InvestigationFinding(
                        import_id=import_id,
                        employee_name=finding["employee_name"],
                        employee_code=finding["employee_code"],
                        visit_date=finding["visit_date"],
                        rule_name=RULE_NAME,
                        message=finding["message"],
                        concentration_percent=finding["concentration_percent"],
                        valid_visit_count=finding["valid_visit_count"],
                        matched_visit_count=finding["matched_visit_count"],
                        radius_meters=finding["radius_meters"],
                        threshold_percent=finding["threshold_percent"],
                        cluster_lat=finding["cluster_lat"],
                        cluster_lon=finding["cluster_lon"],
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
