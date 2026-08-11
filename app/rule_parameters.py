"""Reads and writes rule parameters stored in the `rule_parameters` table.

Parameters live in the database (not in code) so a user can change a rule's
behavior from the Parameters page without touching the codebase.

Scoped by environment (User Mode vs Developer Mode — see app/mode_state.py):
every read/write defaults to the CURRENT mode's environment, so tuning a
threshold in Developer Mode never affects production until published.

Version 2.0, Milestone 9: get_full_configuration()/apply_full_configuration()
below are the bridge to app/sync_service.py's cloud sync -- they always
operate on the User Mode environment specifically, regardless of which mode
the caller happens to be in, and only cover CLOUD_SYNCED_RULE_NAMES.
Developer Mode's parameters (e.g. Hospital Suppression) are deliberately
excluded and never leave this machine: Developer Mode is explicitly a
per-machine, never-shared concept (see app/mode_state.py's docstring), so
syncing it to a shared cloud config would leak one person's local
experimental tuning into what every other laptop pulls down.
"""

from loguru import logger

from app.mode_state import DEVELOPER_ENVIRONMENT, USER_ENVIRONMENT, current_environment
from database.connection import get_config_session
from database.models import RuleParameter

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
    # Read by app/dashboard_service.py and ui/analytics_dashboard_page.py
    # instead of hardcoded module constants -- every dashboard threshold,
    # color, sort mode, and bucket boundary is editable from the
    # Parameters page's "Dashboard Parameters" section. Colors are stored
    # as hex strings (RuleParameter.parameter_value is a plain String
    # column, no schema change needed); cluster_bucket_edges is a
    # comma-separated ascending list of boundary values (8 numbers -> 7
    # buckets).
    "DASHBOARD": {
        "compliance_high_threshold": "90",
        "compliance_mid_threshold": "75",
        "map_tier_low_max": "2",
        "map_tier_moderate_max": "5",
        # Shown for a state with zero employees analysed (no branches/staff
        # there at all) -- kept distinct from map_color_no_findings so the
        # map never implies "we checked and found nothing" for a state
        # Saffron simply doesn't operate in.
        "map_color_no_presence": "#DDE1E6",
        "map_color_no_findings": "#1E9E5A",
        "map_color_low": "#E0A200",
        "map_color_moderate": "#F2811A",
        "map_color_high": "#D64545",
        "division_sort_mode": "employees_flagged",
        "cluster_bucket_edges": "30,40,50,60,70,80,90,100",
    },
}

# The Supabase `module_configurations.module_key` this module syncs under
# (app/sync_service.py is generic and knows nothing about this value).
MODULE_KEY = "path_validator_parameters"

# Rule sections included in cloud sync. HOSPITAL_SUPPRESSION is
# deliberately excluded -- see the module docstring above.
CLOUD_SYNCED_RULE_NAMES = ("SAME_LOCATION", "HOURS_WORKED", "DASHBOARD")


def ensure_defaults() -> None:
    """Insert each rule's default parameters if they don't already exist —
    for BOTH the 'user' and 'developer' environments, so a newly added
    parameter shows up (at its default) in each mode without overwriting any
    value the user has already customized."""
    session = get_config_session()
    try:
        for environment in (USER_ENVIRONMENT, DEVELOPER_ENVIRONMENT):
            for rule_name, params in DEFAULT_PARAMETERS.items():
                for parameter_name, default_value in params.items():
                    exists = (
                        session.query(RuleParameter)
                        .filter_by(environment=environment, rule_name=rule_name, parameter_name=parameter_name)
                        .first()
                    )
                    if not exists:
                        session.add(
                            RuleParameter(
                                environment=environment,
                                rule_name=rule_name,
                                parameter_name=parameter_name,
                                parameter_value=default_value,
                            )
                        )
                        logger.info(
                            f"Initialized default parameter {environment}.{rule_name}.{parameter_name} "
                            f"= {default_value}"
                        )
        session.commit()
    finally:
        session.close()


def get_parameters(rule_name: str, environment: str | None = None) -> dict:
    """Return {parameter_name: parameter_value} for the given rule in the
    given environment, defaulting to the current mode's environment."""
    environment = environment or current_environment()
    session = get_config_session()
    try:
        rows = session.query(RuleParameter).filter_by(environment=environment, rule_name=rule_name).all()
        return {row.parameter_name: row.parameter_value for row in rows}
    finally:
        session.close()


def set_parameter(rule_name: str, parameter_name: str, value: str, environment: str | None = None) -> None:
    """Create or update a single parameter's value in the given environment
    (defaults to the current mode's environment)."""
    environment = environment or current_environment()
    session = get_config_session()
    try:
        row = (
            session.query(RuleParameter)
            .filter_by(environment=environment, rule_name=rule_name, parameter_name=parameter_name)
            .first()
        )
        if row:
            row.parameter_value = value
        else:
            row = RuleParameter(
                environment=environment, rule_name=rule_name, parameter_name=parameter_name, parameter_value=value
            )
            session.add(row)
        session.commit()
    finally:
        session.close()

    logger.info(f"Saved parameter {environment}.{rule_name}.{parameter_name} = {value}")


def get_full_configuration() -> dict:
    """Returns the entire cloud-synced Path Validator configuration --
    every rule in CLOUD_SYNCED_RULE_NAMES, always the User Mode
    (environment="user") values -- as one nested dict ({rule_name:
    {parameter_name: value}}), ready to hand to
    app.sync_service.push_config(). Whatever parameters exist under each
    rule_name are included automatically, so a future parameter added to
    DEFAULT_PARAMETERS is picked up with no change here."""
    return {
        rule_name: get_parameters(rule_name, environment=USER_ENVIRONMENT)
        for rule_name in CLOUD_SYNCED_RULE_NAMES
    }


def pull_and_apply_configuration() -> bool:
    """Pulls the cloud Path Validator Parameters config and applies it to
    the local User Mode cache -- the same pull+apply combo
    ui/parameters_page.py's Refresh used to perform on its own, now the
    Parameters entry in the module-wide Refresh action's operation list
    (see app/path_validator_refresh.py). Returns True if a config was
    live-pulled (even if applying it happened to change nothing locally --
    every other pull_* function in this codebase treats "the pull
    succeeded" as the signal to re-render, not "did values actually
    differ"). Returns False on a failed pull or an empty/never-pushed
    config."""
    from app.sync_service import pull_config

    result = pull_config(MODULE_KEY)
    if not result.success or not result.config:
        return False
    apply_full_configuration(result.config)
    return True


def apply_full_configuration(config: dict) -> None:
    """Writes every rule/parameter in `config` into the local User Mode
    cache -- called after a successful cloud push (to confirm the cache
    matches what was just saved) and after a successful Refresh pull (to
    apply newly downloaded values). A rule_name/parameter this function
    doesn't recognize is written anyway (forward compatible with a newer
    cloud config containing a future parameter an older app version
    doesn't know about by name -- it still gets cached and will simply be
    unused until the UI catches up)."""
    parameter_count = 0
    for rule_name, parameters in config.items():
        for parameter_name, value in parameters.items():
            set_parameter(rule_name, parameter_name, str(value), environment=USER_ENVIRONMENT)
            parameter_count += 1
    logger.info(
        f"Local cache updated from cloud configuration: {parameter_count} parameter(s) "
        f"across {len(config)} rule section(s)"
    )
