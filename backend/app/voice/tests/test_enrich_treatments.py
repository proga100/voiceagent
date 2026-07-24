"""Growz Agroapteka preparations proxy: fuzzy match, dose fallback, dedupe,
cap, description truncation, and fail-open (mirrors test_enrich_crops.py)."""
import httpx
import pytest

from app.config import Settings
from app.voice.enrich import treatments

_KEY = Settings(growz_api_key="k")

_DISEASES = {"data": [
    {"id": "d1", "name": "Fitoftoroz", "biology_name": "Phytophthora infestans"},
    {"id": "d2", "name": "Un shudring"},
    {"id": "d3", "name": "Qoʻngʻir dogʻ"},
    {"id": "d4", "name": "Qora chirish"},
    {"id": "d5", "name": "Oq chirish"},
    {"id": "", "name": "NoId"},          # dropped: no id
    {"id": "d6", "name": ""},            # dropped: no name
]}

_WEEDS = {"data": [
    {"id": "w1", "name": "Qoʻgʻa"},
]}

_TREATMENTS = {"data": [
    {
        "type": "pest", "dose_min": 0.75, "dose_max": 1.0,
        "drug": {
            "name": "NURELL AGRO 55% EM.K", "unit": {"name": "l/ga"},
            "description": ("Keng taʼsir doirali insektitsid " + "x" * 320),
        },
    },
    {
        "type": "disease",
        "drug": {
            "name": "TOPAZ 10% EM.K", "dose_min": 0.3, "dose_max": 0.4,
            "unit": {"name": "l/ga"}, "description": "Fungitsid",
        },
    },
    {  # duplicate of TOPAZ (case/okina-insensitive) — dropped, first kept
        "type": "disease",
        "drug": {"name": "topaz 10% em.k", "unit": {}},
    },
    {"type": "", "drug": {"name": ""}},  # dropped: no drug name
]}

_MANY_TREATMENTS = {"data": [
    {"drug": {"name": f"DRUG {i}"}} for i in range(6)
]}


def _install(monkeypatch, handler):
    monkeypatch.setattr(
        treatments, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


@pytest.fixture(autouse=True)
async def _reset():
    await treatments.aclose()  # clear caches + client
    yield
    await treatments.aclose()


def _handler(diseases=_DISEASES, weeds=_WEEDS, treat=_TREATMENTS, capture=None):
    def handler(request):
        if capture is not None:
            capture.append(request)
        url = str(request.url)
        if "/api/ai/diseases" in url:
            assert request.headers["x-api-key"] == "k"
            assert request.url.params["limit"] == "5000"
            return httpx.Response(200, json=diseases)
        if "/api/ai/weeds" in url:
            assert request.headers["x-api-key"] == "k"
            assert request.url.params["limit"] == "5000"
            return httpx.Response(200, json=weeds)
        if "/api/ai/treatments" in url:
            assert "disease_id" in request.url.params
            assert "diseaseId" not in request.url.params
            assert "disease" not in request.url.params
            assert request.url.params["limit"] == "25"
            return httpx.Response(200, json=treat)
        raise AssertionError(f"unexpected url {request.url}")
    return handler


async def test_exact_match_returns_treatments_capped_and_deduped(monkeypatch):
    capture: list = []
    _install(monkeypatch, _handler(capture=capture))
    out = await treatments.find_preparations(_KEY, "Fitoftoroz", "disease_pest")
    assert [p["name"] for p in out] == ["NURELL AGRO 55% EM.K", "TOPAZ 10% EM.K"]
    treat_req = [r for r in capture if "/api/ai/treatments" in str(r.url)][0]
    assert treat_req.url.params["disease_id"] == "d1"


async def test_biology_name_match(monkeypatch):
    _install(monkeypatch, _handler())
    out = await treatments.find_preparations(
        _KEY, "Phytophthora infestans", "disease_pest"
    )
    assert out and out[0]["name"] == "NURELL AGRO 55% EM.K"


async def test_okina_variant_match(monkeypatch):
    _install(monkeypatch, _handler())
    # Straight apostrophes, as a farmer/Gemini transcript might render them;
    # catalogue entry d3 uses the proper okina — both normalize the same.
    out = await treatments.find_preparations(_KEY, "Qo'ng'ir dog'", "disease_pest")
    assert out  # matched d3, treatments fixture returned regardless of id


async def test_single_containment_match(monkeypatch):
    _install(monkeypatch, _handler())
    out = await treatments.find_preparations(_KEY, "shudring", "disease_pest")
    assert out  # "shudring" is contained only in "Un shudring" (d2)


async def test_ambiguous_containment_returns_empty(monkeypatch):
    _install(monkeypatch, _handler())
    # "chirish" is contained in BOTH "Qora chirish" (d4) and "Oq chirish" (d5).
    out = await treatments.find_preparations(_KEY, "chirish", "disease_pest")
    assert out == []


async def test_no_match_returns_empty(monkeypatch):
    _install(monkeypatch, _handler())
    out = await treatments.find_preparations(_KEY, "Nomaʼlum kasallik", "disease_pest")
    assert out == []


_CROP_DISEASES = {"data": [
    {"id": "c1", "name": "Pomidor fitoftorozi"},
    {"id": "c2", "name": "Kartoshka fitoftorozi"},
]}


async def test_crop_context_disambiguates_prefixed_names(monkeypatch):
    # Growz stores crop-prefixed names, so "fitoftoroz" hits BOTH — ambiguous
    # without a crop, but the known crop narrows it to exactly one.
    _install(monkeypatch, _handler(diseases=_CROP_DISEASES))
    assert await treatments.find_preparations(_KEY, "fitoftoroz", "disease_pest") == []
    scoped = await treatments.find_preparations(
        _KEY, "fitoftoroz", "disease_pest", crop_name="Pomidor"
    )
    assert scoped  # crop 'Pomidor' -> Pomidor fitoftorozi (c1)


async def test_verbose_name_parenthetical_stripped_then_crop_scoped(monkeypatch):
    # Real Gemini output is verbose ("Fitoftoroz (Kechki blight)"); the gloss is
    # stripped to the core term, then the crop narrows the crop-prefixed matches.
    _install(monkeypatch, _handler(diseases=_CROP_DISEASES))
    assert await treatments.find_preparations(
        _KEY, "Fitoftoroz (Kechki blight)", "disease_pest"
    ) == []  # verbose + no crop -> still ambiguous
    scoped = await treatments.find_preparations(
        _KEY, "Fitoftoroz (Kechki blight)", "disease_pest", crop_name="Pomidor"
    )
    assert scoped  # -> Pomidor fitoftorozi (c1)


async def test_crop_context_still_empty_when_crop_shared_by_two(monkeypatch):
    # A crop that still leaves two candidates stays empty (safe over wrong).
    diseases = {"data": [
        {"id": "p1", "name": "Pomidor ildiz chirishi"},
        {"id": "p2", "name": "Pomidor poya chirishi"},
    ]}
    _install(monkeypatch, _handler(diseases=diseases))
    out = await treatments.find_preparations(
        _KEY, "chirish", "disease_pest", crop_name="Pomidor"
    )
    assert out == []


async def test_find_preparations_by_id_direct_no_catalogue(monkeypatch):
    # The robust path: an exact id -> straight to /api/ai/treatments, NO
    # catalogue fetch, NO name matching.
    capture: list = []
    _install(monkeypatch, _handler(capture=capture))
    out = await treatments.find_preparations_by_id(_KEY, "xyz-123")
    assert [p["name"] for p in out] == ["NURELL AGRO 55% EM.K", "TOPAZ 10% EM.K"]
    urls = [str(r.url) for r in capture]
    assert any("disease_id=xyz-123" in u for u in urls)
    assert not any("/api/ai/diseases" in u for u in urls)  # no matching needed


async def test_find_preparations_by_id_empty_id_no_network(monkeypatch):
    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("empty id -> no network")
    _install(monkeypatch, handler)
    assert await treatments.find_preparations_by_id(_KEY, "  ") == []


async def test_find_preparations_by_id_http_error_empty(monkeypatch):
    _install(monkeypatch, lambda r: httpx.Response(502))
    assert await treatments.find_preparations_by_id(_KEY, "id-1") == []


async def test_get_crop_diseases_filters_by_crop_name(monkeypatch):
    diseases = {"data": [
        {"id": "a", "name": "Pomidor fitoftorozi"},
        {"id": "b", "name": "Pomidorda ildiz chirish"},
        {"id": "c", "name": "Kartoshkada fitoftoroz"},
    ]}
    _install(monkeypatch, _handler(diseases=diseases))
    out = await treatments.get_crop_diseases(_KEY, "Pomidor")
    assert {d["name"] for d in out} == {"Pomidor fitoftorozi", "Pomidorda ildiz chirish"}
    assert all(set(d) == {"id", "name"} for d in out)


async def test_get_crop_diseases_empty_crop_returns_empty(monkeypatch):
    _install(monkeypatch, _handler())
    assert await treatments.get_crop_diseases(_KEY, "") == []


async def test_weed_kind_hits_weeds_catalogue(monkeypatch):
    capture: list = []
    _install(monkeypatch, _handler(capture=capture))
    out = await treatments.find_preparations(_KEY, "Qoʻgʻa", "weed")
    assert out and out[0]["name"] == "NURELL AGRO 55% EM.K"
    urls = [str(r.url) for r in capture]
    assert any("/api/ai/weeds" in u for u in urls)
    assert not any("/api/ai/diseases" in u for u in urls)


async def test_dose_fallback_row_then_drug(monkeypatch):
    _install(monkeypatch, _handler())
    out = await treatments.find_preparations(_KEY, "Fitoftoroz", "disease_pest")
    nurell = next(p for p in out if p["name"] == "NURELL AGRO 55% EM.K")
    assert nurell["dose_min"] == 0.75 and nurell["dose_max"] == 1.0  # row-level
    topaz = next(p for p in out if p["name"] == "TOPAZ 10% EM.K")
    assert topaz["dose_min"] == 0.3 and topaz["dose_max"] == 0.4  # drug-level fallback


async def test_description_truncated_to_300_chars(monkeypatch):
    _install(monkeypatch, _handler())
    out = await treatments.find_preparations(_KEY, "Fitoftoroz", "disease_pest")
    nurell = next(p for p in out if p["name"] == "NURELL AGRO 55% EM.K")
    assert len(nurell["description"]) == 300


async def test_cap_at_four_preparations(monkeypatch):
    _install(monkeypatch, _handler(treat=_MANY_TREATMENTS))
    out = await treatments.find_preparations(_KEY, "Fitoftoroz", "disease_pest")
    assert len(out) == 4
    assert [p["name"] for p in out] == ["DRUG 0", "DRUG 1", "DRUG 2", "DRUG 3"]


async def test_all_six_frozen_fields_always_present(monkeypatch):
    _install(monkeypatch, _handler())
    out = await treatments.find_preparations(_KEY, "Fitoftoroz", "disease_pest")
    for p in out:
        assert set(p) == {"name", "dose_min", "dose_max", "unit", "type", "description"}


async def test_missing_key_returns_empty_without_call(monkeypatch):
    def handler(request):  # pragma: no cover - must not be reached
        raise AssertionError("should not hit network without a key")

    _install(monkeypatch, handler)
    out = await treatments.find_preparations(
        Settings(growz_api_key=""), "Fitoftoroz", "disease_pest"
    )
    assert out == []


async def test_catalogue_http_error_returns_empty(monkeypatch):
    _install(monkeypatch, lambda request: httpx.Response(502))
    out = await treatments.find_preparations(_KEY, "Fitoftoroz", "disease_pest")
    assert out == []


async def test_treatments_http_error_returns_empty(monkeypatch):
    def handler(request):
        if "/api/ai/diseases" in str(request.url):
            return httpx.Response(200, json=_DISEASES)
        return httpx.Response(500)

    _install(monkeypatch, handler)
    out = await treatments.find_preparations(_KEY, "Fitoftoroz", "disease_pest")
    assert out == []


async def test_network_error_returns_empty(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("down")

    _install(monkeypatch, handler)
    out = await treatments.find_preparations(_KEY, "Fitoftoroz", "disease_pest")
    assert out == []


async def test_catalogue_is_cached_across_calls(monkeypatch):
    calls = {"diseases": 0}

    def handler(request):
        if "/api/ai/diseases" in str(request.url):
            calls["diseases"] += 1
            return httpx.Response(200, json=_DISEASES)
        return httpx.Response(200, json=_TREATMENTS)

    _install(monkeypatch, handler)
    await treatments.find_preparations(_KEY, "Fitoftoroz", "disease_pest")
    await treatments.find_preparations(_KEY, "Un shudring", "disease_pest")
    assert calls["diseases"] == 1
