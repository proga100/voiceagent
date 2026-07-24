"""Weather fetch: Open-Meteo shape parsing, WMO->Uzbek mapping, fail-open."""
import httpx
import pytest

from app.config import Settings
from app.voice.enrich import weather


@pytest.fixture(autouse=True)
async def _reset_client():
    """Give each test a fresh module client; close it afterwards."""
    await weather.aclose()
    yield
    await weather.aclose()


def _install(monkeypatch, handler):
    monkeypatch.setattr(
        weather, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_parses_current_and_maps_sky(monkeypatch):
    def handler(request):
        assert request.url.params["timezone"] == "Asia/Tashkent"
        return httpx.Response(200, json={"current": {
            "temperature_2m": 38.3, "apparent_temperature": 36.3,
            "relative_humidity_2m": 17, "wind_speed_10m": 11.5,
            "precipitation": 0.0, "weather_code": 2,
        }})

    _install(monkeypatch, handler)
    w = await weather.fetch_weather(Settings(), 41.3, 69.2)
    assert w["temp_c"] == 38.3
    assert w["humidity"] == 17
    assert w["sky"] == "yarim bulutli"  # WMO code 2


async def test_unknown_weather_code_gives_empty_sky(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"current": {
            "temperature_2m": 20.0, "weather_code": 12345,
        }})

    _install(monkeypatch, handler)
    w = await weather.fetch_weather(Settings(), 41.3, 69.2)
    assert w["sky"] == ""


async def test_http_error_returns_none(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(500))
    assert await weather.fetch_weather(Settings(), 41.3, 69.2) is None


async def test_missing_temperature_returns_none(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(200, json={"current": {}}))
    assert await weather.fetch_weather(Settings(), 41.3, 69.2) is None


async def test_network_error_returns_none(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("no route")

    _install(monkeypatch, handler)
    assert await weather.fetch_weather(Settings(), 41.3, 69.2) is None
