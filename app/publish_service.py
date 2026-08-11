"""Publish to User Mode — promotes the Developer Mode environment into
production (User Mode), the equivalent of shipping a tested dev build.

Copies developer -> user for everything that's environment-scoped: the
app_settings credentials/config, all rule parameters, and all feature
flags. Global fields (setup_completed, the dev-mode password) and shared
data (hierarchy, imports, findings) are untouched. After this, User Mode
immediately reflects the promoted feature set the next time it's used.
"""

from datetime import datetime

from loguru import logger

from app.mode_state import DEVELOPER_ENVIRONMENT, USER_ENVIRONMENT
from database.connection import get_config_session
from database.models import AppSettings, FeatureFlag, RuleParameter

# Per-environment credential/config fields copied on publish. Deliberately
# excludes the global fields (setup_completed, dev_password_*) which are
# never per-environment.
_PUBLISHED_SETTINGS_FIELDS = (
    "sender_gmail_address",
    "gmail_app_password",
    "automatic_email_enabled",
    "master_email_address",
    "geoapify_api_key",
)


def publish_developer_to_user() -> dict:
    """Overwrite the User (production) environment with the Developer
    environment's settings, rule parameters, and feature flags. Returns a
    small summary of what was promoted."""
    session = get_config_session()
    try:
        # --- app_settings credentials/config ---
        dev_settings = session.query(AppSettings).filter_by(environment=DEVELOPER_ENVIRONMENT).first()
        user_settings = session.query(AppSettings).filter_by(environment=USER_ENVIRONMENT).first()
        if dev_settings is not None:
            if user_settings is None:
                user_settings = AppSettings(environment=USER_ENVIRONMENT)
                session.add(user_settings)
            for field in _PUBLISHED_SETTINGS_FIELDS:
                setattr(user_settings, field, getattr(dev_settings, field))
            user_settings.updated_at = datetime.now()

        # --- rule parameters ---
        params_promoted = 0
        dev_params = session.query(RuleParameter).filter_by(environment=DEVELOPER_ENVIRONMENT).all()
        for dev_param in dev_params:
            user_param = (
                session.query(RuleParameter)
                .filter_by(
                    environment=USER_ENVIRONMENT,
                    rule_name=dev_param.rule_name,
                    parameter_name=dev_param.parameter_name,
                )
                .first()
            )
            if user_param is None:
                user_param = RuleParameter(
                    environment=USER_ENVIRONMENT,
                    rule_name=dev_param.rule_name,
                    parameter_name=dev_param.parameter_name,
                    parameter_value=dev_param.parameter_value,
                )
                session.add(user_param)
            else:
                user_param.parameter_value = dev_param.parameter_value
            params_promoted += 1

        # --- feature flags ---
        flags_promoted = 0
        dev_flags = session.query(FeatureFlag).filter_by(environment=DEVELOPER_ENVIRONMENT).all()
        for dev_flag in dev_flags:
            user_flag = (
                session.query(FeatureFlag)
                .filter_by(environment=USER_ENVIRONMENT, flag_name=dev_flag.flag_name)
                .first()
            )
            if user_flag is None:
                user_flag = FeatureFlag(environment=USER_ENVIRONMENT, flag_name=dev_flag.flag_name)
                session.add(user_flag)
            user_flag.enabled = dev_flag.enabled
            flags_promoted += 1

        session.commit()
    finally:
        session.close()

    summary = {"parameters_promoted": params_promoted, "flags_promoted": flags_promoted}
    logger.info(
        f"Published Developer -> User: settings copied, {params_promoted} parameter(s) and "
        f"{flags_promoted} feature flag(s) promoted to production"
    )
    return summary
