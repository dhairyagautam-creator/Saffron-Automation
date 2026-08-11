"""Editable ageing/risk-scoring thresholds for the Payment Analytics
module -- Historical Payment Analytics' risk categories
(app/payment_analytics_service.classify_risk()) and Collections Action
Center's overdue-ageing buckets
(app/collections_service.compute_days_and_status()). Both read from here
instead of a hardcoded constant, so a value changed on the Parameters page
takes effect the next time that data is read -- no restart, no re-upload,
since neither engine caches a threshold beyond the single call that reads
it.

Deliberately separate from app/rule_parameters.py (Path Validator's
Developer/User Mode-scoped parameter store) -- see
database.models.PaymentAnalyticsParameter's docstring for why.
"""

from loguru import logger

from database.connection import get_config_session
from database.models import PaymentAnalyticsParameter

HISTORICAL_RISK_SCORING = "HISTORICAL_RISK_SCORING"
COLLECTIONS_AGEING = "COLLECTIONS_AGEING"

# The Supabase `module_configurations.module_key` this module syncs under
# (app/sync_service.py is generic and knows nothing about this value) --
# reuses the exact same cloud table Path Validator/Inventory Parameters
# already use, just a different module_key.
MODULE_KEY = "payment_analytics_parameters"

# Every rule section this module manages -- unlike app/rule_parameters.py,
# there is no Developer-Mode-only section to exclude (Payment Analytics
# has no Developer Mode concept of its own), so every rule syncs.
CLOUD_SYNCED_RULE_NAMES = (HISTORICAL_RISK_SCORING, COLLECTIONS_AGEING)

DEFAULT_PARAMETERS = {
    # Green/Yellow/Orange are each "average payment days below this value";
    # Red is everything at or above orange_max_days. See
    # app/payment_analytics_service.classify_risk().
    HISTORICAL_RISK_SCORING: {
        "green_max_days": "30",
        "yellow_max_days": "45",
        "orange_max_days": "60",
    },
    # Green is NEVER a threshold here -- it's "Today <= Due Date", which
    # never changes (see compute_days_and_status()). Yellow/Orange are
    # each "days overdue at or below this value"; Red is anything greater
    # than orange_max_days.
    COLLECTIONS_AGEING: {
        "yellow_max_days": "5",
        "orange_max_days": "10",
    },
}


def ensure_defaults() -> None:
    """Insert each parameter's default value if it doesn't already exist
    -- never overwrites a value the user has already customized. Call
    once at application startup, same as app.rule_parameters.ensure_defaults()."""
    session = get_config_session()
    try:
        for rule_name, params in DEFAULT_PARAMETERS.items():
            for parameter_name, default_value in params.items():
                exists = (
                    session.query(PaymentAnalyticsParameter)
                    .filter_by(rule_name=rule_name, parameter_name=parameter_name)
                    .first()
                )
                if not exists:
                    session.add(
                        PaymentAnalyticsParameter(
                            rule_name=rule_name, parameter_name=parameter_name, parameter_value=default_value
                        )
                    )
                    logger.info(
                        f"Initialized default Payment Analytics parameter {rule_name}.{parameter_name} = {default_value}"
                    )
        session.commit()
    finally:
        session.close()


def get_parameters(rule_name: str) -> dict:
    """{parameter_name: parameter_value} for the given rule -- always
    includes every default key, even if a row hasn't been written yet
    (e.g. ensure_defaults() hasn't run in this process)."""
    session = get_config_session()
    try:
        rows = session.query(PaymentAnalyticsParameter).filter_by(rule_name=rule_name).all()
        stored = {row.parameter_name: row.parameter_value for row in rows}
    finally:
        session.close()
    return {**DEFAULT_PARAMETERS.get(rule_name, {}), **stored}


def set_parameter(rule_name: str, parameter_name: str, value: str) -> None:
    """Create or update a single parameter's value."""
    session = get_config_session()
    try:
        row = (
            session.query(PaymentAnalyticsParameter)
            .filter_by(rule_name=rule_name, parameter_name=parameter_name)
            .first()
        )
        if row:
            row.parameter_value = value
        else:
            row = PaymentAnalyticsParameter(rule_name=rule_name, parameter_name=parameter_name, parameter_value=value)
            session.add(row)
        session.commit()
    finally:
        session.close()
    logger.info(f"Saved Payment Analytics parameter {rule_name}.{parameter_name} = {value}")


def get_full_configuration() -> dict:
    """Returns Payment Analytics' entire cloud-synced configuration as one
    nested dict ({rule_name: {parameter_name: value}}) covering every rule
    in CLOUD_SYNCED_RULE_NAMES, ready to hand to
    app.sync_service.push_config() -- mirrors app/rule_parameters.py's
    get_full_configuration() exactly, minus the environment dimension
    (Payment Analytics has none)."""
    return {rule_name: get_parameters(rule_name) for rule_name in CLOUD_SYNCED_RULE_NAMES}


def apply_full_configuration(config: dict) -> None:
    """Writes every rule/parameter in `config` into the local cache --
    called after a successful cloud push (to confirm the cache matches
    what was just saved) and after a successful Refresh pull (to apply
    newly downloaded values). A rule_name/parameter this function doesn't
    recognize is written anyway (forward-compatible with a newer cloud
    config containing a future parameter an older app version doesn't
    know about by name)."""
    for rule_name, parameters in config.items():
        for parameter_name, value in parameters.items():
            set_parameter(rule_name, parameter_name, str(value))


def pull_and_apply_configuration() -> bool:
    """Pulls the cloud Payment Analytics Parameters config and applies it
    to the local cache -- the module-wide Refresh action's first
    operation (see app/payment_refresh.py). Returns True if a config was
    live-pulled (every other pull_* function in this codebase treats that
    as the signal to re-render). Returns False on a failed pull or an
    empty/never-pushed config."""
    from app.sync_service import pull_config

    result = pull_config(MODULE_KEY)
    if not result.success or not result.config:
        return False
    apply_full_configuration(result.config)
    return True


def get_historical_risk_thresholds() -> tuple[float, float, float]:
    """(green_max_days, yellow_max_days, orange_max_days) -- the
    Green/Yellow/Orange upper bounds used by
    app/payment_analytics_service.classify_risk(); Red is everything at
    or above orange_max_days."""
    values = get_parameters(HISTORICAL_RISK_SCORING)
    return (
        float(values["green_max_days"]),
        float(values["yellow_max_days"]),
        float(values["orange_max_days"]),
    )


def get_collections_ageing_thresholds() -> tuple[int, int]:
    """(yellow_max_days, orange_max_days) -- the Yellow/Orange maximum
    Days Over Due used by
    app/collections_service.compute_days_and_status(); Red is anything
    greater than orange_max_days. Green is never a threshold here -- see
    module docstring."""
    values = get_parameters(COLLECTIONS_AGEING)
    return int(float(values["yellow_max_days"])), int(float(values["orange_max_days"]))
