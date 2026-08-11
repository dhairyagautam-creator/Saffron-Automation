"""Editable parameters for Inventory Monitoring: the replenishment
threshold multiplier, the Central Warehouse (CWH) threshold multiplier,
the Excess Inventory Transfer Candidate multiplier, and the Threshold
Display Mode (see app/threshold_service.py, app/cwh_service.py, and
app/excess_inventory_service.py), never hardcoded constants.

Unlike every other parameter in the app, changing either threshold
multiplier does NOT retroactively recalculate any existing
InventoryThreshold/CwhStock row: the CFA multiplier is only read when a
new Previous Month Sales Report is processed (see
threshold_service.generate_thresholds_from_sales()); the CWH multiplier is
only read when a new Inventory Report is processed (see
cwh_service.evaluate_cwh_stock()) -- either saved change takes effect on
its next respective upload, not before, and never retroactively -- see
ui/inventory_settings_page.py for the exact message shown to the user
when they save.

The Excess Inventory Transfer Candidate multiplier, by contrast, IS read
live on every call to app.excess_inventory_service.get_excess_inventory()
-- there is no stored per-row "transfer candidate" snapshot to go stale,
so a saved change is reflected immediately, the next time that page
reloads (same immediacy as the Threshold Display Mode below, for the same
reason: neither one is baked into a stored InventoryReplenishment/
InventoryThreshold row).

The Threshold Display Mode is a pure display preference: it never touches
stored threshold values (raw_threshold/packed_threshold), only how
threshold_service.format_threshold_display() renders them -- changing it
takes effect immediately, the next time any page reloads.
"""

from loguru import logger

from database.connection import get_config_session
from database.models import InventoryParameter

THRESHOLD_MULTIPLIER = "threshold_multiplier"
DEFAULT_THRESHOLD_MULTIPLIER = "1.5"

# CWH Threshold = Total Previous Month Sales (all CFAs combined) x this
# multiplier -- see app/cwh_service.py. Previously hardcoded to 2 (Phase
# 2); now editable, per explicit instruction, following exactly the same
# "applies only to the next processing run, never retroactively" rule as
# THRESHOLD_MULTIPLIER above.
CWH_THRESHOLD_MULTIPLIER = "cwh_threshold_multiplier"
DEFAULT_CWH_THRESHOLD_MULTIPLIER = "2.0"

# Excess Inventory's own Transfer Candidate flag: Effective Available
# Stock >= this multiplier x Previous Month Sales -- see
# app/excess_inventory_service.py. Read live on every call, not just at
# upload time (see module docstring), so no "applies only to future
# analyses" caveat is needed for this one.
EXCESS_TRANSFER_CANDIDATE_MULTIPLIER = "excess_transfer_candidate_multiplier"
DEFAULT_EXCESS_TRANSFER_CANDIDATE_MULTIPLIER = "2.0"

THRESHOLD_DISPLAY_MODE = "threshold_display_mode"
DISPLAY_MODE_RAW = "raw"
DISPLAY_MODE_PACKS = "packs"
DEFAULT_THRESHOLD_DISPLAY_MODE = DISPLAY_MODE_RAW

# The Supabase `module_configurations.module_key` this module syncs under
# (app/sync_service.py is generic and knows nothing about this value) --
# reuses the exact same cloud table Path Validator Parameters already
# uses (see app/rule_parameters.py), just a different module_key.
MODULE_KEY = "inventory_parameters"


def ensure_defaults() -> None:
    """Insert each parameter's default value if it doesn't already exist
    -- never overwrites a value the user has already customized. Call
    once at application startup, same as the other *_service.ensure_defaults()."""
    session = get_config_session()
    try:
        for parameter_name, default_value in (
            (THRESHOLD_MULTIPLIER, DEFAULT_THRESHOLD_MULTIPLIER),
            (CWH_THRESHOLD_MULTIPLIER, DEFAULT_CWH_THRESHOLD_MULTIPLIER),
            (EXCESS_TRANSFER_CANDIDATE_MULTIPLIER, DEFAULT_EXCESS_TRANSFER_CANDIDATE_MULTIPLIER),
            (THRESHOLD_DISPLAY_MODE, DEFAULT_THRESHOLD_DISPLAY_MODE),
        ):
            exists = session.query(InventoryParameter).filter_by(parameter_name=parameter_name).first()
            if not exists:
                session.add(InventoryParameter(parameter_name=parameter_name, parameter_value=default_value))
                logger.info(f"Initialized default Inventory parameter {parameter_name} = {default_value}")
        session.commit()
    finally:
        session.close()


def get_threshold_multiplier() -> float:
    """The current replenishment threshold multiplier -- read fresh from
    the database on every call (see threshold_service.py, which calls
    this at the moment a new sales report is processed, never caches
    it)."""
    session = get_config_session()
    try:
        row = session.query(InventoryParameter).filter_by(parameter_name=THRESHOLD_MULTIPLIER).first()
        value = row.parameter_value if row else DEFAULT_THRESHOLD_MULTIPLIER
    finally:
        session.close()
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(DEFAULT_THRESHOLD_MULTIPLIER)


def set_threshold_multiplier(value: str) -> None:
    """Create or update the multiplier's stored value. Does not touch any
    existing InventoryThreshold row -- see module docstring."""
    session = get_config_session()
    try:
        row = session.query(InventoryParameter).filter_by(parameter_name=THRESHOLD_MULTIPLIER).first()
        if row:
            row.parameter_value = value
        else:
            row = InventoryParameter(parameter_name=THRESHOLD_MULTIPLIER, parameter_value=value)
            session.add(row)
        session.commit()
    finally:
        session.close()
    logger.info(f"Saved Inventory parameter {THRESHOLD_MULTIPLIER} = {value}")


def get_cwh_threshold_multiplier() -> float:
    """The current CWH threshold multiplier -- read fresh from the
    database on every call (see app/cwh_service.py, which calls this at
    the moment a new Inventory Report is processed, never caches it)."""
    session = get_config_session()
    try:
        row = session.query(InventoryParameter).filter_by(parameter_name=CWH_THRESHOLD_MULTIPLIER).first()
        value = row.parameter_value if row else DEFAULT_CWH_THRESHOLD_MULTIPLIER
    finally:
        session.close()
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(DEFAULT_CWH_THRESHOLD_MULTIPLIER)


def set_cwh_threshold_multiplier(value: str) -> None:
    """Create or update the CWH multiplier's stored value. Does not touch
    any existing CwhStock row -- see module docstring."""
    session = get_config_session()
    try:
        row = session.query(InventoryParameter).filter_by(parameter_name=CWH_THRESHOLD_MULTIPLIER).first()
        if row:
            row.parameter_value = value
        else:
            row = InventoryParameter(parameter_name=CWH_THRESHOLD_MULTIPLIER, parameter_value=value)
            session.add(row)
        session.commit()
    finally:
        session.close()
    logger.info(f"Saved Inventory parameter {CWH_THRESHOLD_MULTIPLIER} = {value}")


def get_excess_transfer_candidate_multiplier() -> float:
    """The current Excess Inventory Transfer Candidate multiplier -- read
    fresh from the database on every call (see
    app/excess_inventory_service.get_excess_inventory(), which never
    caches it, so a saved change is reflected the next time that page
    reloads)."""
    session = get_config_session()
    try:
        row = session.query(InventoryParameter).filter_by(parameter_name=EXCESS_TRANSFER_CANDIDATE_MULTIPLIER).first()
        value = row.parameter_value if row else DEFAULT_EXCESS_TRANSFER_CANDIDATE_MULTIPLIER
    finally:
        session.close()
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(DEFAULT_EXCESS_TRANSFER_CANDIDATE_MULTIPLIER)


def set_excess_transfer_candidate_multiplier(value: str) -> None:
    """Create or update the Transfer Candidate multiplier's stored value.
    Takes effect immediately (see module docstring) -- there is no stored
    per-row snapshot to leave stale."""
    session = get_config_session()
    try:
        row = session.query(InventoryParameter).filter_by(parameter_name=EXCESS_TRANSFER_CANDIDATE_MULTIPLIER).first()
        if row:
            row.parameter_value = value
        else:
            row = InventoryParameter(parameter_name=EXCESS_TRANSFER_CANDIDATE_MULTIPLIER, parameter_value=value)
            session.add(row)
        session.commit()
    finally:
        session.close()
    logger.info(f"Saved Inventory parameter {EXCESS_TRANSFER_CANDIDATE_MULTIPLIER} = {value}")


def get_threshold_display_mode() -> str:
    """DISPLAY_MODE_RAW or DISPLAY_MODE_PACKS -- read fresh from the
    database on every call (see threshold_service.format_threshold_display(),
    which never caches it, so a saved change is reflected the next time any
    page reloads)."""
    session = get_config_session()
    try:
        row = session.query(InventoryParameter).filter_by(parameter_name=THRESHOLD_DISPLAY_MODE).first()
        value = row.parameter_value if row else DEFAULT_THRESHOLD_DISPLAY_MODE
    finally:
        session.close()
    return value if value in (DISPLAY_MODE_RAW, DISPLAY_MODE_PACKS) else DEFAULT_THRESHOLD_DISPLAY_MODE


def set_threshold_display_mode(value: str) -> None:
    """Create or update the display mode's stored value. Purely a display
    preference -- never touches any stored threshold value, see module
    docstring."""
    if value not in (DISPLAY_MODE_RAW, DISPLAY_MODE_PACKS):
        raise ValueError(f"Unknown threshold display mode {value!r}, expected {DISPLAY_MODE_RAW!r} or {DISPLAY_MODE_PACKS!r}")
    session = get_config_session()
    try:
        row = session.query(InventoryParameter).filter_by(parameter_name=THRESHOLD_DISPLAY_MODE).first()
        if row:
            row.parameter_value = value
        else:
            row = InventoryParameter(parameter_name=THRESHOLD_DISPLAY_MODE, parameter_value=value)
            session.add(row)
        session.commit()
    finally:
        session.close()
    logger.info(f"Saved Inventory parameter {THRESHOLD_DISPLAY_MODE} = {value}")


def get_full_configuration() -> dict:
    """Returns Inventory's entire cloud-synced configuration as one flat
    dict ({THRESHOLD_MULTIPLIER: value, CWH_THRESHOLD_MULTIPLIER: value,
    THRESHOLD_DISPLAY_MODE: value}), ready to hand to
    app.sync_service.push_config(). Flat, not nested by rule_name like
    app/rule_parameters.py's version -- Inventory has no grouped rule
    sections, just standalone parameters. Adding CWH_THRESHOLD_MULTIPLIER
    here is the entire cloud-sync wiring it needs -- it rides the exact
    same push (ui/inventory_settings_page.py's Save button) and pull
    (app/inventory_refresh.py's pull_and_apply_configuration, already part
    of the module-wide Refresh cycle) as the existing CFA multiplier, with
    no new sync code required."""
    return {
        THRESHOLD_MULTIPLIER: str(get_threshold_multiplier()),
        CWH_THRESHOLD_MULTIPLIER: str(get_cwh_threshold_multiplier()),
        EXCESS_TRANSFER_CANDIDATE_MULTIPLIER: str(get_excess_transfer_candidate_multiplier()),
        THRESHOLD_DISPLAY_MODE: get_threshold_display_mode(),
    }


def apply_full_configuration(config: dict) -> None:
    """Writes every parameter in `config` into the local cache -- called
    after a successful cloud push (to confirm the cache matches what was
    just saved) and after a successful Refresh pull (to apply newly
    downloaded values). An unrecognized key is ignored rather than
    raising -- forward-compatible with a newer cloud config containing a
    future parameter this app version doesn't know about yet."""
    if THRESHOLD_MULTIPLIER in config:
        set_threshold_multiplier(str(config[THRESHOLD_MULTIPLIER]))
    if CWH_THRESHOLD_MULTIPLIER in config:
        set_cwh_threshold_multiplier(str(config[CWH_THRESHOLD_MULTIPLIER]))
    if EXCESS_TRANSFER_CANDIDATE_MULTIPLIER in config:
        set_excess_transfer_candidate_multiplier(str(config[EXCESS_TRANSFER_CANDIDATE_MULTIPLIER]))
    if THRESHOLD_DISPLAY_MODE in config:
        set_threshold_display_mode(config[THRESHOLD_DISPLAY_MODE])


def pull_and_apply_configuration() -> bool:
    """Pulls the cloud Inventory Parameters config and applies it to the
    local cache -- the module-wide Refresh action's first operation (see
    app/inventory_refresh.py). Returns True if a config was live-pulled
    (every other pull_* function in this codebase treats that as the
    signal to re-render, not "did values actually differ"). Returns False
    on a failed pull or an empty/never-pushed config."""
    from app.sync_service import pull_config

    result = pull_config(MODULE_KEY)
    if not result.success or not result.config:
        return False
    apply_full_configuration(result.config)
    return True
