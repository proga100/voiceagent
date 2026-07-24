"""Current weather for the farmer's field — Open-Meteo (free, no API key).

Given a lat/lon (the phone GPS, or the Tashkent fallback) returns a tiny summary:
temperature, feels-like, humidity, wind, precipitation, and a short Uzbek sky
description mapped from the WMO weather code. Fail-open: any error returns
``None`` and the conversation simply carries no weather line.
"""
from __future__ import annotations

import logging

import httpx

from app.config import Settings

logger = logging.getLogger("voice.enrich.weather")

# Shared keep-alive client. A short timeout is deliberate — this runs on the
# session-start path, before the Live connection opens, so a slow weather API
# must not stall the call; it degrades to "no weather line" instead.
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(6.0))
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None

# WMO weather-code -> short Uzbek sky description.
_WMO_UZ: dict[int, str] = {
    0: "ochiq havo",
    1: "asosan ochiq", 2: "yarim bulutli", 3: "bulutli",
    45: "tuman", 48: "qirovli tuman",
    51: "yengil shivalama", 53: "shivalama", 55: "kuchli shivalama",
    56: "muzlagan shivalama", 57: "kuchli muzlagan shivalama",
    61: "yengil yomgʻir", 63: "yomgʻir", 65: "kuchli yomgʻir",
    66: "muzlagan yomgʻir", 67: "kuchli muzlagan yomgʻir",
    71: "yengil qor", 73: "qor", 75: "kuchli qor", 77: "qor donachalari",
    80: "yengil jala", 81: "jala", 82: "kuchli jala",
    85: "qor jalasi", 86: "kuchli qor jalasi",
    95: "momaqaldiroq", 96: "doʻl bilan momaqaldiroq",
    99: "kuchli doʻl bilan momaqaldiroq",
}


async def fetch_weather(
    settings: Settings, lat: float, lon: float
) -> dict | None:
    """Current weather at ``(lat, lon)`` or ``None`` on any failure."""
    try:
        resp = await _http().get(
            settings.open_meteo_url,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": (
                    "temperature_2m,relative_humidity_2m,apparent_temperature,"
                    "precipitation,weather_code,wind_speed_10m"
                ),
                "timezone": "Asia/Tashkent",
            },
        )
        resp.raise_for_status()
        cur = resp.json().get("current", {})
    except Exception:  # noqa: BLE001 - fail-open: weather is optional
        logger.warning("weather fetch failed — no weather line", exc_info=True)
        return None

    if not cur or cur.get("temperature_2m") is None:
        return None
    return {
        "temp_c": cur.get("temperature_2m"),
        "feels_c": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind_kmh": cur.get("wind_speed_10m"),
        "precip_mm": cur.get("precipitation"),
        "sky": _WMO_UZ.get(cur.get("weather_code"), ""),
    }
