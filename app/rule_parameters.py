"""Reads and writes rule parameters stored in the `rule_parameters` table.

Parameters live in the database (not in code) so a user can change a rule's
behavior from the Parameters page without touching the codebase.
"""

from loguru import logger

from database.connection import get_config_session
from database.models import RuleParameter

# The module_configurations.module_key every rule_name here syncs under
# (parameter sync project, Phase 3) -- matches module_registry's
# canonical "employee_module" key (Path Validator), not this table's own
# name. HOSPITAL_SUPPRESSION is now included (the Developer Mode
# reasoning that used to exclude it is itself dead -- see
# database/migrations.py's drop_developer_mode_schema()) and now also has
# its own section on the Parameters page UI (see
# ui/parameters_page.py's RULE_SECTIONS). DASHBOARD is gone entirely
# (dead code, removed in Phase 1), so there is nothing to exclude for it
# anymore.
MODULE_KEY = "employee_module"

DEFAULT_PARAMETERS = {
    "SAME_LOCATION": {
        "same_place_radius_meters": "100",
        "concentration_threshold_percent": "30",
        "minimum_valid_gps_visits": "7",
    },
    # Read by rules/hours_worked.py. minimum_hours_threshold is the shortest
    # earliest->latest visit span (in hours) a day may have before it's
    # flagged -- editable from the Parameters page like every other rule.
    "HOURS_WORKED": {
        "minimum_hours_threshold": "6",
    },
    # Read by app/hospital_service.py / app/notification_service.py instead
    # of a hardcoded constant, so it's editable from the Parameters page
    # like every other rule threshold.
    "HOSPITAL_SUPPRESSION": {
        "radius_meters": "100",
    },
}


def ensure_defaults() -> None:
    """Insert each rule's default parameters if they don't already exist, so a
    newly added parameter shows up at its default without overwriting any
    value the user has already customized."""
    session = get_config_session()
    try:
        for rule_name, params in DEFAULT_PARAMETERS.items():
            for parameter_name, default_value in params.items():
                exists = (
                    session.query(RuleParameter)
                    .filter_by(rule_name=rule_name, parameter_name=parameter_name)
                    .first()
                )
                if not exists:
                    session.add(
                        RuleParameter(
                            rule_name=rule_name,
                            parameter_name=parameter_name,
                            parameter_value=default_value,
                        )
                    )
                    logger.info(
                        f"Initialized default parameter {rule_name}.{parameter_name} = {default_value}"
                    )
        session.commit()
    finally:
        session.close()


def get_parameters(rule_name: str) -> dict:
    """Return {parameter_name: parameter_value} for the given rule."""
    session = get_config_session()
    try:
        rows = session.query(RuleParameter).filter_by(rule_name=rule_name).all()
        return {row.parameter_name: row.parameter_value for row in rows}
    finally:
        session.close()


def set_parameter(rule_name: str, parameter_name: str, value: str) -> None:
    """Create or update a single parameter's value."""
    session = get_config_session()
    try:
        row = (
            session.query(RuleParameter)
            .filter_by(rule_name=rule_name, parameter_name=parameter_name)
            .first()
        )
        if row:
            row.parameter_value = value
        else:
            row = RuleParameter(
                rule_name=rule_name, parameter_name=parameter_name, parameter_value=value
            )
            session.add(row)
        session.commit()
    finally:
        session.close()

    logger.info(f"Saved parameter {rule_name}.{parameter_name} = {value}")


# --- Cloud config sync (parameter sync project, Phase 3) ------------------

def get_full_configuration() -> dict:
    """{rule_name: {parameter_name: value}} for every rule this module
    manages -- the exact shape of DEFAULT_PARAMETERS, values as the raw
    strings RuleParameter itself stores (never re-typed to float/int),
    for an exact round-trip. Any rule/parameter missing locally falls
    back to its coded default, same as get_parameters()'s own callers
    already expect."""
    config: dict = {}
    for rule_name, defaults in DEFAULT_PARAMETERS.items():
        saved = get_parameters(rule_name)
        config[rule_name] = {name: saved.get(name, default) for name, default in defaults.items()}
    return config


def apply_full_configuration(config: dict) -> tuple[bool, str | None]:
    """Writes a pulled config blob back into local storage. Returns
    (True, None) on success, or (False, error_message) WITHOUT writing
    anything if any known numeric parameter's remote value doesn't parse
    as a number -- a malformed remote blob must never corrupt local
    state. An unrecognized rule_name or parameter_name in `config` is
    ignored (forward-compatible); a rule/parameter missing from `config`
    is left untouched (a partial blob never blanks an existing local
    value)."""
    to_write = []
    for rule_name, defaults in DEFAULT_PARAMETERS.items():
        remote_params = config.get(rule_name)
        if not isinstance(remote_params, dict):
            continue
        for parameter_name in defaults:
            if parameter_name not in remote_params:
                continue
            value = remote_params[parameter_name]
            try:
                float(value)
            except (TypeError, ValueError):
                return False, f"Remote {rule_name}.{parameter_name} was {value!r}, not a number -- rejected, nothing applied."
            to_write.append((rule_name, parameter_name, str(value)))

    for rule_name, parameter_name, value in to_write:
        set_parameter(rule_name, parameter_name, value)

    return True, None
