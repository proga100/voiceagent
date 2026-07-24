"""Unit tests for the profile-crop chip source (enrich/tenant_crops.py)."""
import json
from datetime import date

import pytest

from app.config import Settings
from app.voice.enrich.tenant_crops import (
    build_planting_profile,
    get_tenant_crops,
    get_tenant_planting,
)

pytestmark = pytest.mark.asyncio


async def test_mock_is_real_plantings_response():
    # The mock is a REAL GET /api/tenant/plantings capture: crop name lives in
    # crop.translations[].name and crop.id is the /api/ai/crops UUID.
    crops = await get_tenant_crops(Settings(tenant_crops_source="mock"))
    assert [c["name"] for c in crops] == ["Achchiq qalampir"]
    assert crops[0]["id"] == "75412c1b-14e9-4c1c-b7de-eb74e4bf39b9"


async def test_plantings_translations_prefer_uz_latin(tmp_path):
    p = tmp_path / "tr.json"
    p.write_text(json.dumps({"data": [
        {"crop": {"id": "cid", "translations": [
            {"language": "6157b497-cc07-420c-881f-fb316f5b3a38", "name": "Помидор"},
            {"language": "40d15260-93c9-4f92-a56a-c25a28e4b732", "name": "Pomidor"},
        ]}},
    ]}), encoding="utf-8")
    crops = await get_tenant_crops(
        Settings(tenant_crops_source="mock", tenant_crops_mock_path=str(p))
    )
    # Latin Uzbek is chosen (matches /api/ai/crops + what Rais speaks), not Cyrillic.
    assert crops == [{"id": "cid", "name": "Pomidor"}]


async def test_off_source_returns_empty():
    assert await get_tenant_crops(Settings(tenant_crops_source="off")) == []


async def test_get_tenant_planting_finds_by_name():
    p = await get_tenant_planting(Settings(tenant_crops_source="mock"), "Achchiq qalampir")
    assert p is not None
    assert p["crop"]["id"] == "75412c1b-14e9-4c1c-b7de-eb74e4bf39b9"


async def test_get_tenant_planting_none_for_unknown():
    assert await get_tenant_planting(Settings(tenant_crops_source="mock"), "Pomidor") is None
    assert await get_tenant_planting(Settings(tenant_crops_source="off"), "Achchiq qalampir") is None


async def test_build_planting_profile_from_detail_mock():
    # The DETAIL shape (GET /api/tenant/plantings/{id}): crop, dates, growth
    # period, field and the current task (from activityTask.description). Region
    # and GPS are id-only/absent in this shape, so they're omitted.
    p = await get_tenant_planting(Settings(tenant_crops_source="mock"), "Achchiq qalampir")
    profile = build_planting_profile(p, date(2026, 7, 15))
    assert profile["crop"] == "Achchiq qalampir"
    assert profile["planted_date"] == "2026-03-19"
    assert profile["days_since_planting"] == 118
    assert profile["growth_period_days"] == "60-90"
    assert profile["field"] == {"name": "gwyw", "area": 0.19}
    assert profile["current_agrotech_task"].startswith("Qalampirning ildiz tizimi")
    assert "region" not in profile and "location" not in profile


async def test_build_planting_profile_list_shape_has_region_gps_task():
    # The LIST shape (district name, farmer GPS, short activityType.name) is
    # still parsed — build_planting_profile handles both.
    record = {
        "crop": {"translations": [{"name": "Pomidor"}]},
        "plantedDate": "2026-04-01T00:00:00.000Z",
        "field": {"name": "F1", "district": {"translations": [{"name": "Chirchiq"}]}},
        "area": 1.5,
        "plantingUnit": {"key": "ga"},
        "farmer": {"location": {"coordinates": [41.1, 69.2]}},
        "currentTask": {"activityType": {"translations": [{"name": "Sug'orish"}]}},
    }
    profile = build_planting_profile(record, date(2026, 7, 15))
    assert profile["region"] == "Chirchiq"
    assert profile["location"] == {"lat": 41.1, "lon": 69.2}
    assert profile["current_agrotech_task"] == "Sug'orish"
    assert profile["field"] == {"name": "F1", "area": 1.5, "unit": "ga"}


async def test_build_planting_profile_omits_missing():
    profile = build_planting_profile({"crop": {"translations": [{"name": "Olma"}]}}, date(2026, 7, 15))
    assert profile == {"crop": "Olma"}  # no dates/region/field/task -> omitted
    assert build_planting_profile({}, date(2026, 7, 15)) == {}


async def test_api_source_stubbed_empty_until_unblocked():
    # The real GET /api/tenant/crops is behind the Growz OTP-verify 500 blocker.
    assert await get_tenant_crops(Settings(tenant_crops_source="api")) == []


async def test_bad_mock_path_fails_open():
    s = Settings(tenant_crops_source="mock", tenant_crops_mock_path="/no/such/file.json")
    assert await get_tenant_crops(s) == []


async def test_caps_at_four(tmp_path):
    p = tmp_path / "many.json"
    p.write_text(json.dumps({"data": [
        {"cropId": f"id{i}", "cropName": f"Crop{i}"} for i in range(9)
    ]}), encoding="utf-8")
    crops = await get_tenant_crops(
        Settings(tenant_crops_source="mock", tenant_crops_mock_path=str(p))
    )
    assert len(crops) == 4


async def test_dedupes_and_skips_nameless(tmp_path):
    p = tmp_path / "dupe.json"
    p.write_text(json.dumps({"data": [
        {"cropId": "a", "cropName": "Anjir"},
        {"cropId": "b", "cropName": "Anjir"},   # dupe name -> dropped
        {"cropId": "c", "name": ""},             # nameless -> dropped
        {"cropId": "d", "name": "Olma"},         # alt "name" key accepted
    ]}), encoding="utf-8")
    crops = await get_tenant_crops(
        Settings(tenant_crops_source="mock", tenant_crops_mock_path=str(p))
    )
    assert [c["name"] for c in crops] == ["Anjir", "Olma"]
