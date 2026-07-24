"""Upload-time photo verification: schema, prompt wiring, and fail-open."""
import asyncio

import pytest

from app.config import Settings
from app.voice.pipeline.photo_check import PhotoVerdict, verify_photo


class _Models:
    def __init__(self, payload: str, capture: dict, raise_exc: bool = False):
        self._payload = payload
        self._capture = capture
        self._raise = raise_exc

    async def generate_content(self, *, model, contents, config):
        if self._raise:
            raise RuntimeError("boom")
        self._capture["model"] = model
        self._capture["contents"] = contents
        self._capture["config"] = config
        class _Resp:
            text = self._payload
            parsed = None
        return _Resp()


class _Auth:
    def __init__(self, models):
        self._models = models

    def genai_client(self):
        class _C:
            def __init__(s):
                s.aio = type("A", (), {"models": None})()
        c = _C()
        c.aio.models = self._models
        return c


def test_valid_leaf_verdict_parses():
    capture: dict = {}
    payload = PhotoVerdict(
        is_plant=True, seen_part="leaf", matches_target=True
    ).model_dump_json()
    auth = _Auth(_Models(payload, capture))
    v = asyncio.run(verify_photo(Settings(), auth, b"img", "image/jpeg", "leaf"))
    assert v.is_plant and v.seen_part == "leaf" and v.matches_target
    assert not v.unverified
    assert capture["model"] == Settings().gemini_model


def test_non_plant_verdict():
    payload = PhotoVerdict(
        is_plant=False, seen_part="none", matches_target=False
    ).model_dump_json()
    v = asyncio.run(
        verify_photo(Settings(), _Auth(_Models(payload, {})), b"x", "image/png", "fruit")
    )
    assert not v.is_plant and v.seen_part == "none"


def test_error_fails_open_as_unverified():
    v = asyncio.run(
        verify_photo(
            Settings(), _Auth(_Models("", {}, raise_exc=True)), b"x", "image/png", "leaf"
        )
    )
    assert v.unverified and v.is_plant  # accepted, marked unverified


# ---- §4 additions: the three coarse VLM booleans ----------------------------


def test_new_quality_booleans_parse_from_json():
    payload = PhotoVerdict(
        is_plant=True, seen_part="leaf", matches_target=True,
        symptom_visible=False, multiple_plants=True, quality_ok=False,
    ).model_dump_json()
    v = asyncio.run(
        verify_photo(Settings(), _Auth(_Models(payload, {})), b"x", "image/jpeg", "leaf")
    )
    assert v.symptom_visible is False
    assert v.multiple_plants is True
    assert v.quality_ok is False


def test_legacy_json_without_new_keys_gets_neutral_defaults():
    # A pre-§4 model response (or cached parse) misses the three new keys —
    # they must default to the neutral values, never reject/low-flag a photo.
    legacy = '{"is_plant": true, "seen_part": "fruit", "matches_target": false}'
    v = asyncio.run(
        verify_photo(Settings(), _Auth(_Models(legacy, {})), b"x", "image/jpeg", "leaf")
    )
    assert v.symptom_visible is True
    assert v.multiple_plants is False
    assert v.quality_ok is True


def test_fallback_unverified_verdict_has_neutral_flags():
    v = asyncio.run(
        verify_photo(
            Settings(), _Auth(_Models("", {}, raise_exc=True)), b"x", "image/png", "leaf"
        )
    )
    assert v.unverified
    assert v.symptom_visible is True
    assert v.multiple_plants is False
    assert v.quality_ok is True


def test_verify_prompt_mentions_the_three_new_criteria():
    from app.voice.pipeline.photo_check import _VERIFY_SYSTEM_PROMPT

    assert "symptom_visible" in _VERIFY_SYSTEM_PROMPT
    assert "multiple_plants" in _VERIFY_SYSTEM_PROMPT
    assert "quality_ok" in _VERIFY_SYSTEM_PROMPT
