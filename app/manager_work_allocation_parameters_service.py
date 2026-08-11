"""Editable thresholds for the Manager Work Allocation engine (see
app/manager_work_allocation_service.py for ABM,
app/manager_work_allocation_rbm_service.py for RBM) -- never hardcoded.
Mirrors app/work_distribution_parameters_service.py's flat parameter-store
shape, but against its own ManagerWorkAllocationParameter table -- Manager
Work Allocation is a completely independent engine from RGD Coverage, so
its settings storage stays independent too.

Two genuinely different value shapes live here, so two separate storage
paths:
- ABM's Minimum Joint Working Days is a single float, using the existing
  `_get`/`_set` (which always parses/serializes as float) -- UNCHANGED
  from before this phase.
- RBM's flag tiers (Phase 3.1) are a small ORDERED LIST of {min, max,
  missed} dicts (`max` is None for "no upper limit", allowed only on the
  last tier -- see app.manager_work_allocation_rbm_service.validate_rbm_flag_tiers
  for the validation rules enforced before a save is ever accepted). A
  single float can't represent that shape, so it's stored as its own JSON-
  encoded parameter_value via the separate `_get_raw`/`_set_raw` below,
  kept OUT of the float-typed `_get`/`_set`/`get_all()`/DEFAULTS machinery
  entirely -- same reasoning as
  app.work_distribution_email_settings_service keeping credentials out of
  that module's own KPI-only `get_all()`.

Like Work Distribution's own KPI thresholds, a saved change here does not
retroactively re-flag any already-generated ManagerWorkAllocationFinding
row -- it only takes effect the next time a Manager Work Allocation report
is uploaded and processed.
"""

import json

from loguru import logger

from database.connection import get_config_session
from database.models import ManagerWorkAllocationParameter

MINIMUM_JOINT_WORKING_DAYS = "minimum_joint_working_days"
RBM_FLAG_TIERS = "rbm_flag_tiers"

DEFAULTS = {
    MINIMUM_JOINT_WORKING_DAYS: "4",
}

# Backward-compatibility default (Phase 3.1): an installation upgrading
# from before RBM thresholds were configurable gets exactly the values the
# RBM engine used to hardcode, so nothing changes in its findings until the
# user actually edits Settings -- see ensure_defaults() below, which seeds
# this the same "only if missing" way as every other default here.
DEFAULT_RBM_FLAG_TIERS = [
    {"min": 8, "max": 10, "missed": 1},
    {"min": 11, "max": 13, "missed": 2},
    {"min": 14, "max": None, "missed": 4},
]

# The Supabase module_configurations.module_key this module would sync
# under, once cloud sync is built for it (not yet -- out of this phase's
# scope). Kept here now, unused, mirroring
# app.work_distribution_parameters_service.MODULE_KEY's own role.
MODULE_KEY = "manager_work_allocation_parameters"


def ensure_defaults() -> None:
    """Insert each parameter's default value if it doesn't already exist --
    never overwrites a value the user has already customized. Call once at
    application startup, same as the other *_service.ensure_defaults()."""
    session = get_config_session()
    try:
        for parameter_name, default_value in DEFAULTS.items():
            exists = session.query(ManagerWorkAllocationParameter).filter_by(parameter_name=parameter_name).first()
            if not exists:
                session.add(ManagerWorkAllocationParameter(parameter_name=parameter_name, parameter_value=default_value))
                logger.info(f"Initialized default Manager Work Allocation parameter {parameter_name} = {default_value}")

        tiers_exist = session.query(ManagerWorkAllocationParameter).filter_by(parameter_name=RBM_FLAG_TIERS).first()
        if not tiers_exist:
            session.add(ManagerWorkAllocationParameter(
                parameter_name=RBM_FLAG_TIERS, parameter_value=json.dumps(DEFAULT_RBM_FLAG_TIERS),
            ))
            logger.info(f"Initialized default Manager Work Allocation parameter {RBM_FLAG_TIERS} = {DEFAULT_RBM_FLAG_TIERS}")
        session.commit()
    finally:
        session.close()


def _get_raw(parameter_name: str, default: str) -> str:
    session = get_config_session()
    try:
        row = session.query(ManagerWorkAllocationParameter).filter_by(parameter_name=parameter_name).first()
        return row.parameter_value if row else default
    finally:
        session.close()


def _set_raw(parameter_name: str, value: str) -> None:
    session = get_config_session()
    try:
        row = session.query(ManagerWorkAllocationParameter).filter_by(parameter_name=parameter_name).first()
        if row:
            row.parameter_value = value
        else:
            session.add(ManagerWorkAllocationParameter(parameter_name=parameter_name, parameter_value=value))
        session.commit()
    finally:
        session.close()
    logger.info(f"Saved Manager Work Allocation parameter {parameter_name} = {value}")


def get_rbm_flag_tiers() -> list:
    """[{"min": int, "max": int | None, "missed": int}, ...] in ascending
    order -- `max: None` means "no upper limit" (only valid on the last
    tier; enforced by app.manager_work_allocation_rbm_service.validate_rbm_flag_tiers
    before any save, not re-validated here on read). Read fresh from
    Settings by app.manager_work_allocation_rbm_service on every Run
    Analysis -- never cached, never hardcoded."""
    raw = _get_raw(RBM_FLAG_TIERS, json.dumps(DEFAULT_RBM_FLAG_TIERS))
    try:
        tiers = json.loads(raw)
        if isinstance(tiers, list) and tiers:
            return tiers
    except (TypeError, ValueError):
        pass
    return [dict(tier) for tier in DEFAULT_RBM_FLAG_TIERS]


def set_rbm_flag_tiers(tiers: list) -> None:
    """Stores `tiers` verbatim as JSON -- callers (the Settings page) are
    responsible for calling
    app.manager_work_allocation_rbm_service.validate_rbm_flag_tiers first
    and refusing to save on any validation error, same convention as every
    other Settings page field in this app (validate in the UI layer, keep
    the storage layer a dumb read/write)."""
    _set_raw(RBM_FLAG_TIERS, json.dumps(tiers))


def _get(parameter_name: str) -> float:
    session = get_config_session()
    try:
        row = session.query(ManagerWorkAllocationParameter).filter_by(parameter_name=parameter_name).first()
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
        row = session.query(ManagerWorkAllocationParameter).filter_by(parameter_name=parameter_name).first()
        if row:
            row.parameter_value = value
        else:
            session.add(ManagerWorkAllocationParameter(parameter_name=parameter_name, parameter_value=value))
        session.commit()
    finally:
        session.close()
    logger.info(f"Saved Manager Work Allocation parameter {parameter_name} = {value}")


def get_minimum_joint_working_days() -> float:
    return _get(MINIMUM_JOINT_WORKING_DAYS)


def set_minimum_joint_working_days(value: str) -> None:
    _set(MINIMUM_JOINT_WORKING_DAYS, value)


def get_all() -> dict:
    """{parameter_name: float value} for every configured threshold -- read
    directly by app.manager_work_allocation_service's ABM evaluation on
    every upload, so calculations always use the current Settings value,
    never a hardcoded number."""
    return {name: _get(name) for name in DEFAULTS}
