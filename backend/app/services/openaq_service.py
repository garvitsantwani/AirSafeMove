"""
OpenAQ Real-Time AQI Service for AirSafe Move.

Fetches live PM2.5 measurements from the OpenAQ v3 API for Indian cities
and converts them to AQI using US EPA breakpoints.

Features:
- Async HTTP requests (via httpx)
- In-memory TTL cache (5 minutes) to avoid repeated API hits per request cycle
- Graceful fallback: returns None on any failure so the pipeline uses historical data
- API key loaded from OPENAQ_API_KEY environment variable
"""

import os
import math
import logging
import time
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache implementation (simple dict-based TTL cache, no extra dependency)
# ---------------------------------------------------------------------------

_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    entry = _CACHE.get(key)
    if entry and (time.monotonic() - entry["ts"]) < _CACHE_TTL_SECONDS:
        return entry["value"]
    return None


def _cache_set(key: str, value: Any) -> None:
    _CACHE[key] = {"value": value, "ts": time.monotonic()}


# ---------------------------------------------------------------------------
# US EPA PM2.5 → AQI conversion
# ---------------------------------------------------------------------------

# (PM2.5 low, PM2.5 high, AQI low, AQI high)
_PM25_BREAKPOINTS = [
    (0.0, 12.0, 0, 50),
    (12.1, 35.4, 51, 100),
    (35.5, 55.4, 101, 150),
    (55.5, 150.4, 151, 200),
    (150.5, 250.4, 201, 300),
    (250.5, 350.4, 301, 400),
    (350.5, 500.4, 401, 500),
]


def pm25_to_aqi(pm25: float) -> int:
    """
    Convert a PM2.5 concentration (µg/m³) to AQI using US EPA linear interpolation.
    Values above 500.4 are capped at 500.
    """
    if pm25 < 0:
        return 0
    for bp_lo, bp_hi, aqi_lo, aqi_hi in _PM25_BREAKPOINTS:
        if bp_lo <= pm25 <= bp_hi:
            aqi = ((aqi_hi - aqi_lo) / (bp_hi - bp_lo)) * (pm25 - bp_lo) + aqi_lo
            return round(aqi)
    return 500  # Hazardous cap


# ---------------------------------------------------------------------------
# City name → OpenAQ-friendly search terms
# ---------------------------------------------------------------------------

# Some Indian city names need slight adjustments to match OpenAQ location data
_CITY_SEARCH_ALIASES: Dict[str, str] = {
    "Bangalore": "Bengaluru",
    "Goa (Panaji)": "Panaji",
    "Thiruvananthapuram": "Thiruvananthapuram",
    "Visakhapatnam": "Visakhapatnam",
}

OPENAQ_BASE_URL = "https://api.openaq.org/v3"


def _get_api_key() -> Optional[str]:
    key = os.getenv("OPENAQ_API_KEY")
    if not key:
        logger.warning(
            "OPENAQ_API_KEY is not set. Real-time AQI lookup will not be available."
        )
    return key


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------


async def get_current_aqi(city_name: str) -> Optional[Dict[str, Any]]:
    """
    Fetch the latest PM2.5-based AQI for a given Indian city from OpenAQ v3.

    Returns a dict:
    {
        "city": str,
        "pm25": float,
        "aqi_estimate": int,
        "timestamp": str,          # ISO8601 UTC
        "data_source": "openaq_live"
    }

    Returns None if:
    - OPENAQ_API_KEY is not set
    - No station found for the city
    - No recent PM2.5 measurement available
    - Any network / API error
    """
    cache_key = city_name.lower()
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for city: %s", city_name)
        return cached

    api_key = _get_api_key()
    if not api_key:
        return None

    search_name = _CITY_SEARCH_ALIASES.get(city_name, city_name)
    headers = {"X-API-Key": api_key, "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Step 1: find a location/station in the city
            locations_resp = await client.get(
                f"{OPENAQ_BASE_URL}/locations",
                params={
                    "city": search_name,
                    "parameters_id": 2,  # PM2.5 parameter ID in OpenAQ v3
                    "limit": 5,
                    "order_by": "lastUpdated",
                    "sort": "desc",
                },
                headers=headers,
            )

            if locations_resp.status_code != 200:
                logger.warning(
                    "OpenAQ locations API returned %d for city '%s'",
                    locations_resp.status_code,
                    city_name,
                )
                return None

            locations_data = locations_resp.json()
            results = locations_data.get("results", [])

            if not results:
                logger.info(
                    "No OpenAQ stations found for city: %s (searched: %s)",
                    city_name,
                    search_name,
                )
                return None

            location_id = results[0].get("id")
            if not location_id:
                return None

            # Step 2: fetch the latest PM2.5 measurement for that location
            measurements_resp = await client.get(
                f"{OPENAQ_BASE_URL}/measurements",
                params={
                    "location_id": location_id,
                    "parameters_id": 2,  # PM2.5
                    "limit": 1,
                    "order_by": "datetime",
                    "sort": "desc",
                },
                headers=headers,
            )

            if measurements_resp.status_code != 200:
                logger.warning(
                    "OpenAQ measurements API returned %d for location %s",
                    measurements_resp.status_code,
                    location_id,
                )
                return None

            measurements_data = measurements_resp.json()
            measurements = measurements_data.get("results", [])

            if not measurements:
                logger.info(
                    "No PM2.5 measurements found for location %d in city '%s'",
                    location_id,
                    city_name,
                )
                return None

            measurement = measurements[0]
            pm25_value = measurement.get("value")
            if pm25_value is None or pm25_value < 0:
                logger.info(
                    "Invalid PM2.5 value '%s' for city '%s'", pm25_value, city_name
                )
                return None

            timestamp = (
                measurement.get("date", {}).get("utc")
                or measurement.get("datetime", {}).get("utc")
                or "unknown"
            )

            aqi_estimate = pm25_to_aqi(float(pm25_value))

            result = {
                "city": city_name,
                "pm25": round(float(pm25_value), 2),
                "aqi_estimate": aqi_estimate,
                "timestamp": timestamp,
                "data_source": "openaq_live",
            }

            _cache_set(cache_key, result)
            logger.info(
                "OpenAQ live AQI for %s: PM2.5=%.1f µg/m³ → AQI=%d",
                city_name,
                pm25_value,
                aqi_estimate,
            )
            return result

    except httpx.RequestError as exc:
        logger.error(
            "Network error fetching OpenAQ data for city '%s': %s", city_name, exc
        )
        return None
    except Exception as exc:
        logger.error(
            "Unexpected error in OpenAQ service for city '%s': %s", city_name, exc
        )
        return None


async def get_current_aqi_batch(
    city_names: list[str],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Fetch live AQI for multiple cities concurrently.

    Returns a dict mapping city_name → result (or None on failure).
    """
    import asyncio

    tasks = [get_current_aqi(city) for city in city_names]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output: Dict[str, Optional[Dict[str, Any]]] = {}
    for city, result in zip(city_names, results):
        if isinstance(result, Exception):
            logger.error("Exception fetching AQI for %s: %s", city, result)
            output[city] = None
        else:
            output[city] = result  # type: ignore[assignment]

    return output


def clear_cache() -> None:
    """Clear the AQI cache (useful for testing)."""
    _CACHE.clear()
