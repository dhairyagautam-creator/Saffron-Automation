"""Hospital Suppression — a post-processing filter, not a validation rule.

Many doctors work inside large hospitals or medical campuses, where several
legitimate visits can land within the Same Location Rule's 100m cluster
radius purely because the hospital itself is small. This module answers one
question for an *already-flagged* finding: is there a real hospital or
medical facility right next to the location that caused the flag? If so,
the automatic email notification for that finding is withheld — the
finding itself is untouched and still shows up in the Findings table
exactly as before. This never changes whether an employee gets flagged,
only whether a notification goes out.

Only ever called against a flagged finding's own cluster coordinate — never
against every visit in the uploaded workbook, per the performance
requirement. Queries the Geoapify Places API's Nearby Search for
hospital/clinic/healthcare features within the configured radius, evaluates
every candidate returned (not just the first) and picks the nearest,
cache-first via `hospital_lookup_cache` (keyed by rounded coordinate) so the
same cluster is never looked up twice.

Provider history: this used to query OpenStreetMap's free, unauthenticated
Overpass API. Across real production runs it was directly observed
producing three distinct failure modes — HTTP 429 rate-limiting, extended
full unresponsiveness, and slow HTTP 504 Gateway Timeouts (one coordinate
took ~35s across two 504s before finally succeeding) — while a standalone,
isolated request against the exact same endpoint with the exact same query
succeeded in under 2 seconds and returned the correct hospital. That's
decisive evidence the free tier's own backend reliability, not our request
logic, was the bottleneck: our parsing, place-type selection, and radius
were all already correct when Overpass actually answered. Migrated to
Geoapify (a paid-tier-capable Places API with an SLA) rather than continue
tuning retry/backoff parameters against a best-effort free resource.

Diagnostics: every request logs the exact URL, HTTP status, response time,
and raw response body; every decision logs which POI (if any) was selected,
its distance, and the explicit final suppression decision — see
find_nearby_hospital and _query_geoapify.
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

from app.geo_utils import haversine_km
from app.geoapify_settings_service import get_geoapify_api_key
from database.connection import get_session
from database.models import HospitalLookupCache

GEOAPIFY_URL = "https://api.geoapify.com/v2/places"
# The parent "healthcare" category casts the same broad net the old Overpass
# query used (hospital + clinic + doctors + any other healthcare-tagged
# feature) rather than narrowing to just "healthcare.hospital" and risking
# missing a legitimate small clinic/doctor's office.
GEOAPIFY_CATEGORIES = "healthcare"
DEFAULT_RADIUS_METERS = 100
COORDINATE_PRECISION = 5  # ~1.1m -- plenty for a 100m-radius lookup, coarser than geocode_cache's 6
REQUEST_TIMEOUT_SECONDS = 10
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.5
# Geoapify's free tier is a per-day request quota, not a strict
# per-second throttle like Nominatim's usage policy -- a small self-imposed
# pace still avoids bursting a full batch at once, without the Overpass-era
# single-worker/1-req-sec constraint that traded a lot of wall-clock time
# for reliability against a much less predictable free service.
LOOKUP_WORKERS = 3
MIN_REQUEST_INTERVAL_SECONDS = 0.3
_last_request_lock = threading.Lock()
_last_request_time = 0.0


def _wait_for_rate_limit() -> None:
    global _last_request_time
    with _last_request_lock:
        wait = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        _last_request_time = time.monotonic()


# A whole-batch safeguard on top of the per-lookup retries below: if the
# provider (any provider, including a misconfigured/missing API key) is
# having a genuinely bad run, retrying each remaining coordinate for its own
# full MAX_ATTEMPTS budget just multiplies a guaranteed failure across every
# finding. Once BATCH_FAILURE_THRESHOLD lookups in the same
# find_hospitals_many() batch have failed, or BATCH_TIME_BUDGET_SECONDS has
# elapsed since it started, every remaining coordinate in that batch is
# skipped without even attempting a request -- treated as not-suppressed
# (fail-open, same as any other lookup failure) and clearly logged as
# skipped rather than failed, so it's never confused with "no hospital
# found."
BATCH_FAILURE_THRESHOLD = 3
BATCH_TIME_BUDGET_SECONDS = 60


class _CircuitBreaker:
    """Shared across every lookup in one find_hospitals_many() batch. Trips
    (and stays tripped for the rest of that batch) once too many lookups
    have failed, or the batch has been running too long -- whichever comes
    first. Thread-safe since lookups run concurrently across LOOKUP_WORKERS."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._tripped = False
        self._started_at = time.monotonic()

    def should_skip(self) -> bool:
        with self._lock:
            if self._tripped:
                return True
            if time.monotonic() - self._started_at > BATCH_TIME_BUDGET_SECONDS:
                self._tripped = True
                logger.warning(
                    f"Hospital suppression circuit breaker OPEN: batch exceeded "
                    f"{BATCH_TIME_BUDGET_SECONDS}s -- skipping remaining lookups this run."
                )
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._consecutive_failures >= BATCH_FAILURE_THRESHOLD and not self._tripped:
                self._tripped = True
                logger.warning(
                    f"Hospital suppression circuit breaker OPEN: {self._consecutive_failures} "
                    "lookups failed in a row this run -- skipping remaining lookups instead of "
                    "retrying against what looks like a degraded provider."
                )


def round_coordinate(lat: float, lon: float) -> tuple[float, float]:
    return round(lat, COORDINATE_PRECISION), round(lon, COORDINATE_PRECISION)


def _get_cached(lat: float, lon: float) -> dict | None:
    session = get_session()
    try:
        row = session.query(HospitalLookupCache).filter_by(latitude=lat, longitude=lon).first()
        if row is None:
            return None
        return {
            "found": bool(row.found),
            "name": row.hospital_name,
            "lat": row.hospital_lat,
            "lon": row.hospital_lon,
            "distance_meters": row.distance_meters,
            "error": None,
        }
    finally:
        session.close()


def _store_cached(lat: float, lon: float, result: dict) -> None:
    """If another lookup already cached this exact (rounded) coordinate in
    the meantime, the unique constraint rejects the duplicate — that's just
    a cache hit we lost the race on, not an error (same pattern as
    app/geocoding_service.py). Never called for a failed lookup — see
    module docstring."""
    session = get_session()
    try:
        session.add(
            HospitalLookupCache(
                latitude=lat,
                longitude=lon,
                found=1 if result["found"] else 0,
                hospital_name=result["name"],
                hospital_lat=result["lat"],
                hospital_lon=result["lon"],
                distance_meters=result["distance_meters"],
                created_at=datetime.now(),
            )
        )
        session.commit()
    except IntegrityError:
        session.rollback()
    finally:
        session.close()


class GeoapifyNotConfigured(Exception):
    """Raised when no Geoapify API key has been saved on the Settings page."""


def _query_geoapify(lat: float, lon: float, radius_meters: int, api_key: str) -> dict:
    """A single Geoapify Places API request, with retries — a paid-tier-
    capable API can still have a transient blip, so the same MAX_ATTEMPTS
    retry-with-backoff pattern used for the old Overpass integration is
    kept, just against one (not two) endpoints, since Geoapify doesn't need
    a community-run fallback mirror the way Overpass did.

    Every attempt logs: the exact request URL (API key redacted), HTTP
    status code (or the transport-level error if the server never responded
    at all), response time, and — on success — the raw response body
    (bounded in size). Raises the last error if every attempt fails.
    """
    params = {
        "categories": GEOAPIFY_CATEGORIES,
        "filter": f"circle:{lon},{lat},{radius_meters}",
        "limit": "20",
        "lang": "en",
        "apiKey": api_key,
    }
    url = f"{GEOAPIFY_URL}?{urllib.parse.urlencode(params)}"
    redacted_url = url.replace(api_key, "***REDACTED***")

    last_exc = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _wait_for_rate_limit()
        request = urllib.request.Request(url, headers={"User-Agent": "saffron_validator"})
        logger.info(f"[Hospital API] Service: Geoapify Places API | Request URL: {redacted_url}")
        attempt_start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                raw_body = response.read().decode("utf-8")
                elapsed = time.perf_counter() - attempt_start
                logger.info(
                    f"[Hospital API] Response for ({lat}, {lon}): HTTP {response.status}, "
                    f"{elapsed:.2f}s, body ({len(raw_body)} bytes): {raw_body[:500]!r}"
                    + ("... [truncated]" if len(raw_body) > 500 else "")
                )
                return json.loads(raw_body)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            elapsed = time.perf_counter() - attempt_start
            body_snippet = ""
            try:
                body_snippet = exc.read().decode("utf-8")[:500]
            except Exception:
                pass
            logger.warning(
                f"[Hospital API] Request FAILED for ({lat}, {lon}), attempt {attempt}/{MAX_ATTEMPTS}: "
                f"HTTP status={exc.code}, elapsed={elapsed:.2f}s, response body: {body_snippet!r}"
            )
            if exc.code in (401, 403):
                # An invalid/missing API key will never succeed on retry —
                # fail this coordinate immediately instead of burning the
                # full retry budget on a guaranteed repeat of the same 401/403.
                raise
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_exc = exc
            elapsed = time.perf_counter() - attempt_start
            logger.warning(
                f"[Hospital API] Request FAILED for ({lat}, {lon}), attempt {attempt}/{MAX_ATTEMPTS}: "
                f"elapsed={elapsed:.2f}s, exception={exc!r}"
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise last_exc


def _extract_candidates(features: list, lat: float, lon: float) -> list[dict]:
    """Turn every Geoapify GeoJSON feature into a candidate
    {name, lat, lon, distance_meters}, sorted nearest-first. Evaluates ALL
    of them — the decision below picks the nearest only after every
    candidate within the response has been measured, not just the first
    one Geoapify happened to list. Geoapify already computes `distance`
    server-side (meters from the search circle's center) when a `filter`
    is used; haversine is only a defensive fallback if that field is ever
    missing from a feature."""
    candidates = []
    for feature in features:
        props = feature.get("properties") or {}
        flat, flon = props.get("lat"), props.get("lon")
        if flat is None or flon is None:
            continue

        name = props.get("name") or props.get("formatted") or "Unnamed medical facility"
        distance_m = props.get("distance")
        if distance_m is None:
            distance_m = haversine_km(lat, lon, flat, flon) * 1000
        candidates.append({"name": name, "lat": flat, "lon": flon, "distance_meters": distance_m})

    candidates.sort(key=lambda c: c["distance_meters"])
    return candidates


def find_nearby_hospital(
    lat: float,
    lon: float,
    radius_meters: int = DEFAULT_RADIUS_METERS,
    circuit_breaker: "_CircuitBreaker | None" = None,
) -> dict:
    """Return {'found', 'name', 'lat', 'lon', 'distance_meters', 'error'}
    for the nearest hospital/clinic/healthcare facility within
    `radius_meters` of (lat, lon), evaluating every candidate Geoapify
    returns. Cache-first.

    `error` is None for a genuine answer (found or not); otherwise it holds
    the reason the lookup itself couldn't be completed (missing API key,
    network/API failure) after all retries — the caller should treat that
    case as "we don't actually know" rather than "confirmed no hospital",
    and this function still fails OPEN (found=False) so a lookup failure
    never blocks the email pipeline. The full candidate list and the final
    decision are always logged.

    `circuit_breaker`, when passed by find_hospitals_many, lets this call
    skip the network entirely once the shared breaker has tripped for this
    batch — see _CircuitBreaker.
    """
    rounded_lat, rounded_lon = round_coordinate(lat, lon)
    lookup_start = time.perf_counter()

    cached = _get_cached(rounded_lat, rounded_lon)
    if cached is not None:
        logger.info(
            f"[Hospital Check] Lat={rounded_lat}, Lon={rounded_lon} | executed=False (cache hit) | "
            f"found={cached['found']}, name={cached['name']}, distance={cached['distance_meters']}m"
        )
        return cached

    if circuit_breaker is not None and circuit_breaker.should_skip():
        logger.warning(
            f"[Hospital Check] Lat={rounded_lat}, Lon={rounded_lon} | executed=False (circuit breaker "
            "open for this batch, not retried) | Final decision: NOT suppressed (fail-open) -- this is a "
            "skipped lookup, not a confirmed absence of a nearby hospital."
        )
        return {
            "found": False,
            "name": None,
            "lat": None,
            "lon": None,
            "distance_meters": None,
            "error": "skipped — circuit breaker open (provider failing repeatedly this run)",
        }

    api_key = get_geoapify_api_key()
    if not api_key:
        logger.error(
            f"[Hospital Check] Lat={rounded_lat}, Lon={rounded_lon} | executed=False (no Geoapify API key "
            "configured on the Settings page) | Final decision: NOT suppressed (fail-open) -- this is a "
            "configuration gap, not a confirmed absence of a nearby hospital."
        )
        return {
            "found": False,
            "name": None,
            "lat": None,
            "lon": None,
            "distance_meters": None,
            "error": "no Geoapify API key configured",
        }

    try:
        data = _query_geoapify(rounded_lat, rounded_lon, radius_meters, api_key)
    except Exception as exc:
        if circuit_breaker is not None:
            circuit_breaker.record_failure()
        elapsed = time.perf_counter() - lookup_start
        logger.error(
            f"[Hospital Check] Lat={rounded_lat}, Lon={rounded_lon} | executed=True | "
            f"total lookup time={elapsed:.2f}s | FAILED after retries: {exc!r} | "
            "Final decision: NOT suppressed (fail-open) -- this is a lookup failure, not a confirmed "
            "absence of a nearby hospital."
        )
        return {"found": False, "name": None, "lat": None, "lon": None, "distance_meters": None, "error": str(exc)}

    if circuit_breaker is not None:
        circuit_breaker.record_success()

    features = data.get("features", [])
    candidates = _extract_candidates(features, rounded_lat, rounded_lon)
    elapsed = time.perf_counter() - lookup_start

    logger.info(
        f"[Hospital Check] Lat={rounded_lat}, Lon={rounded_lon} | executed=True | total lookup time={elapsed:.2f}s | "
        f"radius={radius_meters}m | Geoapify returned {len(features)} feature(s), {len(candidates)} medical "
        f"POI(s) with usable coordinates."
    )
    for candidate in candidates:
        logger.info(
            f"  candidate: {candidate['name']} at ({candidate['lat']}, {candidate['lon']}), "
            f"{candidate['distance_meters']:.1f}m away"
        )

    if not candidates:
        logger.info(
            f"[Hospital Check] Lat={rounded_lat}, Lon={rounded_lon} | no medical POIs returned | "
            "Final decision: NOT suppressed (genuine — Geoapify answered, no facility within radius)."
        )
        result = {"found": False, "name": None, "lat": None, "lon": None, "distance_meters": None, "error": None}
    else:
        nearest = candidates[0]
        logger.info(
            f"[Hospital Check] Lat={rounded_lat}, Lon={rounded_lon} | selected POI: '{nearest['name']}' "
            f"at ({nearest['lat']}, {nearest['lon']}), {nearest['distance_meters']:.1f}m away, out of "
            f"{len(candidates)} POI(s) found | Final decision: SUPPRESSED."
        )
        result = {
            "found": True,
            "name": nearest["name"],
            "lat": nearest["lat"],
            "lon": nearest["lon"],
            "distance_meters": round(nearest["distance_meters"]),
            "error": None,
        }

    _store_cached(rounded_lat, rounded_lon, result)
    return result


def find_hospitals_many(
    coords: list[tuple[float, float]],
    radius_meters: int = DEFAULT_RADIUS_METERS,
    on_progress=None,
) -> dict:
    """Resolve many cluster coordinates at once: dedupe + round, serve every
    cache hit for free, and look up genuine misses concurrently (small
    thread pool) instead of one at a time. Returns a dict keyed by the
    *rounded* coordinate — same shape as app.geocoding_service.geocode_many.

    Only ever call this with coordinates belonging to findings that are
    already flagged — never with every visit in the workbook.

    `on_progress(completed, total)`, if given, is called once up front with
    the cache hits already counted as done, then once more per lookup as it
    finishes (via as_completed, so it fires in completion order rather than
    waiting for the whole batch) — this is what lets the UI show a live
    "N / M checked" count instead of one before/after log line.
    """
    on_progress = on_progress or (lambda completed, total: None)

    rounded = {round_coordinate(lat, lon) for lat, lon in coords}
    if not rounded:
        return {}

    resolved: dict = {}
    missing = []
    for coord in rounded:
        cached = _get_cached(*coord)
        if cached is not None:
            resolved[coord] = cached
        else:
            missing.append(coord)

    total = len(rounded)
    completed = len(resolved)
    on_progress(completed, total)

    if missing:
        circuit_breaker = _CircuitBreaker()
        with ThreadPoolExecutor(max_workers=min(LOOKUP_WORKERS, len(missing))) as pool:
            futures = {
                pool.submit(find_nearby_hospital, lat, lon, radius_meters, circuit_breaker): (lat, lon)
                for lat, lon in missing
            }
            for future in as_completed(futures):
                coord = futures[future]
                resolved[coord] = future.result()
                completed += 1
                on_progress(completed, total)

    return resolved


def format_suppression_reason(name: str, distance_meters: int) -> str:
    """'Apollo Hospital (63 meters away)' — the exact display format
    requested for the Findings page."""
    return f"{name} ({distance_meters} meters away)"
