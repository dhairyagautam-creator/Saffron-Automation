"""Tests for each module's own get_full_configuration()/
apply_full_configuration() -- the per-module contract
app/parameter_sync_service.py's push_config()/try_push_and_apply() are
built on. No network involved here (these two functions are pure local
DB read/write); tests/test_parameter_sync_service.py covers the actual
push/pull layer with a fake Supabase client.

Also confirms, per module, that get_full_configuration() never produces
any of the forbidden credential/bookkeeping keys -- a second, per-module
check complementary to test_parameter_sync_service.py's generic
"push_config refuses every forbidden key" test: this one would fail even
before push_config ever got a chance to raise, if a module's own
config-building code started including a credential.
"""

import pytest

from app.parameter_sync_service import FORBIDDEN_KEYS
from tests.db_isolation import isolate_database


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch):
    isolate_database(monkeypatch)


def _assert_no_forbidden_keys(config: dict) -> None:
    """Recursively confirms no forbidden key appears at any nesting level
    -- rule_parameters/payment_parameters nest one level ({rule_name:
    {parameter_name: value}}), so a shallow key check alone isn't enough."""
    def walk(node):
        if isinstance(node, dict):
            found = FORBIDDEN_KEYS & node.keys()
            assert not found, f"Forbidden key(s) found in config: {found}"
            for value in node.values():
                walk(value)

    walk(config)


# --- app/rule_parameters.py (Path Validator, "employee_module") -----------

def test_rule_parameters_full_configuration_round_trips():
    import app.rule_parameters as rp

    rp.set_parameter("SAME_LOCATION", "same_place_radius_meters", "75")
    rp.set_parameter("HOSPITAL_SUPPRESSION", "radius_meters", "150")

    config = rp.get_full_configuration()
    assert config["SAME_LOCATION"]["same_place_radius_meters"] == "75"
    assert config["HOSPITAL_SUPPRESSION"]["radius_meters"] == "150"
    _assert_no_forbidden_keys(config)

    # Apply a different config, confirm it's written back and readable.
    ok, error = rp.apply_full_configuration({"SAME_LOCATION": {"same_place_radius_meters": "200"}})
    assert ok is True
    assert error is None
    assert rp.get_parameters("SAME_LOCATION")["same_place_radius_meters"] == "200"


def test_rule_parameters_dashboard_is_gone():
    import app.rule_parameters as rp

    assert "DASHBOARD" not in rp.DEFAULT_PARAMETERS
    config = rp.get_full_configuration()
    assert "DASHBOARD" not in config


def test_rule_parameters_apply_rejects_non_numeric_value_without_writing():
    import app.rule_parameters as rp

    rp.set_parameter("SAME_LOCATION", "same_place_radius_meters", "50")
    ok, error = rp.apply_full_configuration({"SAME_LOCATION": {"same_place_radius_meters": "not-a-number"}})
    assert ok is False
    assert error is not None
    # Nothing was corrupted -- the original value is untouched.
    assert rp.get_parameters("SAME_LOCATION")["same_place_radius_meters"] == "50"


# --- app/inventory_parameters_service.py ("inventory_module") -------------

def test_inventory_parameters_full_configuration_round_trips():
    import app.inventory_parameters_service as ips

    ips.set_threshold_multiplier("2.0")
    config = ips.get_full_configuration()
    assert config[ips.THRESHOLD_MULTIPLIER] == 2.0
    _assert_no_forbidden_keys(config)

    ok, error = ips.apply_full_configuration({ips.THRESHOLD_MULTIPLIER: 3.5})
    assert ok is True
    assert ips.get_threshold_multiplier() == 3.5


def test_inventory_parameters_apply_rejects_bad_display_mode():
    import app.inventory_parameters_service as ips

    ips.set_threshold_display_mode(ips.DISPLAY_MODE_PACKS)
    ok, error = ips.apply_full_configuration({ips.THRESHOLD_DISPLAY_MODE: "not_a_mode"})
    assert ok is False
    assert ips.get_threshold_display_mode() == ips.DISPLAY_MODE_PACKS  # untouched


# --- app/work_distribution_parameters_service.py + Manager Work
# Allocation, combined under the shared "work_distribution" blob --------

def test_work_distribution_full_configuration_combines_both_tables():
    import app.manager_work_allocation_parameters_service as mwaps
    import app.work_distribution_parameters_service as wdps

    wdps.set_bm_minimum_calls("140")
    mwaps.set_minimum_joint_working_days("5")

    config = wdps.get_full_configuration()
    assert config[wdps.BM_MINIMUM_CALLS] == 140.0
    assert config[mwaps.MINIMUM_JOINT_WORKING_DAYS] == 5.0
    _assert_no_forbidden_keys(config)


def test_work_distribution_apply_writes_both_tables():
    import app.manager_work_allocation_parameters_service as mwaps
    import app.work_distribution_parameters_service as wdps

    ok, error = wdps.apply_full_configuration({
        wdps.BM_MINIMUM_CALLS: 160,
        mwaps.MINIMUM_JOINT_WORKING_DAYS: 6,
    })
    assert ok is True
    assert wdps.get_bm_minimum_calls() == 160.0
    assert mwaps.get_minimum_joint_working_days() == 6.0


def test_rbm_flag_tiers_validated_on_apply_malformed_blob_rejected_without_corruption():
    """The explicit Phase 6 requirement: a malformed remote rbm_flag_tiers
    blob must be rejected without corrupting local state."""
    import app.manager_work_allocation_parameters_service as mwaps
    import app.work_distribution_parameters_service as wdps

    good_tiers = [{"min": 8, "max": 10, "missed": 1}, {"min": 11, "max": None, "missed": 2}]
    mwaps.set_rbm_flag_tiers(good_tiers)

    # Malformed: min > max on the first tier -- validate_rbm_flag_tiers
    # must reject this.
    bad_tiers = [{"min": 20, "max": 10, "missed": 1}]
    ok, error = wdps.apply_full_configuration({mwaps.RBM_FLAG_TIERS: bad_tiers})
    assert ok is False
    assert error is not None
    # Local tiers are untouched -- not partially overwritten, not corrupted.
    assert mwaps.get_rbm_flag_tiers() == good_tiers


def test_rbm_flag_tiers_non_list_rejected():
    import app.manager_work_allocation_parameters_service as mwaps
    import app.work_distribution_parameters_service as wdps

    good_tiers = [{"min": 8, "max": None, "missed": 1}]
    mwaps.set_rbm_flag_tiers(good_tiers)

    ok, error = wdps.apply_full_configuration({mwaps.RBM_FLAG_TIERS: "not-a-list"})
    assert ok is False
    assert mwaps.get_rbm_flag_tiers() == good_tiers


def test_work_distribution_kpi_still_applies_even_if_rbm_tiers_half_fails():
    """A malformed rbm_flag_tiers on the remote side must not block a
    genuinely valid KPI update from the same pull -- see
    work_distribution_parameters_service.apply_full_configuration's own
    docstring."""
    import app.manager_work_allocation_parameters_service as mwaps
    import app.work_distribution_parameters_service as wdps

    ok, error = wdps.apply_full_configuration({
        wdps.BM_TARGET_CALLS: 175,
        mwaps.RBM_FLAG_TIERS: "not-a-list",
    })
    assert ok is False  # overall result reflects the rejected half
    assert wdps.get_bm_target_calls() == 175.0  # but the valid half still applied


# --- app/payment_parameters_service.py ("payments_module") ----------------

def test_payment_parameters_full_configuration_round_trips():
    import app.payment_parameters_service as pps

    pps.set_parameter("HISTORICAL_RISK_SCORING", "green_max_days", "25")
    config = pps.get_full_configuration()
    assert config["HISTORICAL_RISK_SCORING"]["green_max_days"] == "25"
    _assert_no_forbidden_keys(config)

    ok, error = pps.apply_full_configuration({"COLLECTIONS_AGEING": {"yellow_max_days": "7"}})
    assert ok is True
    assert pps.get_parameters("COLLECTIONS_AGEING")["yellow_max_days"] == "7"


def test_payment_parameters_apply_rejects_non_numeric_value():
    import app.payment_parameters_service as pps

    pps.set_parameter("COLLECTIONS_AGEING", "yellow_max_days", "5")
    ok, error = pps.apply_full_configuration({"COLLECTIONS_AGEING": {"yellow_max_days": "soon"}})
    assert ok is False
    assert pps.get_parameters("COLLECTIONS_AGEING")["yellow_max_days"] == "5"
