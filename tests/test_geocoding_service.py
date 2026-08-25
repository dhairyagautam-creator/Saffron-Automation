"""Tests for the reverse-geocode distance gate (2026-08-25 investigation
fix): Geoapify's REVERSE endpoint, for real Path Validator coordinates in
sparse-coverage areas, was found to match a named POI/business up to 218m
from the actual queried coordinate and present that business's name as
"the address" with no distance check. `_geoapify_reverse` now only trusts
that name within REVERSE_MATCH_MAX_DISTANCE_METERS; beyond it, it falls
back to `_coarse_address`'s plainer street/city/state components.

No network calls -- `_get_json` is monkeypatched with real response shapes
captured during that investigation (Sadre Alam/Azamgarh, 218m mismatch;
Sita Ram/Azamgarh, 41m genuine match).
"""

import app.geocoding_service as geocoding_service
from app.geocoding_service import (
    REVERSE_MATCH_MAX_DISTANCE_METERS,
    _coarse_address,
    _feature_distance_meters,
    _geoapify_reverse,
)


def _feature(lon, lat, properties):
    return {"type": "Feature", "properties": properties, "geometry": {"type": "Point", "coordinates": [lon, lat]}}


# --- _feature_distance_meters -----------------------------------------------

def test_distance_zero_when_feature_is_at_the_exact_query_point():
    feature = _feature(83.186128, 26.0712309, {})
    assert _feature_distance_meters(26.0712309, 83.186128, feature) < 0.01


def test_distance_matches_real_sadre_alam_mismatch():
    """Real investigation data: query (26.0712309, 83.186128), Geoapify
    matched (83.1863243, 26.0692776) -- measured ~218m."""
    feature = _feature(83.1863243, 26.0692776, {})
    distance = _feature_distance_meters(26.0712309, 83.186128, feature)
    assert 200 < distance < 235


def test_distance_none_when_geometry_missing():
    assert _feature_distance_meters(26.07, 83.18, {"properties": {}}) is None
    assert _feature_distance_meters(26.07, 83.18, {"geometry": {}}) is None
    assert _feature_distance_meters(26.07, 83.18, {"geometry": {"coordinates": [83.18]}}) is None


# --- _coarse_address ---------------------------------------------------------

def test_coarse_address_builds_street_city_postcode_state_country():
    props = {"street": "NH128C", "city": "Azamgarh", "postcode": "276001", "state": "Uttar Pradesh", "country": "India"}
    assert _coarse_address(props) == "NH128C, Azamgarh - 276001, Uttar Pradesh, India"


def test_coarse_address_falls_back_to_county_when_city_missing():
    props = {"county": "Bhilwara Tehsil", "state": "Rajasthan"}
    result = _coarse_address(props)
    assert "Bhilwara Tehsil" in result and "Rajasthan" in result


def test_coarse_address_none_when_no_usable_components():
    assert _coarse_address({}) is None
    assert _coarse_address({"formatted": "Some Business Name"}) is None  # never falls back to `formatted` itself


def test_coarse_address_never_includes_a_name_field():
    """The whole point: even if `name`/`formatted` are present in props,
    _coarse_address must never surface them -- only street/city/state/
    country-level components."""
    props = {"name": "Pankha Ghar", "formatted": "Pankha Ghar, NH128C, Azamgarh", "street": "NH128C", "city": "Azamgarh"}
    result = _coarse_address(props)
    assert "Pankha Ghar" not in result


# --- _geoapify_reverse (distance-gated) --------------------------------------

def test_close_match_keeps_the_named_business(monkeypatch):
    """Real investigation data: Sita Ram, 41m match -- well within
    REVERSE_MATCH_MAX_DISTANCE_METERS -- must keep the specific name."""
    lat, lon = 26.0775122, 83.1860139
    feature = _feature(
        83.186404, 26.0773898,
        {
            "formatted": "Anand Mulispecilty Hospital, NH128C, Azamgarh - 276001, UP, India",
            "street": "NH128C", "city": "Azamgarh", "postcode": "276001", "state": "Uttar Pradesh",
        },
    )
    monkeypatch.setattr(geocoding_service, "_get_json", lambda url: {"features": [feature]})

    result = _geoapify_reverse(lat, lon, "fake-key")
    assert result == "Anand Mulispecilty Hospital, NH128C, Azamgarh - 276001, UP, India"


def test_far_match_drops_the_business_name_for_coarse_address(monkeypatch):
    """Real investigation data: Sadre Alam, 218m mismatch -- 'Pankha Ghar'
    must NOT be shown; the coarse street/city/state components should be
    used instead."""
    lat, lon = 26.0712309, 83.186128
    feature = _feature(
        83.1863243, 26.0692776,
        {
            "formatted": "Pankha Ghar, NH128C, Azamgarh - 276001, UP, India",
            "street": "NH128C", "city": "Azamgarh", "postcode": "276001", "state": "Uttar Pradesh",
        },
    )
    monkeypatch.setattr(geocoding_service, "_get_json", lambda url: {"features": [feature]})

    result = _geoapify_reverse(lat, lon, "fake-key")
    assert result is not None
    assert "Pankha Ghar" not in result
    assert "NH128C" in result and "Azamgarh" in result


def test_far_match_with_no_coarse_components_falls_back_to_formatted(monkeypatch):
    """If the distant match ALSO has no usable street/city/state fields,
    there's nothing coarser to offer -- fall back to `formatted` rather
    than returning nothing (matches the module's own "None only if the
    provider genuinely returns nothing" contract)."""
    lat, lon = 26.0712309, 83.186128
    feature = _feature(83.1863243, 26.0692776, {"formatted": "Pankha Ghar, Azamgarh"})
    monkeypatch.setattr(geocoding_service, "_get_json", lambda url: {"features": [feature]})

    result = _geoapify_reverse(lat, lon, "fake-key")
    assert result == "Pankha Ghar, Azamgarh"


def test_exactly_at_the_threshold_boundary_is_treated_as_far(monkeypatch):
    """Distance strictly greater than REVERSE_MATCH_MAX_DISTANCE_METERS
    triggers the coarse fallback; this constructs a feature just past it."""
    import math
    lat, lon = 26.0, 83.0
    # ~1 degree latitude ~= 111320m -- offset chosen to land just over the threshold.
    offset_deg = (REVERSE_MATCH_MAX_DISTANCE_METERS + 5) / 111320.0
    feature = _feature(lon, lat + offset_deg, {"formatted": "Some Far Shop, Road X", "street": "Road X", "city": "Town"})
    monkeypatch.setattr(geocoding_service, "_get_json", lambda url: {"features": [feature]})

    result = _geoapify_reverse(lat, lon, "fake-key")
    assert "Some Far Shop" not in result


def test_no_features_returns_none(monkeypatch):
    monkeypatch.setattr(geocoding_service, "_get_json", lambda url: {"features": []})
    assert _geoapify_reverse(26.07, 83.18, "fake-key") is None


def test_request_failure_returns_none(monkeypatch):
    monkeypatch.setattr(geocoding_service, "_get_json", lambda url: None)
    assert _geoapify_reverse(26.07, 83.18, "fake-key") is None
