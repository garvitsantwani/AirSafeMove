"""
OpenAQ Real-Time AQI Service for AirSafe Move.

Fetches live PM2.5 measurements from the OpenAQ v3 API for Indian cities
and converts them to AQI using US EPA breakpoints.

Approach (v3 – coordinate-based):
  1. Search locations by coordinates (lat/lon + 25 km radius) with
     parameters_id=2 (PM2.5).
  2. For each location, find the PM2.5 sensor ID from the `sensors` array.
  3. Call `/sensors/{sensor_id}/measurements?limit=1` to get the most
     recent PM2.5 reading.
  4. Convert PM2.5 µg/m³ → AQI via US EPA breakpoints.

Features:
  - Async HTTP requests (via httpx)
  - In-memory TTL cache (5 minutes)
  - Graceful fallback: returns None on any failure
  - API key loaded from OPENAQ_API_KEY environment variable
"""

import os
import logging
import time
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# City coordinates (used for coordinate-based search)
# ---------------------------------------------------------------------------

_CITY_COORDS: Dict[str, tuple] = {
    "Delhi": (28.6139, 77.2090),
    "Mumbai": (19.0760, 72.8777),
    "Bangalore": (12.9716, 77.5946),
    "Chennai": (13.0827, 80.2707),
    "Kolkata": (22.5726, 88.3639),
    "Hyderabad": (17.3850, 78.4867),
    "Pune": (18.5204, 73.8567),
    "Ahmedabad": (23.0225, 72.5714),
    "Jaipur": (26.9124, 75.7873),
    "Lucknow": (26.8467, 80.9462),
    "Shimla": (31.1048, 77.1734),
    "Dehradun": (30.3165, 78.0322),
    "Coimbatore": (11.0168, 76.9558),
    "Mysore": (12.2958, 76.6394),
    "Kochi": (9.9312, 76.2673),
    "Thiruvananthapuram": (8.5241, 76.9366),
    "Chandigarh": (30.7333, 76.7794),
    "Goa (Panaji)": (15.4909, 73.8278),
    "Visakhapatnam": (17.6868, 83.2185),
    "Indore": (22.7196, 75.8577),
    "Bhopal": (23.2599, 77.4126),
    "Nagpur": (21.1458, 79.0882),
    "Vadodara": (22.3072, 73.1812),
    "Surat": (21.1702, 72.8311),
    "Mangalore": (12.9141, 74.8560),
    "Pondicherry": (11.9416, 79.8083),
}

# ---------------------------------------------------------------------------
# Cache implementation
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
    """Convert PM2.5 concentration (µg/m³) to AQI using US EPA breakpoints."""
    if pm25 < 0:
        return 0
    for bp_lo, bp_hi, aqi_lo, aqi_hi in _PM25_BREAKPOINTS:
        if bp_lo <= pm25 <= bp_hi:
            aqi = ((aqi_hi - aqi_lo) / (bp_hi - bp_lo)) * (pm25 - bp_lo) + aqi_lo
            return round(aqi)
    return 500  # Hazardous cap


# ---------------------------------------------------------------------------
# OpenAQ v3 API
# ---------------------------------------------------------------------------

OPENAQ_BASE_URL = "https://api.openaq.org/v3"
SEARCH_RADIUS_M = 25000  # Max allowed by OpenAQ v3 (25 km)


def _get_api_key() -> Optional[str]:
    key = os.getenv("OPENAQ_API_KEY")
    if not key:
        logger.warning(
            "OPENAQ_API_KEY is not set. Real-time AQI lookup will not be available."
        )
    return key


def _find_pm25_sensor_id(location: dict) -> Optional[int]:
    """Find the sensor ID for PM2.5 from a v3 location's sensors list."""
    sensors = location.get("sensors", [])
    for sensor in sensors:
        param = sensor.get("parameter", {})
        if isinstance(param, dict):
            if param.get("id") == 2 or "pm25" in str(param.get("name", "")).lower():
                return sensor.get("id")
        elif isinstance(param, (int, str)):
            # Sometimes param is just the parameter id
            if str(param) == "2":
                return sensor.get("id")
    
    # Fall back: check parameters list
    parameters = location.get("parameters", [])
    for param in parameters:
        if param.get("id") == 2 or "pm25" in str(param.get("name", "")).lower():
            # Try to use the parametersId from sensors
            for sensor in sensors:
                sensor_param = sensor.get("parameter", {})
                if isinstance(sensor_param, dict) and sensor_param.get("id") == param.get("id"):
                    return sensor.get("id")
            # If only one sensor, use it
            if len(sensors) == 1:
                return sensors[0].get("id")
    
    return None


def _extract_pm25_from_location(location: dict) -> Optional[float]:
    """
    Extract latest PM2.5 value from v3 location parameters (if embedded).
    """
    parameters = location.get("parameters", [])
    for param in parameters:
        param_name = str(param.get("name", "")).lower()
        param_id = param.get("id")
        if param_id == 2 or "pm25" in param_name or "pm2.5" in param_name:
            latest = param.get("latest")
            if latest and latest.get("value") is not None:
                val = float(latest["value"])
                if val > 0:
                    return val
    return None


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------


async def get_current_aqi(city_name: str) -> Optional[Dict[str, Any]]:
    """
    Fetch the latest PM2.5-based AQI for a given Indian city from OpenAQ v3.

    Uses coordinate-based search → sensor-based measurements.

    Returns a dict or None on failure.
    """
    cache_key = city_name.lower()
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for city: %s", city_name)
        return cached

    api_key = _get_api_key()
    if not api_key:
        return None

    coords = _CITY_COORDS.get(city_name)
    if not coords:
        logger.info("No coordinates registered for city: %s", city_name)
        return None

    lat, lon = coords
    headers = {"X-API-Key": api_key, "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Step 1: Find locations near the city with PM2.5 sensors
            locations_resp = await client.get(
                f"{OPENAQ_BASE_URL}/locations",
                params={
                    "coordinates": f"{lat},{lon}",
                    "radius": SEARCH_RADIUS_M,
                    "parameters_id": 2,  # PM2.5
                    "limit": 10,
                },
                headers=headers,
            )

            if locations_resp.status_code != 200:
                logger.warning(
                    "OpenAQ locations API returned %d for city '%s'",
                    locations_resp.status_code, city_name,
                )
                return None

            results = locations_resp.json().get("results", [])
            if not results:
                logger.info("No OpenAQ stations found near city: %s", city_name)
                return None

            # Step 2: Check if any location has embedded latest PM2.5
            for location in results:
                pm25_value = _extract_pm25_from_location(location)
                if pm25_value is not None and pm25_value > 0:
                    aqi_estimate = pm25_to_aqi(pm25_value)
                    result = {
                        "city": city_name,
                        "pm25": round(pm25_value, 2),
                        "aqi_estimate": aqi_estimate,
                        "timestamp": "embedded",
                        "data_source": "openaq_live",
                    }
                    _cache_set(cache_key, result)
                    logger.info(
                        "OpenAQ live AQI for %s (embedded): PM2.5=%.1f → AQI=%d",
                        city_name, pm25_value, aqi_estimate,
                    )
                    return result

            # Step 3: Get latest via sensor measurements endpoint
            for location in results[:5]:
                sensor_id = _find_pm25_sensor_id(location)
                if not sensor_id:
                    continue

                meas_resp = await client.get(
                    f"{OPENAQ_BASE_URL}/sensors/{sensor_id}/measurements",
                    params={"limit": 1},
                    headers=headers,
                )

                if meas_resp.status_code != 200:
                    continue

                measurements = meas_resp.json().get("results", [])
                if not measurements:
                    continue

                meas = measurements[0]
                pm25_value = meas.get("value")
                if pm25_value is None or pm25_value <= 0:
                    continue

                # Extract timestamp from the period/datetime
                period = meas.get("period", {})
                timestamp = (
                    period.get("datetimeTo", {}).get("utc")
                    or period.get("datetimeFrom", {}).get("utc")
                    or meas.get("datetime", "unknown")
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
                    "OpenAQ live AQI for %s (sensor %d): PM2.5=%.1f → AQI=%d",
                    city_name, sensor_id, pm25_value, aqi_estimate,
                )
                return result

            logger.info("No recent PM2.5 data found for city: %s", city_name)
            return None

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
    """Fetch live AQI for multiple cities concurrently."""
    import asyncio

    tasks = [get_current_aqi(city) for city in city_names]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output: Dict[str, Optional[Dict[str, Any]]] = {}
    for city, result in zip(city_names, results):
        if isinstance(result, Exception):
            logger.error("Exception fetching AQI for %s: %s", city, result)
            output[city] = None
        else:
            output[city] = result

    return output


def clear_cache() -> None:
    """Clear the AQI cache (useful for testing)."""
    _CACHE.clear()
