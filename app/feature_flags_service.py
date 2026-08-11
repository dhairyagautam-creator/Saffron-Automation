"""Feature flags — the core of the Developer Mode system.

Each flag is stored per environment ('user' vs 'developer', see
app/mode_state.py). A flag is ON in 'developer' and OFF in 'user' until the
developer promotes it via Publish to User Mode (app/publish_service.py).
Any experimental feature is gated on `is_feature_enabled(...)` for the
current mode, so User Mode simply never runs it.

To add a future experimental feature: add its default here (off for user,
on for developer) and wrap the feature's code/UI in
`if is_feature_enabled("your_flag"): ...`. No other plumbing required.
"""

from loguru import logger

from app.mode_state import DEVELOPER_ENVIRONMENT, USER_ENVIRONMENT, current_environment
from database.connection import get_config_session
from database.models import FeatureFlag

# flag_name -> {environment: default_enabled}.
#
# Hospital Suppression was originally Developer-Mode-only (off in user)
# while it was experimental. As of v1.3 it is a standard, always-on stage
# of the Validator notification pipeline for everyone, so its user
# (production) default is now ON. Existing installs that were seeded with
# the old off-in-user default are promoted by
# database/migrations.ensure_hospital_suppression_enabled_in_user_mode()
# (ensure_flag_defaults never overwrites an existing row, so the default
# change alone only covers brand-new installs). It stays a developer flag
# too so Developer Mode can still toggle it off for A/B testing, but that
# never affects production, where it is forced on.
DEFAULT_FLAGS = {
    "hospital_suppression": {USER_ENVIRONMENT: True, DEVELOPER_ENVIRONMENT: True},
}


def ensure_flag_defaults() -> None:
    """Insert any missing (environment, flag) rows at their default. Never
    overwrites an existing value — call once at startup, like
    app/rule_parameters.ensure_defaults."""
    session = get_config_session()
    try:
        for flag_name, per_env in DEFAULT_FLAGS.items():
            for environment, default_enabled in per_env.items():
                exists = (
                    session.query(FeatureFlag)
                    .filter_by(environment=environment, flag_name=flag_name)
                    .first()
                )
                if not exists:
                    session.add(
                        FeatureFlag(
                            environment=environment,
                            flag_name=flag_name,
                            enabled=1 if default_enabled else 0,
                        )
                    )
                    logger.info(
                        f"Initialized feature flag {environment}.{flag_name} = {default_enabled}"
                    )
        session.commit()
    finally:
        session.close()


def is_feature_enabled(flag_name: str, environment: str | None = None) -> bool:
    """Whether `flag_name` is enabled in the given environment, defaulting to
    the current mode's environment. Unknown/absent flags are treated as
    disabled (safe default — an ungated feature never leaks into User Mode)."""
    environment = environment or current_environment()
    session = get_config_session()
    try:
        row = session.query(FeatureFlag).filter_by(environment=environment, flag_name=flag_name).first()
        return bool(row.enabled) if row else False
    finally:
        session.close()


def get_flags(environment: str) -> dict:
    """Return {flag_name: bool} for the given environment."""
    session = get_config_session()
    try:
        rows = session.query(FeatureFlag).filter_by(environment=environment).all()
        return {row.flag_name: bool(row.enabled) for row in rows}
    finally:
        session.close()


def set_feature_enabled(flag_name: str, enabled: bool) -> None:
    """Toggle a flag in the DEVELOPER environment only — user-environment
    flags are never edited directly, they change only via Publish to User
    Mode. This is what the Developer page's toggles call."""
    session = get_config_session()
    try:
        row = (
            session.query(FeatureFlag)
            .filter_by(environment=DEVELOPER_ENVIRONMENT, flag_name=flag_name)
            .first()
        )
        if row is None:
            row = FeatureFlag(environment=DEVELOPER_ENVIRONMENT, flag_name=flag_name)
            session.add(row)
        row.enabled = 1 if enabled else 0
        session.commit()
    finally:
        session.close()

    logger.info(f"Feature flag developer.{flag_name} set to {enabled}")
