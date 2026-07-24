"""Growz crop-catalogue proxy: shape mapping, sorting, caching, fail-open."""
import httpx
import pytest

from app.config import Settings
from app.voice.enrich import crops

_KEY = Settings(growz_api_key="k")

_SAMPLE = {"data": [
    {"id": "u2", "name": "Pomidor", "biology_name": "Solanum lycopersicum",
     "crop_category": {"id": "c1", "name": "Sabzavotlar"}},
    {"id": "u1", "name": "Bodring",
     "crop_category": {"id": "c1", "name": "Sabzavotlar"}},
    {"id": "u3", "name": "Bugʻdoy",
     "crop_category": {"id": "c2", "name": "Donli ekinlar"}},
    {"id": "", "name": "NoId"},          # dropped: no id
    {"id": "u4", "name": ""},            # dropped: no name
]}


@pytest.fixture(autouse=True)
async def _reset():
    await crops.aclose()  # clear cache + client
    yield
    await crops.aclose()


def _install(monkeypatch, handler):
    monkeypatch.setattr(
        crops, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


async def test_maps_shape_and_sorts_by_category_then_name(monkeypatch):
    def handler(request):
        assert request.headers["x-api-key"] == "k"
        assert request.url.params["limit"] == "1000"
        return httpx.Response(200, json=_SAMPLE)

    _install(monkeypatch, handler)
    out = await crops.get_crops(_KEY)
    # Two invalid rows dropped; sorted (Donli < Sabzavotlar, then by name).
    assert [c["name"] for c in out] == ["Bugʻdoy", "Bodring", "Pomidor"]
    assert out[0] == {
        "id": "u3", "name": "Bugʻdoy", "biology_name": "",
        "category": "Donli ekinlar",
    }


async def test_result_is_cached(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=_SAMPLE)

    _install(monkeypatch, handler)
    await crops.get_crops(_KEY)
    await crops.get_crops(_KEY)
    assert calls["n"] == 1  # second call served from cache


async def test_missing_key_returns_empty_without_call(monkeypatch):
    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("should not hit network without a key")

    _install(monkeypatch, handler)
    assert await crops.get_crops(Settings(growz_api_key="")) == []


async def test_http_error_returns_empty(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(502))
    assert await crops.get_crops(_KEY) == []


async def test_network_error_returns_empty(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("down")

    _install(monkeypatch, handler)
    assert await crops.get_crops(_KEY) == []
