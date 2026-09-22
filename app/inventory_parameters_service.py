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

# The module_configurations.module_key these 4 parameters sync under
# (parameter sync project, Phase 3) -- matches module_registry's
# canonical "inventory_module" key, not this table's own name. Sender
# email/app password (app/inventory_email_settings_service.py) are
# deliberately excluded -- see that module's own docstring.
MODULE_KEY = "inventory_module"
_SYNCED_PARAMETER_NAMES = (
    THRESHOLD_MULTIPLIER,
    CWH_THRESHOLD_MULTIPLIER,
    EXCESS_TRANSFER_CANDIDATE_MULTIPLIER,
    THRESHOLD_DISPLAY_MODE,
)


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


# --- Cloud config sync (parameter sync project, Phase 3) ------------------

def get_full_configuration() -> dict:
    """The complete synced config for MODULE_KEY -- the 4 non-credential
    parameters only (see _SYNCED_PARAMETER_NAMES); sender email/app
    password never included, by construction, not by filtering."""
    return {
        THRESHOLD_MULTIPLIER: get_threshold_multiplier(),
        CWH_THRESHOLD_MULTIPLIER: get_cwh_threshold_multiplier(),
        EXCESS_TRANSFER_CANDIDATE_MULTIPLIER: get_excess_transfer_candidate_multiplier(),
        THRESHOLD_DISPLAY_MODE: get_threshold_display_mode(),
    }


def apply_full_configuration(config: dict) -> tuple[bool, str | None]:
    """Writes a pulled config blob back into local storage. Returns
    (True, None) on success, or (False, error_message) WITHOUT writing
    anything if threshold_display_mode is present but not a recognized
    value -- a malformed remote blob must never corrupt local state. A
    missing key is left untouched; an unrecognized extra key is ignored."""
    if THRESHOLD_DISPLAY_MODE in config and config[THRESHOLD_DISPLAY_MODE] not in (DISPLAY_MODE_RAW, DISPLAY_MODE_PACKS):
        return False, f"Remote threshold_display_mode was {config[THRESHOLD_DISPLAY_MODE]!r}, not a recognized value -- rejected, nothing applied."

    for name, setter in (
        (THRESHOLD_MULTIPLIER, set_threshold_multiplier),
        (CWH_THRESHOLD_MULTIPLIER, set_cwh_threshold_multiplier),
        (EXCESS_TRANSFER_CANDIDATE_MULTIPLIER, set_excess_transfer_candidate_multiplier),
    ):
        if name in config:
            try:
                setter(str(float(config[name])))
            except (TypeError, ValueError):
                return False, f"Remote {name} was not a valid number -- rejected, nothing applied."

    if THRESHOLD_DISPLAY_MODE in config:
        set_threshold_display_mode(config[THRESHOLD_DISPLAY_MODE])

    return True, None
