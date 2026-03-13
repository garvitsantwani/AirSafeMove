"""
Unit tests for services/openaq_service.py

Tests cover:
1. Successful city AQI query with mocked OpenAQ API
2. Missing measurement (empty results) - graceful None return
3. API failure (network error) - graceful None return
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Make sure the test runner can import the module
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.openaq_service import (
    get_current_aqi,
    pm25_to_aqi,
    clear_cache,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_mock_response(status_code: int, json_data: dict):
    """Build a minimal mock httpx Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json = MagicMock(return_value=json_data)
    return mock


LOCATIONS_SUCCESS = {
    "results": [{"id": 123, "city": "Mumbai", "lastUpdated": "2024-01-01T00:00:00Z"}]
}

MEASUREMENTS_SUCCESS = {
    "results": [
        {
            "value": 45.0,
            "date": {"utc": "2024-01-01T12:00:00Z"},
            "parameter": "pm25",
        }
    ]
}

EMPTY_RESULTS = {"results": []}


# ---------------------------------------------------------------------------
# Test: Successful query
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_current_aqi_success():
    """
    Given a valid OpenAQ response with PM2.5 = 45 µg/m³,
    the service should return a result dict with a computed AQI
    and data_source = 'openaq_live'.
    """
    clear_cache()

    with patch.dict(os.environ, {"OPENAQ_API_KEY": "test-key-123"}):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, LOCATIONS_SUCCESS),
                _make_mock_response(200, MEASUREMENTS_SUCCESS),
            ]
        )

        with patch("app.services.openaq_service.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_current_aqi("Mumbai")

    assert result is not None
    assert result["city"] == "Mumbai"
    assert result["pm25"] == 45.0
    assert result["data_source"] == "openaq_live"
    # PM2.5 = 45 µg/m³ → AQI should be 128 (Unhealthy for Sensitive)
    assert result["aqi_estimate"] == pm25_to_aqi(45.0)
    assert isinstance(result["aqi_estimate"], int)


# ---------------------------------------------------------------------------
# Test: Missing measurement (empty results)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_current_aqi_missing_measurement():
    """
    When OpenAQ finds a location but returns no measurements,
    the service should return None gracefully (no exception raised).
    """
    clear_cache()

    with patch.dict(os.environ, {"OPENAQ_API_KEY": "test-key-123"}):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[
                _make_mock_response(200, LOCATIONS_SUCCESS),
                _make_mock_response(200, EMPTY_RESULTS),  # no measurements
            ]
        )

        with patch("app.services.openaq_service.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_current_aqi("SomeSmallTown")

    assert result is None


# ---------------------------------------------------------------------------
# Test: No station found for city
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_current_aqi_no_station():
    """
    When OpenAQ returns no stations for a city, return None.
    """
    clear_cache()

    with patch.dict(os.environ, {"OPENAQ_API_KEY": "test-key-123"}):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            return_value=_make_mock_response(200, EMPTY_RESULTS)
        )

        with patch("app.services.openaq_service.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_current_aqi("UnknownCityXYZ")

    assert result is None


# ---------------------------------------------------------------------------
# Test: API failure (network error) - fallback to None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_current_aqi_api_failure():
    """
    When a network error occurs during the API call,
    the service should catch it and return None without raising.
    """
    import httpx

    clear_cache()

    with patch.dict(os.environ, {"OPENAQ_API_KEY": "test-key-123"}):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.RequestError("Connection refused")
        )

        with patch("app.services.openaq_service.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await get_current_aqi("Delhi")

    # Must return None, must not raise
    assert result is None


# ---------------------------------------------------------------------------
# Test: No API key set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_current_aqi_no_api_key():
    """When OPENAQ_API_KEY is not set, return None immediately."""
    clear_cache()

    env_without_key = {k: v for k, v in os.environ.items() if k != "OPENAQ_API_KEY"}

    with patch.dict(os.environ, env_without_key, clear=True):
        result = await get_current_aqi("Delhi")

    assert result is None


# ---------------------------------------------------------------------------
# Test: pm25_to_aqi conversion
# ---------------------------------------------------------------------------

def test_pm25_to_aqi_good():
    """PM2.5 = 5 µg/m³ should map to AQI in Good range (≤ 50)."""
    aqi = pm25_to_aqi(5.0)
    assert 0 <= aqi <= 50


def test_pm25_to_aqi_moderate():
    """PM2.5 = 20 µg/m³ should map to AQI in Moderate range (51-100)."""
    aqi = pm25_to_aqi(20.0)
    assert 51 <= aqi <= 100


def test_pm25_to_aqi_unhealthy_sensitive():
    """PM2.5 = 45 µg/m³ should map to AQI in 101-150 (Unhealthy for Sensitive)."""
    aqi = pm25_to_aqi(45.0)
    assert 101 <= aqi <= 150


def test_pm25_to_aqi_cap():
    """PM2.5 above 500.4 should cap at AQI 500."""
    aqi = pm25_to_aqi(600.0)
    assert aqi == 500


def test_pm25_to_aqi_negative():
    """Negative PM2.5 should return 0."""
    aqi = pm25_to_aqi(-5.0)
    assert aqi == 0
