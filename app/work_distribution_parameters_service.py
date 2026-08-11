"""Editable KPI thresholds for Work Distribution (see
app/work_distribution_service.py) -- never hardcoded. Mirrors
app/inventory_parameters_service.py's flat parameter-store shape.

BM thresholds compare PERCENTAGES (missed doctor %, poor-coverage %)
against a BM's ~50-doctor book; ABM thresholds compare RAW COUNTS (missed
doctor count, poor-coverage count) against an ABM's ~75-doctor,
A-RGD-only book -- a real business-rule difference specified by the
module's own KPI spec, not an inconsistency.

Like Inventory's threshold multipliers, a saved change here does not
retroactively re-flag any already-generated WorkDistributionFinding row --
it only takes effect the next time a Work Distribution report is uploaded
and processed (see app/work_distribution_service.process_work_distribution_report).
"""

from loguru import logger

from database.connection import get_config_session
from database.models import WorkDistributionParameter

BM_MINIMUM_CALLS = "bm_minimum_calls"
BM_TARGET_CALLS = "bm_target_calls"
BM_MISSED_DOCTOR_PERCENT = "bm_missed_doctor_percent"
BM_COVERAGE_PERCENT = "bm_coverage_percent"
ABM_MISSED_DOCTORS = "abm_missed_doctors"
ABM_COVERAGE_DOCTORS = "abm_coverage_doctors"

DEFAULTS = {
    BM_MINIMUM_CALLS: "135",
    BM_TARGET_CALLS: "150",
    BM_MISSED_DOCTOR_PERCENT: "5",
    BM_COVERAGE_PERCENT: "10",
    ABM_MISSED_DOCTORS: "5",
    ABM_COVERAGE_DOCTORS: "10",
}

# The Supabase module_configurations.module_key this module would sync
# under, once cloud sync is built for it (not yet -- see the module's
# Phase 2 scope). Kept here now, unused, so a future sync wiring needs no
# renaming, mirroring app.inventory_parameters_service.MODULE_KEY's own
# role.
MODULE_KEY = "work_distribution_parameters"


def ensure_defaults() -> None:
    """Insert each parameter's default value if it doesn't already exist --
    never overwrites a value the user has already customized. Call once at
    application startup, same as the other *_service.ensure_defaults()."""
    session = get_config_session()
    try:
        for parameter_name, default_value in DEFAULTS.items():
            exists = session.query(WorkDistributionParameter).filter_by(parameter_name=parameter_name).first()
            if not exists:
                session.add(WorkDistributionParameter(parameter_name=parameter_name, parameter_value=default_value))
                logger.info(f"Initialized default Work Distribution parameter {parameter_name} = {default_value}")
        session.commit()
    finally:
        session.close()


def _get(parameter_name: str) -> float:
    session = get_config_session()
    try:
        row = session.query(WorkDistributionParameter).filter_by(parameter_name=parameter_name).first()
        value = row.parameter_value if row else DEFAULTS[parameter_name]
    finally:
        session.close()
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(DEFAULTS[parameter_name])


def _set(parameter_name: str, value: str) -> None:
    session = get_config_session()
    try:
        row = session.query(WorkDistributionParameter).filter_by(parameter_name=parameter_name).first()
        if row:
            row.parameter_value = value
        else:
            session.add(WorkDistributionParameter(parameter_name=parameter_name, parameter_value=value))
        session.commit()
    finally:
        session.close()
    logger.info(f"Saved Work Distribution parameter {parameter_name} = {value}")


def get_bm_minimum_calls() -> float:
    return _get(BM_MINIMUM_CALLS)


def set_bm_minimum_calls(value: str) -> None:
    _set(BM_MINIMUM_CALLS, value)


def get_bm_target_calls() -> float:
    return _get(BM_TARGET_CALLS)


def set_bm_target_calls(value: str) -> None:
    _set(BM_TARGET_CALLS, value)


def get_bm_missed_doctor_percent() -> float:
    return _get(BM_MISSED_DOCTOR_PERCENT)


def set_bm_missed_doctor_percent(value: str) -> None:
    _set(BM_MISSED_DOCTOR_PERCENT, value)


def get_bm_coverage_percent() -> float:
    return _get(BM_COVERAGE_PERCENT)


def set_bm_coverage_percent(value: str) -> None:
    _set(BM_COVERAGE_PERCENT, value)


def get_abm_missed_doctors() -> float:
    return _get(ABM_MISSED_DOCTORS)


def set_abm_missed_doctors(value: str) -> None:
    _set(ABM_MISSED_DOCTORS, value)


def get_abm_coverage_doctors() -> float:
    return _get(ABM_COVERAGE_DOCTORS)


def set_abm_coverage_doctors(value: str) -> None:
    _set(ABM_COVERAGE_DOCTORS, value)


def get_all() -> dict:
    """{parameter_name: float value} for every configured KPI threshold --
    read directly by app.work_distribution_service's KPI evaluation on
    every upload, so calculations always use the current Settings values,
    never a hardcoded number."""
    return {name: _get(name) for name in DEFAULTS}
