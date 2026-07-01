#!/usr/bin/env python3
"""
notam_common.py — Shared NOTAM parsing utilities

Provides coordinate parsing (DMS → decimal degrees), whitespace
normalisation, ISO 8601 time conversion, and GeoJSON serialisation
for NOTAM data.

Normalised NOTAM dict fields (used by both CAAS and CAAM parsers):
    id            – e.g. "A1737/26"
    type          – single-letter code, e.g. "N", "R"
    message       – body text (before structured fields)
    lower         – lower altitude limit, or None
    upper         – upper altitude limit, or None
    from          – effective-from (ISO 8601 string), or None
    to            – effective-to (ISO 8601 string), or None
    time_schedule – time schedule string, or None
    locations     – list[{"raw", "latitude", "longitude"}]
    replaces      – replaced NOTAM id (CAAM-only), or None
    fir_or_icao   – FIR/ICAO identifier (CAAM-only), or None
    raw           – original block text
"""

import re
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# NOTAM coordinate formats:
#   Integer seconds:  DDMMSSsH DDDMMSSsH          e.g. 011914N1034544E
#   Decimal seconds:  DDMMSSss.ssH DDDMMSSss.ssH  e.g. 023000.00N1051628.72E
#
# Lat:  2-digit DD, 2-digit MM, 2-digit SS + optional decimal, N/S
# Lon:  3-digit DDD, 2-digit MM, 2-digit SS + optional decimal, E/W
# Optional whitespace between lat and lon tokens.
COORD_RE = re.compile(
    r"\b(\d{6}(?:\.\d+)?[NS])\s*(\d{7}(?:\.\d+)?[EW])\b"
)


# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------

def dms_to_dec(deg: int, mins: int, secs: float, hem: str) -> float:
    """Convert degrees/minutes/seconds + hemisphere to signed decimal degrees."""
    dec = deg + mins / 60 + secs / 3600
    return -dec if hem in ("S", "W") else dec


def parse_lat(raw: str) -> float:
    """
    Parse a raw latitude token (e.g. '011914N' or '023000.00N').
    Returns decimal degrees.
    """
    hem = raw[-1]
    numeric = raw[:-1]
    d = int(numeric[0:2])
    m = int(numeric[2:4])
    s = float(numeric[4:])
    return dms_to_dec(d, m, s, hem)


def parse_lon(raw: str) -> float:
    """
    Parse a raw longitude token (e.g. '1034544E' or '1051628.72E').
    Returns decimal degrees.
    """
    hem = raw[-1]
    numeric = raw[:-1]
    d = int(numeric[0:3])
    m = int(numeric[3:5])
    s = float(numeric[5:])
    return dms_to_dec(d, m, s, hem)


def parse_coords(text: str) -> list[dict]:
    """
    Find all NOTAM-format coordinates in *text* and return them as a list of
    dicts with decimal-degree lat/lon.

    Duplicate occurrences are skipped.
    """
    results = []
    seen = set()
    for lat_raw, lon_raw in COORD_RE.findall(text):
        key = (lat_raw, lon_raw)
        if key in seen:
            continue
        seen.add(key)

        lat_dec = parse_lat(lat_raw)
        lon_dec = parse_lon(lon_raw)

        results.append({
            "raw":       f"{lat_raw} {lon_raw}",
            "latitude":  round(lat_dec, 6),
            "longitude": round(lon_dec, 6),
        })
    return results


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

def normalise_ws(text: str) -> str:
    """Collapse all whitespace runs into a single space and strip."""
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# ISO 8601 conversion
# ---------------------------------------------------------------------------

def to_iso8601(value: str | None, fmt: str) -> str | None:
    """Parse *value* with *fmt* (strptime) and return ISO 8601 UTC string.

    Returns ``None`` unchanged, and preserves sentinel values such as
    ``"PERM"`` as-is.  If *value* does not match *fmt* (e.g. corrupt
    source data) the function returns ``None`` instead of crashing.
    """
    if not value or value == "PERM":
        return value
    try:
        return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# GeoJSON serialisation
# ---------------------------------------------------------------------------

def notam_to_geojson_feature(notam: dict) -> dict:
    """
    Convert one parsed NOTAM dict to a GeoJSON Feature.

    Geometry rules:
      0 locations → null geometry
      1 location  → Point
      2 locations → LineString
      3+ locations → Polygon (ring closed automatically if needed)
    """
    locs = notam.get("locations") or []
    n = len(locs)

    coords = [[loc["longitude"], loc["latitude"]] for loc in locs]

    if n == 0:
        geometry = None
    elif n == 1:
        geometry = {"type": "Point", "coordinates": coords[0]}
    elif n == 2:
        geometry = {"type": "LineString", "coordinates": coords}
    else:
        ring = coords if coords[0] == coords[-1] else coords + [coords[0]]
        geometry = {"type": "Polygon", "coordinates": [ring]}

    properties = {
        "id":            notam.get("id"),
        "type":          notam.get("type"),
        "message":       notam.get("message"),
        "lower":         notam.get("lower"),
        "upper":         notam.get("upper"),
        "from":          notam.get("from"),
        "to":            notam.get("to"),
        "time_schedule": notam.get("time_schedule"),
        "coord_tokens":  [loc["raw"] for loc in locs] or None,
    }

    # Include optional CAAM-specific fields if present
    for opt in ("replaces", "fir_or_icao"):
        val = notam.get(opt)
        if val is not None:
            properties[opt] = val

    return {
        "type":       "Feature",
        "geometry":   geometry,
        "properties": properties,
    }


def to_geojson(notams: list[dict]) -> dict:
    return {
        "type":     "FeatureCollection",
        "features": [notam_to_geojson_feature(n) for n in notams],
    }
