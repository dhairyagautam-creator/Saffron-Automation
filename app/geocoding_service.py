"""Landmark-first reverse geocoding, used ONLY when preparing manager
notification emails.

The goal is an address a human manager immediately recognises — a hospital,
mall, school, petrol pump, bus/railway station, named road, society, etc. —
NOT an administrative rollup like "Rajkot, Rajkot East Taluka, Rajkot". So
for each flagged coordinate this resolves, in order:

  1. Nearest NAMED landmark (Geoapify Places nearby search across a broad
     set of recognisable categories, within LANDMARK_RADIUS_METERS). This
     is what surfaces "Shalby Hospital", "Anand Niketan School", etc.
  2. Geoapify reverse geocoding — the street/road/named-place level
     (`formatted`), e.g. "Sai Healthcare, Zadeshwar-Bharuch Road, Bharuch".
     Deliberately NOT the coarse administrative fallback -- UNLESS the
     feature Geoapify actually matched sits farther than
     REVERSE_MATCH_MAX_DISTANCE_METERS from the queried coordinate (see
     `_feature_distance_meters`/`_coarse_address` below), in which case the
     specific POI/business name is dropped in favor of the coarser
     street/city/state components it also returned -- naming a shop or
     hospital the employee wasn't actually at is worse than a plainer but
     honest locality description. Root-caused 2026-08-25 (Release Debugging
     Mode investigation): the Places landmark search above found ZERO
     results for 17/18 real production coordinates tested (rural/small-town
     India has sparse landmark coverage in Geoapify's index), so in
     practice EVERY real case fell through to this reverse step, and its
     `formatted` result turned out to be Geoapify's nearest indexed POI of
     ANY kind -- measured 41-218 meters from the actual visited coordinate
     across 6 spot-checked cases, i.e. a real (not hypothetical) risk of
     naming an unrelated nearby business as if the employee visited it.
  3. None — only if the provider genuinely returns nothing (the caller then
     shows "Address unavailable"; a raw lat/lon is never displayed).

Applies identically in User Mode and Developer Mode — the same Geoapify key
setting is used by both (per environment; see app/geoapify_settings_service).
A local `geocode_cache` table (per-mode via the data engine) means the same
coordinate is never looked up twice.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from loguru import logger
from sqlalchemy.exc import IntegrityError

from app.geoapify_settings_service import get_geoapify_api_key
from app.geo_utils import haversine_km
from database.connection import get_session
from database.models import GeocodeCache

PROVIDER = "Geoapify"
COORDINATE_PRECISION = 6  # ~0.11m — enough to treat a repeated visit as the same spot
GEOCODE_WORKERS = 3
REQUEST_TIMEOUT_SECONDS = 10

PLACES_URL = "https://api.geoapify.com/v2/places"
REVERSE_URL = "https://api.geoapify.com/v1/geocode/reverse"

# How close a named landmark has to be to be used in place of the street
# address. Within this, the employee is essentially AT the landmark; beyond
# it, the reverse-geocoded nearest named place/road is more truthful.
LANDMARK_RADIUS_METERS = 300

# How close the feature Geoapify's REVERSE endpoint actually matched has to
# be to the queried coordinate before its name is trusted as "where the
# employee was". Beyond this, _geoapify_reverse drops the POI/business name
# and falls back to _coarse_address's plainer street/city/state components
# instead. Chosen from the 2026-08-25 investigation's own measured range
# (41m/54m/71m real matches worth keeping vs. 91m/132m/218m ones that
# named a materially unrelated nearby business) -- comfortably above the
# module's own 50m same-location detection radius, so a match this close
# genuinely could be the visited spot, not an arbitrary cutoff.
REVERSE_MATCH_MAX_DISTANCE_METERS = 75

# Broad set of recognisable-landmark categories (Geoapify category tree):
# hospitals/clinics, malls/markets/shops, schools/colleges/universities,
# petrol pumps, public transport (bus/railway), airports, tourism sights,
# commercial/industrial buildings, offices, eateries, parks.
LANDMARK_CATEGORIES = ",".join(
    [
        "healthcare",
        "commercial",
        "education",
        "service.vehicle.fuel",
        "public_transport",
        "airport",
        "tourism",
        "building.commercial",
        "building.industrial",
        "office",
        "catering",
        "leisure",
    ]
)

# Light client-side pacing so a batch doesn't burst the API all at once.
_MIN_REQUEST_INTERVAL_SECONDS = 0.25
_last_request_lock = threading.Lock()
_last_request_time = 0.0


def round_coordinate(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, COORDINATE_PRECISION), round(lon, COORDINATE_PRECISION)


def _pace() -> None:
    global _last_request_time
    with _last_request_lock:
        wait = _MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.monotonic()


def _get_json(url: str) -> dict | None:
    _pace()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "saffron_validator"})
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Geoapify request failed ({exc!r}) for {url.split('?')[0]}")
        return None


def _geoapify_landmark(lat: float, lon: float, api_key: str) -> str | None:
    """Nearest NAMED landmark within LANDMARK_RADIUS_METERS, or None."""
    params = {
        "categories": LANDMARK_CATEGORIES,
        "filter": f"circle:{lon},{lat},{LANDMARK_RADIUS_METERS}",
        "bias": f"proximity:{lon},{lat}",
        "limit": "20",
        "lang": "en",
        "apiKey": api_key,
    }
    data = _get_json(f"{PLACES_URL}?{urllib.parse.urlencode(params)}")
    if not data:
        return None

    named = [
        f["properties"]
        for f in data.get("features", [])
        if (f.get("properties") or {}).get("name")
    ]
    if not named:
        return None
    named.sort(key=lambda p: p.get("distance", 1e9))
    nearest = named[0]
    name = nearest["name"]
    locality = nearest.get("suburb") or nearest.get("city") or nearest.get("street")
    display = f"{name}, {locality}" if locality and locality not in name else name
    logger.info(
        f"Geocoded ({lat}, {lon}) -> landmark '{display}' "
        f"({nearest.get('distance', '?')}m, {','.join(nearest.get('categories', [])[:2])})"
    )
    return display


def _feature_distance_meters(lat: float, lon: float, feature: dict) -> float | None:
    """Straight-line distance between the queried coordinate and the
    coordinate Geoapify's response says it actually matched (GeoJSON
    `geometry.coordinates`, [lon, lat]) -- None if the response carries no
    usable geometry. Same haversine_km formula rules/same_location.py uses
    for its own 50m detection, just applied here to a DIFFERENT question
    (how far is this reverse-geocode match from the query point, not
    whether two visits cluster) -- this never feeds back into detection."""
    coords = (feature.get("geometry") or {}).get("coordinates")
    if not coords or len(coords) < 2:
        return None
    feature_lon, feature_lat = coords[0], coords[1]
    return haversine_km(lat, lon, feature_lat, feature_lon) * 1000.0


def _coarse_address(props: dict) -> str | None:
    """Street/locality-level address built from Geoapify's own component
    fields, deliberately WITHOUT the specific POI/business name `formatted`
    leads with -- used when that matched feature is farther than
    REVERSE_MATCH_MAX_DISTANCE_METERS from the actual query point (see
    _geoapify_reverse), where naming it would misrepresent the visit
    location. None if none of these components were returned either."""
    parts = []
    if props.get("street"):
        parts.append(props["street"])
    city = props.get("city") or props.get("county")
    postcode = props.get("postcode")
    if city and postcode:
        parts.append(f"{city} - {postcode}")
    elif city:
        parts.append(city)
    if props.get("state"):
        parts.append(props["state"])
    if props.get("country"):
        parts.append(props["country"])
    return ", ".join(parts) if parts else None


def _geoapify_reverse(lat: float, lon: float, api_key: str) -> str | None:
    """Street/road/named-place level reverse geocode (Geoapify `formatted`),
    NOT the coarse administrative rollup -- UNLESS the feature Geoapify
    actually matched is farther than REVERSE_MATCH_MAX_DISTANCE_METERS from
    `(lat, lon)`, in which case its specific name is dropped for
    _coarse_address's plainer (but honest) components instead (see this
    module's own docstring for why: measured 41-218m real mismatches).
    Returns None if nothing usable came back at all."""
    params = {"lat": lat, "lon": lon, "lang": "en", "apiKey": api_key}
    data = _get_json(f"{REVERSE_URL}?{urllib.parse.urlencode(params)}")
    if not data:
        return None
    features = data.get("features", [])
    if not features:
        return None
    feature = features[0]
    props = feature.get("properties") or {}

    distance_m = _feature_distance_meters(lat, lon, feature)
    if distance_m is not None and distance_m > REVERSE_MATCH_MAX_DISTANCE_METERS:
        coarse = _coarse_address(props)
        if coarse:
            logger.info(
                f"Geocoded ({lat}, {lon}) -> matched feature {distance_m:.0f}m away "
                f"(> {REVERSE_MATCH_MAX_DISTANCE_METERS}m) -- using coarse '{coarse}' "
                f"instead of naming the distant feature ('{props.get('formatted')}')"
            )
            return coarse
        # No street/city/state component to fall back on either -- fall
        # through to `formatted` below rather than returning nothing, same
        # as the pre-fix behavior for this edge case.

    formatted = props.get("formatted") or props.get("address_line1")
    if not formatted:
        return None
    logger.info(f"Geocoded ({lat}, {lon}) -> reverse '{formatted}'")
    return formatted


def _get_cached_addresses(coords: set[tuple[float, float]]) -> dict[tuple[float, float], str]:
    """One session, one query per candidate coordinate (each an indexed
    unique lookup) — cheap relative to a single network geocode call, so not
    worth the portability risk of a multi-column IN() query."""
    if not coords:
        return {}
    result = {}
    session = get_session()
    try:
        for lat, lon in coords:
            row = session.query(GeocodeCache).filter_by(latitude=lat, longitude=lon).first()
            if row is not None:
                result[(lat, lon)] = row.address
    finally:
        session.close()
    return result


def _store_cached_address(lat: float, lon: float, address: str) -> None:
    """Insert the resolved address into the cache. If another geocode call
    already cached this exact coordinate in the meantime, the unique
    constraint rejects the duplicate — that's just a cache hit we lost the
    race on, not an error."""
    session = get_session()
    try:
        session.add(
            GeocodeCache(
                latitude=lat,
                longitude=lon,
                address=address,
                provider=PROVIDER,
                created_at=datetime.now(),
            )
        )
        session.commit()
    except IntegrityError:
        session.rollback()
    finally:
        session.close()


def _geocode_one(coord: tuple[float, float]) -> str | None:
    """Resolve a single already-rounded, already-known-uncached coordinate to
    a landmark-first address. Never raises. Returns None (never a raw
    "lat, lon" string, never an administrative rollup) if nothing usable was
    found — the caller renders that as "Address unavailable"."""
    lat, lon = coord
    api_key = get_geoapify_api_key()
    if not api_key:
        logger.warning(
            f"No Geoapify API key configured — cannot resolve a precise address for ({lat}, {lon}). "
            "Set one on the Settings page (Location Services)."
        )
        return None

    try:
        address = _geoapify_landmark(lat, lon, api_key) or _geoapify_reverse(lat, lon, api_key)
    except Exception as exc:  # defensive: geocoding must never crash the email pipeline
        logger.warning(f"Unexpected geocoding error for ({lat}, {lon}): {exc!r}")
        return None

    if not address:
        logger.warning(f"No landmark or street-level address found for ({lat}, {lon})")
        return None

    _store_cached_address(lat, lon, address)
    return address


def geocode_many(coords: list[tuple[float, float]], on_progress=None) -> dict[tuple[float, float], str | None]:
    """Resolve many coordinates to landmark-first addresses in one pass:
    dedupe + round, serve every cache hit for free, and resolve only genuine
    misses concurrently. Returns a dict keyed by the *rounded* coordinate —
    every input coordinate gets an entry, but the value is None (never a raw
    coordinate string) for any lookup that couldn't be resolved.

    `on_progress(completed, total)`, if given, is called once up front with
    the cache hits already counted as done, then once more per lookup as it
    finishes (via as_completed) — lets the UI show a live "N / M completed"
    count.
    """
    on_progress = on_progress or (lambda completed, total: None)

    rounded = {round_coordinate(lat, lon) for lat, lon in coords}
    if not rounded:
        return {}

    resolved = _get_cached_addresses(rounded)
    missing = rounded - resolved.keys()

    total = len(rounded)
    completed = len(resolved)
    on_progress(completed, total)

    if missing:
        missing = list(missing)
        with ThreadPoolExecutor(max_workers=min(GEOCODE_WORKERS, len(missing))) as pool:
            futures = {pool.submit(_geocode_one, coord): coord for coord in missing}
            for future in as_completed(futures):
                coord = futures[future]
                resolved[coord] = future.result()
                completed += 1
                on_progress(completed, total)

    return resolved


def geocode_coordinates(lat: float, lon: float) -> str | None:
    """Resolve one (lat, lon) pair to a landmark-first address, or None if it
    couldn't be resolved. Thin single-coordinate wrapper around
    `geocode_many`."""
    return geocode_many([(lat, lon)])[round_coordinate(lat, lon)]
