"""finalize_session_memory: extraction merge, fail-open, caps, phone linking."""
import asyncio

import pytest

from app.config import Settings
from app.voice.pipeline.memory import (
    FarmerProfile,
    MemoryStore,
    OpenIssue,
    ProfileUpdate,
    finalize_session_memory,
)

DEV = "11111111-2222-3333-4444-555555555555"

TRANSCRIPT = (
    "FERMER: Assalomu alaykum, men Karim akaman.\n"
    "RAIS: Assalomu alaykum Karim aka! Qanday yordam beray?\n"
    "FERMER: Pomidorimning barglari dogʻ boʻlib qoldi.\n"
)


class _Models:
    def __init__(self, payload: str | None, capture: dict | None = None,
                 raise_exc: bool = False, delay: float = 0.0):
        self._payload = payload
        self._capture = capture if capture is not None else {}
        self._raise = raise_exc
        self._delay = delay
        self.calls = 0

    async def generate_content(self, *, model, contents, config):
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raise:
            raise RuntimeError("boom")
        self._capture["model"] = model
        self._capture["contents"] = contents

        class _Resp:
            text = self._payload
            parsed = None

        return _Resp()


class _Auth:
    def __init__(self, models):
        self._models = models

    def genai_client(self):
        class _C:
            pass

        c = _C()
        c.aio = type("A", (), {})()
        c.aio.models = self._models
        return c


def _settings(tmp_path) -> Settings:
    return Settings(memory_dir=str(tmp_path / "memory"))


def _run(settings, auth, store, key, profile, transcript, diagnosis=None):
    asyncio.run(finalize_session_memory(
        settings, auth, store, DEV, key, profile, transcript, diagnosis
    ))


def test_extraction_merges_facts(tmp_path):
    settings = _settings(tmp_path)
    store = MemoryStore(settings)
    payload = ProfileUpdate(
        name="Karim", region="Fargʻona", crops_add=["pomidor"],
        open_issue_problem="pomidor bargida dogʻ",
    ).model_dump_json()
    _run(settings, _Auth(_Models(payload)), store, f"dev:{DEV}", None, TRANSCRIPT)

    key, p = store.load_for_device(DEV)
    assert key == f"dev:{DEV}"
    assert p.name == "Karim" and p.region == "Fargʻona"
    assert p.crops == ["pomidor"]
    assert p.open_issue.problem == "pomidor bargida dogʻ"
    assert p.sessions_count == 1 and p.last_seen


def test_confirmed_phone_links_profile(tmp_path):
    settings = _settings(tmp_path)
    store = MemoryStore(settings)
    payload = ProfileUpdate(name="Karim", phone="91 312 45 67").model_dump_json()
    _run(settings, _Auth(_Models(payload)), store, f"dev:{DEV}", None, TRANSCRIPT)

    key, p = store.load_for_device(DEV)
    assert key == "phone:998913124567"
    assert p.phone == "998913124567"


def test_extraction_failure_keeps_deterministic_update(tmp_path):
    settings = _settings(tmp_path)
    store = MemoryStore(settings)
    profile = FarmerProfile(name="Karim", sessions_count=3)
    diagnosis = {"disease": "fitoftoroz", "confidence": "high", "date": "2026-07-05"}
    _run(settings, _Auth(_Models(None, raise_exc=True)), store,
         f"dev:{DEV}", profile, TRANSCRIPT, diagnosis)

    _, p = store.load_for_device(DEV)
    assert p.sessions_count == 4          # bumped despite LLM failure
    assert p.last_seen
    assert p.open_issue.diagnosis == "fitoftoroz"
    assert p.open_issue.status == "davolanmoqda"


def test_trivial_transcript_skips_llm(tmp_path):
    settings = _settings(tmp_path)
    store = MemoryStore(settings)
    models = _Models(ProfileUpdate().model_dump_json())
    _run(settings, _Auth(models), store, f"dev:{DEV}", None, "FERMER: alo")

    assert models.calls == 0              # too short — no extraction call
    _, p = store.load_for_device(DEV)
    assert p is not None and p.sessions_count == 1   # deterministic part saved


def test_caps_enforced(tmp_path):
    settings = _settings(tmp_path)
    store = MemoryStore(settings)
    payload = ProfileUpdate(
        crops_add=[f"ekin{i}" for i in range(9)],           # > 5 per session
        notes_add=["a" * 500, "b", "c", "d"],               # > 2, over-long
    ).model_dump_json()
    profile = FarmerProfile(crops=[f"bor{i}" for i in range(13)])
    _run(settings, _Auth(_Models(payload)), store, f"dev:{DEV}", profile, TRANSCRIPT)

    _, p = store.load_for_device(DEV)
    assert len(p.crops) <= 15
    assert p.crops[:13] == [f"bor{i}" for i in range(13)]   # existing kept
    assert len(p.notes) == 2
    assert all(len(n) <= 120 for n in p.notes)


def test_resolved_issue_marked(tmp_path):
    settings = _settings(tmp_path)
    store = MemoryStore(settings)
    payload = ProfileUpdate(open_issue_resolved=True).model_dump_json()
    profile = FarmerProfile(
        open_issue=OpenIssue(problem="shira", status="davolanmoqda")
    )
    _run(settings, _Auth(_Models(payload)), store, f"dev:{DEV}", profile, TRANSCRIPT)

    _, p = store.load_for_device(DEV)
    assert p.open_issue.status == "hal_bolgan"


def test_never_raises_even_if_store_broken(tmp_path):
    settings = _settings(tmp_path)
    store = MemoryStore(settings)
    # Simulate a store whose save explodes — finalize must swallow it.
    def _boom(*a, **k):
        raise OSError("disk full")
    store.save = _boom  # type: ignore[method-assign]
    _run(settings, _Auth(_Models(ProfileUpdate().model_dump_json())),
         store, f"dev:{DEV}", None, TRANSCRIPT)   # no exception = pass


def test_transcript_saved_for_review(tmp_path):
    """The raw conversation lands in transcripts/<key>/<day>.txt, sessions appended."""
    settings = _settings(tmp_path)
    store = MemoryStore(settings)
    payload = ProfileUpdate(name="Karim").model_dump_json()
    _run(settings, _Auth(_Models(payload)), store, f"dev:{DEV}", None, TRANSCRIPT)
    _run(settings, _Auth(_Models(payload)), store, f"dev:{DEV}", None, TRANSCRIPT)

    day_dir = tmp_path / "memory" / "transcripts" / f"dev:{DEV}"
    files = list(day_dir.glob("*.txt"))
    assert len(files) == 1                              # same day → one file
    body = files[0].read_text(encoding="utf-8")
    assert body.count("=== session ") == 2              # two sessions appended
    assert "FERMER: Assalomu alaykum, men Karim akaman." in body
    assert "RAIS:" in body


def test_transcript_saved_even_when_extraction_fails(tmp_path):
    settings = _settings(tmp_path)
    store = MemoryStore(settings)
    _run(settings, _Auth(_Models(None, raise_exc=True)), store,
         f"dev:{DEV}", None, TRANSCRIPT)

    day_dir = tmp_path / "memory" / "transcripts" / f"dev:{DEV}"
    assert list(day_dir.glob("*.txt")), "transcript must survive LLM failure"


def test_empty_transcript_not_saved(tmp_path):
    settings = _settings(tmp_path)
    store = MemoryStore(settings)
    _run(settings, _Auth(_Models(None)), store, f"dev:{DEV}", None, "   ")

    assert not (tmp_path / "memory" / "transcripts").exists()
