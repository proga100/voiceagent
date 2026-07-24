"""Photo ranking before diagnosis: passthrough, ranking selection/validation,
fail-open on garbage/errors, and no-mutation of the input list (mirrors the
fake-genai-client style of test_diagnosis.py)."""
import asyncio

import pytest

from app.config import Settings
from app.voice.pipeline import photo_select
from app.voice.pipeline.photo_select import PhotoRanking, select_best_photos
from app.voice.pipeline.tools import PhotoAttachment


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _Models:
    """Records the generate_content call; returns ``text`` or raises ``exc``."""

    def __init__(self, text: str | None = None, exc: Exception | None = None, capture=None) -> None:
        self._text = text
        self._exc = exc
        self._capture = capture if capture is not None else {}

    async def generate_content(self, *, model, contents, config):
        self._capture["model"] = model
        self._capture["contents"] = contents
        self._capture["config"] = config
        if self._exc is not None:
            raise self._exc
        return _Resp(self._text)


class _Auth:
    def __init__(self, models: _Models) -> None:
        self._models = models

    def genai_client(self):
        aio = type("Aio", (), {"models": self._models})()
        return type("Client", (), {"aio": aio})()


def _photos(n: int) -> list[PhotoAttachment]:
    return [
        PhotoAttachment(photo_id=f"p{i}", data=f"data{i}".encode(), mime="image/jpeg")
        for i in range(n)
    ]


async def test_passthrough_when_len_leq_max_n_no_api_call():
    class _BoomModels:
        async def generate_content(self, **kwargs):
            raise AssertionError("must not call the API when len <= max_n")

    auth = _Auth(_BoomModels())
    photos = _photos(3)
    out = await select_best_photos(Settings(), auth, photos, max_n=3)
    assert out == photos


async def test_passthrough_when_fewer_than_max_n():
    class _BoomModels:
        async def generate_content(self, **kwargs):
            raise AssertionError("must not call the API when len < max_n")

    auth = _Auth(_BoomModels())
    photos = _photos(2)
    out = await select_best_photos(Settings(), auth, photos, max_n=3)
    assert out == photos


async def test_valid_ranking_selects_and_returns_original_capture_order():
    capture: dict = {}
    ranking = PhotoRanking(chosen=[3, 0])  # model's "best first" order
    auth = _Auth(_Models(ranking.model_dump_json(), capture=capture))
    photos = _photos(5)
    out = await select_best_photos(Settings(), auth, photos, max_n=3)
    # original capture order (0 before 3), NOT the model's best-first order.
    assert out == [photos[0], photos[3]]
    assert capture["model"] == Settings().gemini_model
    parts = capture["contents"][0].parts
    assert len(parts) == 10  # 2 parts (label + inline_data) per photo x 5


async def test_out_of_range_and_duplicate_indices_dropped():
    ranking = PhotoRanking(chosen=[1, 1, 99, -1, 2])
    auth = _Auth(_Models(ranking.model_dump_json()))
    photos = _photos(5)
    out = await select_best_photos(Settings(), auth, photos, max_n=3)
    assert out == [photos[1], photos[2]]


async def test_empty_chosen_falls_back_to_first_max_n():
    ranking = PhotoRanking(chosen=[])
    auth = _Auth(_Models(ranking.model_dump_json()))
    photos = _photos(5)
    out = await select_best_photos(Settings(), auth, photos, max_n=3)
    assert out == photos[:3]


async def test_all_out_of_range_falls_back_to_first_max_n():
    ranking = PhotoRanking(chosen=[42, 99])
    auth = _Auth(_Models(ranking.model_dump_json()))
    photos = _photos(5)
    out = await select_best_photos(Settings(), auth, photos, max_n=3)
    assert out == photos[:3]


async def test_garbage_json_falls_back_to_first_max_n():
    auth = _Auth(_Models("not json"))
    photos = _photos(5)
    out = await select_best_photos(Settings(), auth, photos, max_n=3)
    assert out == photos[:3]


async def test_raised_exception_falls_back_to_first_max_n():
    auth = _Auth(_Models(exc=RuntimeError("boom")))
    photos = _photos(4)
    out = await select_best_photos(Settings(), auth, photos, max_n=3)
    assert out == photos[:3]


async def test_timeout_falls_back_to_first_max_n(monkeypatch):
    monkeypatch.setattr(photo_select, "RANKING_TIMEOUT_S", 0.05)

    class _SlowModels:
        async def generate_content(self, **kwargs):
            await asyncio.sleep(1)
            raise AssertionError("unreachable")

    auth = _Auth(_SlowModels())
    photos = _photos(4)
    out = await select_best_photos(Settings(), auth, photos, max_n=3)
    assert out == photos[:3]


async def test_cancelled_error_propagates():
    auth = _Auth(_Models(exc=asyncio.CancelledError()))
    photos = _photos(4)
    with pytest.raises(asyncio.CancelledError):
        await select_best_photos(Settings(), auth, photos, max_n=3)


async def test_input_list_never_mutated():
    ranking = PhotoRanking(chosen=[0, 1])
    auth = _Auth(_Models(ranking.model_dump_json()))
    photos = _photos(5)
    original = list(photos)
    await select_best_photos(Settings(), auth, photos, max_n=3)
    assert photos == original
