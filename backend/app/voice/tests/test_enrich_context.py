"""Enrichment context block: Uzbek date formatting + optional weather/crop lines."""
from datetime import date

from app.config import Settings
from app.voice.enrich import context
from app.voice.enrich.context import build_enrichment_context, build_session_enrichment


def test_uz_date_format():
    # 2026-07-11 is a Saturday.
    assert context._uz_date(date(2026, 7, 11)) == "11-iyul 2026-yil, shanba"
    assert context._uz_date(date(2026, 1, 1)) == "1-yanvar 2026-yil, payshanba"


def test_block_has_all_parts_when_weather_and_crop_present():
    w = {"temp_c": 38.3, "humidity": 17, "wind_kmh": 11.5, "sky": "yarim bulutli"}
    block = build_enrichment_context("Pomidor", w, date(2026, 7, 11))
    assert block.startswith("[BUGUNGI SHAROIT] Bugun 11-iyul 2026-yil, shanba.")
    assert "38°C" in block  # rounded from 38.3
    assert "yarim bulutli" in block
    assert "namlik 17%" in block
    assert "shamol 12 km/soat" in block  # rounded from 11.5
    assert "«Pomidor»" in block


def test_block_drops_weather_line_when_absent():
    block = build_enrichment_context("Bodring", None, date(2026, 7, 11))
    assert "Ob-havo" not in block
    assert "«Bodring»" in block


def test_block_is_date_only_when_nothing_else():
    block = build_enrichment_context(None, None, date(2026, 7, 11))
    assert "Ob-havo" not in block
    assert "ekini haqida" not in block
    assert "11-iyul" in block


def test_partial_weather_omits_missing_fields():
    # Only temperature present — no humidity/wind/sky sub-clauses.
    block = build_enrichment_context(None, {"temp_c": 5.0}, date(2026, 7, 11))
    assert "5°C" in block
    assert "namlik" not in block and "shamol" not in block


def test_block_includes_saved_planting_profile_facts():
    profile = {
        "planted_date": "2026-03-19", "days_since_planting": 118,
        "growth_period_days": "60-90", "region": "Yuqori Chirchiq",
        "field": {"name": "gwyw", "area": 0.19, "unit": "sotix"},
        "current_agrotech_task": "Mineral o'git bilan oziqlantirish",
    }
    block = build_enrichment_context("Achchiq qalampir", None, date(2026, 7, 15), profile)
    assert "Fermer profili (Growz):" in block
    assert "ekilgan sana 2026-03-19 (118 kun oldin)" in block
    assert "hudud Yuqori Chirchiq" in block
    assert "dala «gwyw» 0.19 sotix" in block
    assert "Mineral o'git bilan oziqlantirish" in block


async def test_session_enrichment_pulls_profile_from_mock(monkeypatch):
    # Saved crop -> the planting-profile facts are injected into the block.
    # (The DETAIL mock has no GPS, so weather uses the Tashkent default.)
    seen = {}

    async def _fake_weather(settings, lat, lon):
        seen["lat"], seen["lon"] = lat, lon
        return {"temp_c": 39, "sky": "ochiq"}

    monkeypatch.setattr(context, "fetch_weather", _fake_weather)
    s = Settings(
        tenant_crops_source="mock", weather_default_lat=41.3, weather_default_lon=69.2
    )
    block = await build_session_enrichment(s, "Achchiq qalampir", None, None)
    assert "Fermer profili (Growz):" in block
    assert "dala «gwyw»" in block
    assert seen == {"lat": 41.3, "lon": 69.2}


async def test_session_enrichment_uses_planting_gps_when_present(monkeypatch):
    # LIST-shape profile with GPS -> the planting's GPS drives weather when the
    # phone sent none. (Uses a stubbed planting so it's independent of the mock.)
    seen = {}

    async def _fake_weather(settings, lat, lon):
        seen["lat"], seen["lon"] = lat, lon
        return None

    async def _fake_planting(settings, crop_name, user_id=None):
        return {"crop": {"translations": [{"name": crop_name}]},
                "farmer": {"location": {"coordinates": [40.5, 71.1]}}}

    monkeypatch.setattr(context, "fetch_weather", _fake_weather)
    monkeypatch.setattr(context, "get_tenant_planting", _fake_planting)
    await build_session_enrichment(Settings(), "Anor", None, None)
    assert seen == {"lat": 40.5, "lon": 71.1}


async def test_session_enrichment_uses_default_coords_when_gps_absent(monkeypatch):
    seen = {}

    async def _fake_weather(settings, lat, lon):
        seen["lat"], seen["lon"] = lat, lon
        return {"temp_c": 10, "sky": "ochiq havo"}

    monkeypatch.setattr(context, "fetch_weather", _fake_weather)
    s = Settings(weather_default_lat=41.3, weather_default_lon=69.2)
    block = await build_session_enrichment(s, "Uzum", None, None)
    assert seen == {"lat": 41.3, "lon": 69.2}  # fell back to Tashkent
    assert "«Uzum»" in block and "10°C" in block


async def test_session_enrichment_passes_gps_when_present(monkeypatch):
    seen = {}

    async def _fake_weather(settings, lat, lon):
        seen["lat"], seen["lon"] = lat, lon
        return None

    monkeypatch.setattr(context, "fetch_weather", _fake_weather)
    await build_session_enrichment(Settings(), "Olma", 40.1, 71.7)
    assert seen == {"lat": 40.1, "lon": 71.7}


async def test_session_enrichment_skips_weather_when_disabled(monkeypatch):
    called = False

    async def _fake_weather(settings, lat, lon):
        nonlocal called
        called = True
        return {"temp_c": 1}

    monkeypatch.setattr(context, "fetch_weather", _fake_weather)
    block = await build_session_enrichment(
        Settings(enrich_weather_enabled=False), "Paxta", 40.0, 70.0
    )
    assert called is False
    assert "Ob-havo" not in block and "«Paxta»" in block
