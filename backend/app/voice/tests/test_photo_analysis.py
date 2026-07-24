"""Per-image §5.4 triage (photo_analysis.py): parsing, order preservation and
the never-raises guarantee (per-slot None on failure). Mirrors the
fake-genai-client style of test_photo_select.py; the fake is keyed by the
photo bytes so parallel calls stay distinguishable."""
import asyncio

import pytest

from app.config import Settings
from app.voice.pipeline import photo_analysis
from app.voice.pipeline.photo_analysis import (
    PerImageAnalysis,
    analyze_selected_photos,
)
from app.voice.pipeline.tools import PhotoAttachment

_A = PerImageAnalysis(
    symptoms_seen=["sariq dogʻlar"], organ="leaf",
    likely_disease_hypotheses=["fitoftoroz"], likely_pest_hypotheses=[],
    nutrient_deficiency_suspected=False, agrotech_stress_suspected=False,
    confidence="high",
)
_B = PerImageAnalysis(
    symptoms_seen=["chirish"], organ="stem",
    likely_disease_hypotheses=[], likely_pest_hypotheses=["kuya"],
    nutrient_deficiency_suspected=True, agrotech_stress_suspected=True,
    confidence="low",
)


class _Resp:
    def __init__(self, text=None, parsed=None):
        self.text = text
        self.parsed = parsed


class _PerPhotoModels:
    """Maps the attached photo bytes -> response text / parsed / exception."""

    def __init__(self, mapping: dict):
        self._mapping = mapping

    async def generate_content(self, *, model, contents, config):
        data = contents[0].parts[0].inline_data.data
        result = self._mapping[data]
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, str):
            return _Resp(text=result)
        return _Resp(parsed=result)


class _Auth:
    def __init__(self, models):
        self._models = models

    def genai_client(self):
        aio = type("Aio", (), {"models": self._models})()
        return type("Client", (), {"aio": aio})()


def _photo(i: int) -> PhotoAttachment:
    return PhotoAttachment(photo_id=f"p{i}", data=f"data{i}".encode(), mime="image/jpeg")


async def test_empty_input_returns_empty_without_api_call():
    class _BoomModels:
        async def generate_content(self, **kwargs):
            raise AssertionError("must not call the API for an empty list")

    out = await analyze_selected_photos(Settings(), _Auth(_BoomModels()), [])
    assert out == []


async def test_parses_from_parsed_and_from_text_preserving_order():
    auth = _Auth(_PerPhotoModels({
        b"data0": _A,                        # via response.parsed
        b"data1": _B.model_dump_json(),      # via response.text JSON
    }))
    out = await analyze_selected_photos(Settings(), auth, [_photo(0), _photo(1)])
    assert out == [_A, _B]


async def test_one_failing_slot_degrades_to_none_others_survive():
    auth = _Auth(_PerPhotoModels({
        b"data0": _A,
        b"data1": RuntimeError("flash down"),
        b"data2": _B,
    }))
    out = await analyze_selected_photos(
        Settings(), auth, [_photo(0), _photo(1), _photo(2)]
    )
    assert out == [_A, None, _B]


async def test_garbage_json_degrades_that_slot_to_none():
    auth = _Auth(_PerPhotoModels({b"data0": "not json", b"data1": _A}))
    out = await analyze_selected_photos(Settings(), auth, [_photo(0), _photo(1)])
    assert out == [None, _A]


async def test_all_failing_yields_all_none_without_raising():
    auth = _Auth(_PerPhotoModels({
        b"data0": RuntimeError("a"), b"data1": RuntimeError("b"),
    }))
    out = await analyze_selected_photos(Settings(), auth, [_photo(0), _photo(1)])
    assert out == [None, None]


async def test_timeout_degrades_slot_to_none(monkeypatch):
    monkeypatch.setattr(photo_analysis, "PER_IMAGE_TIMEOUT_S", 0.05)

    class _SlowModels:
        async def generate_content(self, **kwargs):
            await asyncio.sleep(1)
            raise AssertionError("unreachable")

    out = await analyze_selected_photos(Settings(), _Auth(_SlowModels()), [_photo(0)])
    assert out == [None]


async def test_cancelled_error_propagates():
    auth = _Auth(_PerPhotoModels({b"data0": asyncio.CancelledError()}))
    with pytest.raises(asyncio.CancelledError):
        await analyze_selected_photos(Settings(), auth, [_photo(0)])
