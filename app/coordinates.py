"""Parses the 'Actual Lat-Long' column into numeric latitude/longitude columns.

No network calls or address resolution happen here — this only splits and
validates the raw coordinate string.

Root cause note (Release Debugging Mode investigation): different CFA/ERP
exports separate the lat/long pair with different characters -- comma in
some exports, underscore in others (confirmed against real production
"Onyx_15.07.2027_Call Time.xlsx" / "Guardians_..._Call Time.xlsx" files,
where 100% of values use "_", e.g. "24.26546_72.18449", not ","). The
parser previously only recognized a comma, so every real production
coordinate silently became (None, None) -- no crash, no error, just zero
valid GPS rows entering clustering downstream, which is what produced zero
findings against real data while a comma-formatted test file kept working.
Both separators are recognized now so the parser is correct for either
export shape, not just the one it happened to be tested against.
"""

import pandas as pd
from loguru import logger

COORDINATE_COLUMN = "Actual Lat-Long"

# Recognized "lat<sep>long" separators, tried in order. Comma first since
# it's the more conventional format; underscore is what real production
# CFA exports (Onyx, Guardians, ...) actually use.
_SEPARATORS = (",", "_")


def _parse_single(value) -> tuple[float | None, float | None]:
    """Parse a 'lat<sep>long' string into (lat, long) floats, trying each of
    _SEPARATORS in turn.

    Returns (None, None) if the value is missing, malformed, or out of range.
    """
    if pd.isna(value):
        return None, None

    text = str(value)
    for sep in _SEPARATORS:
        parts = text.split(sep)
        if len(parts) != 2:
            continue

        try:
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
        except ValueError:
            continue

        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            continue

        return lat, lon

    return None, None


def parse_coordinates(df: pd.DataFrame) -> dict:
    """Add 'latitude' and 'longitude' numeric columns to `df` in place.

    The original 'Actual Lat-Long' column is left untouched so both the raw
    and parsed values end up persisted together.

    Returns a dict with `valid_count` and `invalid_count`.
    """
    if COORDINATE_COLUMN not in df.columns:
        logger.warning(f"'{COORDINATE_COLUMN}' column not found; skipping coordinate parsing")
        df["latitude"] = None
        df["longitude"] = None
        return {"valid_count": 0, "invalid_count": len(df)}

    parsed = df[COORDINATE_COLUMN].apply(_parse_single)
    df["latitude"] = parsed.apply(lambda p: p[0])
    df["longitude"] = parsed.apply(lambda p: p[1])

    valid_count = int(df["latitude"].notna().sum())
    invalid_count = len(df) - valid_count

    logger.info(f"Coordinates parsed: {valid_count} valid, {invalid_count} invalid/missing")

    return {"valid_count": valid_count, "invalid_count": invalid_count}
