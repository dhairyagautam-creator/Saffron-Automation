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

# The module_configurations.module_key this module's KPI thresholds sync
# under (parameter sync project, Phase 3) -- combined with
# ManagerWorkAllocationParameter's own values into one shared blob under
# this same key (see app/parameter_sync_service.py and
# app/manager_work_allocation_parameters_service.py's own
# get_full_configuration()), matching module_registry's canonical
# "work_distribution" module key rather than this table's own name.
MODULE_KEY = "work_distribution"


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


# --- Cloud config sync (parameter sync project, Phase 3) ------------------
# One shared blob under module_key "work_distribution" covers BOTH this
# module's own 6 KPI thresholds and Manager Work Allocation's own 2 --
# they live in two separate local tables and are edited by two separate
# Save buttons on the same Work Distribution Settings page, but sync
# together as one config, matching module_registry's single
# "work_distribution" module key rather than one row per local table.

def get_full_configuration() -> dict:
    """This module's own 6 KPI thresholds plus Manager Work Allocation's
    own 2 (see app.manager_work_allocation_parameters_service.get_full_configuration),
    merged into one dict -- the complete config pushed under MODULE_KEY."""
    from app.manager_work_allocation_parameters_service import get_full_configuration as _get_mwa_configuration

    return {**get_all(), **_get_mwa_configuration()}


def apply_full_configuration(config: dict) -> tuple[bool, str | None]:
    """Writes a pulled config blob back into local storage: this
    module's own KPI keys (present in `config`, using the same
    _set()), then Manager Work Allocation's own keys via
    app.manager_work_allocation_parameters_service.apply_full_configuration,
    which validates rbm_flag_tiers before writing anything for its half.
    Returns (True, None) on success, or (False, error_message) if the
    Manager Work Allocation half rejects the blob -- in that case, this
    module's OWN KPI keys are still applied (they have no cross-field
    validation to fail), so a bad rbm_flag_tiers on the remote side never
    blocks a genuinely valid KPI update from the same pull. A KPI key
    missing from `config` is left untouched, never blanked."""
    for name in DEFAULTS:
        if name in config:
            _set(name, str(config[name]))

    from app.manager_work_allocation_parameters_service import apply_full_configuration as _apply_mwa_configuration

    return _apply_mwa_configuration(config)
