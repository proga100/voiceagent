"""AI senior-agronom second opinion (contract addendum P3.5):
``review_diagnosis`` genai wiring (fake client, mirrors test_diagnosis.py),
``_review_input`` thin/full last_diagnosis tolerance, the runner's happy
path / fail-open paths, and ``maybe_start_mock_review`` gating."""
import pytest

from app.config import Settings
from app.voice.agronom import review as agronom_review
from app.voice.agronom.review import (
    ExpertReview,
    _review_input,
    _run_mock_review,
    maybe_start_mock_review,
    review_diagnosis,
)
from app.voice.chat.models import AgronomReview, ChatDoc
from app.voice.chat.store import ChatStore

DEV = "11111111-2222-3333-4444-555555555555"

_REVIEW = ExpertReview(
    verdict="confirmed",
    expert_summary="Tashxis toʻgʻri, davolashni davom ettiring.",
    expert_notes=["Ertalab salqinda ishlov bering."],
    keep_preparations=True,
    adjusted_growz_disease_id="",
)


# ---- review_diagnosis genai wiring (mirrors test_diagnosis.py) ------------


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _Models:
    def __init__(self, text: str, capture: dict) -> None:
        self._text = text
        self._capture = capture

    async def generate_content(self, *, model, contents, config):
        self._capture["model"] = model
        self._capture["contents"] = contents
        self._capture["config"] = config
        return _Resp(self._text)


class _Auth:
    def __init__(self, models: _Models) -> None:
        self._models = models

    def genai_client(self):
        aio = type("Aio", (), {"models": self._models})()
        return type("Client", (), {"aio": aio})()


async def test_review_diagnosis_parses_result():
    capture: dict = {}
    auth = _Auth(_Models(_REVIEW.model_dump_json(), capture))
    settings = Settings()
    result = await review_diagnosis(settings, auth, {"crop_name": "pomidor"}, [])
    assert isinstance(result, ExpertReview)
    assert result.verdict == "confirmed"
    assert result.keep_preparations is True


async def test_review_diagnosis_includes_candidate_block_when_given():
    capture: dict = {}
    auth = _Auth(_Models(_REVIEW.model_dump_json(), capture))
    settings = Settings()
    await review_diagnosis(
        settings, auth, {"crop_name": "pomidor"},
        [{"id": "d1", "name": "Pomidor fitoftorozi"}],
    )
    system = capture["config"].system_instruction
    assert "GROWZ DISEASE CANDIDATES" in system
    assert "d1: Pomidor fitoftorozi" in system


async def test_review_diagnosis_no_candidate_block_when_empty():
    capture: dict = {}
    auth = _Auth(_Models(_REVIEW.model_dump_json(), capture))
    settings = Settings()
    await review_diagnosis(settings, auth, {"crop_name": "pomidor"}, [])
    assert (
        capture["config"].system_instruction == agronom_review.AGRONOM_REVIEW_SYSTEM_PROMPT
    )


async def test_review_diagnosis_uses_diagnosis_model():
    capture: dict = {}
    auth = _Auth(_Models(_REVIEW.model_dump_json(), capture))
    settings = Settings(diagnosis_model="gemini-3.1-pro-preview")
    await review_diagnosis(settings, auth, {}, [])
    assert capture["model"] == "gemini-3.1-pro-preview"


# ---- _review_input: thin vs full last_diagnosis ---------------------------


def test_review_input_full_last_diagnosis():
    doc = ChatDoc(
        id="x", user_id=DEV, crop_name="Pomidor", plant_part="leaf",
        query_type="disease_pest", symptom_summary="Barglarda dogʻ",
        last_diagnosis={
            "disease": "Fitoftoroz", "confidence": "high",
            "result": {"likely_disease": "Fitoftoroz", "confidence": "high",
                       "is_plant": True, "differentials": [],
                       "immediate_treatment": [], "prevention": [],
                       "spoken_summary": "x", "language": "uz"},
            "summary": {"crop": "pomidor"},
            "preparations_full": [{"name": "TOPAZ"}],
        },
    )
    out = _review_input(doc)
    assert out["crop_name"] == "Pomidor"
    assert out["ai_diagnosis"]["likely_disease"] == "Fitoftoroz"
    assert out["interview_summary"] == {"crop": "pomidor"}
    assert out["ai_preparations"] == [{"name": "TOPAZ"}]


def test_review_input_thin_last_diagnosis_falls_back():
    doc = ChatDoc(
        id="x", user_id=DEV, crop_name="Pomidor",
        last_diagnosis={
            "disease": "Fitoftoroz", "confidence": "high", "date": "2026-07-11",
            "preparations": ["TOPAZ", "SKOR"],
        },
    )
    out = _review_input(doc)
    assert out["ai_diagnosis"] == {"likely_disease": "Fitoftoroz", "confidence": "high"}
    assert out["interview_summary"] == {}
    assert out["ai_preparations"] == [{"name": "TOPAZ"}, {"name": "SKOR"}]


def test_review_input_no_last_diagnosis_at_all():
    doc = ChatDoc(id="x", user_id=DEV, crop_name="Pomidor")
    out = _review_input(doc)
    assert out["ai_diagnosis"] == {"likely_disease": "", "confidence": ""}
    assert out["ai_preparations"] == []


def test_review_input_includes_session_photos_when_present():
    # §5.5 — bytes-free photo refs flow into the agronom payload.
    refs = [
        {"photo_id": "p0", "stored_path": "/data/photos/u/c/p0.jpg",
         "selected": True, "image_confidence": "ok", "duplicate_of": None,
         "per_image_analysis": {"organ": "leaf"}},
        {"photo_id": "p1", "stored_path": None, "selected": False,
         "image_confidence": "low", "duplicate_of": "p0",
         "per_image_analysis": None},
    ]
    doc = ChatDoc(
        id="x", user_id=DEV, crop_name="Pomidor",
        last_diagnosis={"disease": "Fitoftoroz", "confidence": "high",
                        "photos": refs},
    )
    out = _review_input(doc)
    assert out["session_photos"] == refs
    # No raw bytes ever reach the reviewer.
    assert all("data" not in r for r in out["session_photos"])


def test_review_input_session_photos_defaults_to_empty_for_legacy_docs():
    doc = ChatDoc(
        id="x", user_id=DEV, crop_name="Pomidor",
        last_diagnosis={"disease": "Fitoftoroz", "confidence": "high"},
    )
    assert _review_input(doc)["session_photos"] == []


# ---- maybe_start_mock_review gating ----------------------------------------


def _doc_with_pending(store: ChatStore, *, diagnosis: dict | None = None) -> ChatDoc:
    doc = store.create(DEV)
    doc.agronom_review = AgronomReview(status="pending", requested_at="t1")
    if diagnosis is not None:
        doc.last_diagnosis = diagnosis
    store.save(doc)
    return doc


def test_maybe_start_mock_review_noop_when_agronom_disabled(tmp_path, monkeypatch):
    settings = Settings(
        chats_dir=str(tmp_path / "chats"), agronom_enabled=False,
        agronom_mock_enabled=True,
    )
    store = ChatStore(settings)
    doc = _doc_with_pending(store, diagnosis={"disease": "x"})
    calls = []
    monkeypatch.setattr(
        agronom_review.asyncio, "create_task", lambda coro: calls.append(coro)
    )
    maybe_start_mock_review(settings, store, doc)
    assert calls == []


def test_maybe_start_mock_review_noop_when_mock_disabled(tmp_path, monkeypatch):
    settings = Settings(
        chats_dir=str(tmp_path / "chats"), agronom_enabled=True,
        agronom_mock_enabled=False,
    )
    store = ChatStore(settings)
    doc = _doc_with_pending(store, diagnosis={"disease": "x"})
    calls = []
    monkeypatch.setattr(
        agronom_review.asyncio, "create_task", lambda coro: calls.append(coro)
    )
    maybe_start_mock_review(settings, store, doc)
    assert calls == []


def test_maybe_start_mock_review_noop_when_not_pending(tmp_path, monkeypatch):
    settings = Settings(
        chats_dir=str(tmp_path / "chats"), agronom_enabled=True,
        agronom_mock_enabled=True,
    )
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.agronom_review = None  # "none" status
    doc.last_diagnosis = {"disease": "x"}
    calls = []
    monkeypatch.setattr(
        agronom_review.asyncio, "create_task", lambda coro: calls.append(coro)
    )
    maybe_start_mock_review(settings, store, doc)
    assert calls == []


def test_maybe_start_mock_review_noop_when_no_diagnosis_yet(tmp_path, monkeypatch):
    settings = Settings(
        chats_dir=str(tmp_path / "chats"), agronom_enabled=True,
        agronom_mock_enabled=True,
    )
    store = ChatStore(settings)
    doc = _doc_with_pending(store, diagnosis=None)
    calls = []
    monkeypatch.setattr(
        agronom_review.asyncio, "create_task", lambda coro: calls.append(coro)
    )
    maybe_start_mock_review(settings, store, doc)
    assert calls == []
    # No task scheduled -> no pending coroutine warning; nothing to await.


def test_maybe_start_mock_review_starts_task_when_all_conditions_met(
    tmp_path, monkeypatch
):
    settings = Settings(
        chats_dir=str(tmp_path / "chats"), agronom_enabled=True,
        agronom_mock_enabled=True,
    )
    store = ChatStore(settings)
    doc = _doc_with_pending(store, diagnosis={"disease": "Fitoftoroz"})
    calls = []

    def fake_create_task(coro):
        calls.append(coro)
        coro.close()  # avoid "never awaited" warnings — we only check gating
        return None

    monkeypatch.setattr(agronom_review.asyncio, "create_task", fake_create_task)
    maybe_start_mock_review(settings, store, doc)
    assert len(calls) == 1


def test_maybe_start_mock_review_never_raises_on_internal_error(tmp_path, monkeypatch):
    settings = Settings(
        chats_dir=str(tmp_path / "chats"), agronom_enabled=True,
        agronom_mock_enabled=True,
    )
    store = ChatStore(settings)
    doc = _doc_with_pending(store, diagnosis={"disease": "x"})

    def boom(coro):
        coro.close()
        raise RuntimeError("boom")

    monkeypatch.setattr(agronom_review.asyncio, "create_task", boom)
    maybe_start_mock_review(settings, store, doc)  # must not raise


# ---- _run_mock_review runner (frozen flow, P3.5) ---------------------------


def _patch_runner(
    monkeypatch, *, review=None, review_exc=None,
    candidates=None, preps_by_id=None,
):
    async def fake_get_crop_diseases(settings, crop_name, kind="disease_pest"):
        return candidates or []

    async def fake_review_diagnosis(settings, auth, review_input, growz_candidates):
        if review_exc is not None:
            raise review_exc
        return review if review is not None else _REVIEW

    async def fake_find_preparations_by_id(settings, disease_id):
        return preps_by_id or []

    monkeypatch.setattr(agronom_review, "get_crop_diseases", fake_get_crop_diseases)
    monkeypatch.setattr(agronom_review, "review_diagnosis", fake_review_diagnosis)
    monkeypatch.setattr(
        agronom_review, "find_preparations_by_id", fake_find_preparations_by_id
    )


@pytest.fixture
def store(tmp_path):
    return ChatStore(Settings(chats_dir=str(tmp_path / "chats")))


@pytest.fixture
def settings(tmp_path):
    return Settings(chats_dir=str(tmp_path / "chats"))


async def test_run_mock_review_happy_path_writes_done_and_is_mock(
    monkeypatch, store, settings
):
    doc = _doc_with_pending(store, diagnosis={"disease": "Fitoftoroz"})
    _patch_runner(monkeypatch, review=_REVIEW)

    await _run_mock_review(settings, store, DEV, doc.id)

    reloaded = store.read(DEV, doc.id)
    assert reloaded.agronom_review.status == "done"
    assert reloaded.agronom_review.is_mock is True
    assert reloaded.agronom_review.verdict == "confirmed"
    assert reloaded.agronom_review.expert_summary == _REVIEW.expert_summary
    assert reloaded.agronom_review.expert_notes == _REVIEW.expert_notes
    assert reloaded.agronom_review.adjusted_preparations == []
    assert reloaded.messages[-1].role == "agronom"
    assert reloaded.messages[-1].kind == "agronom_review"
    assert reloaded.messages[-1].text == (
        f"Agronom javobi: {_REVIEW.expert_summary}"
    )


async def test_run_mock_review_keep_preparations_false_yields_empty_list(
    monkeypatch, store, settings
):
    doc = _doc_with_pending(store, diagnosis={"disease": "Fitoftoroz"})
    review = ExpertReview(
        verdict="adjusted", expert_summary="Boshqa kasallik.",
        expert_notes=[], keep_preparations=False, adjusted_growz_disease_id="",
    )
    _patch_runner(monkeypatch, review=review)

    await _run_mock_review(settings, store, DEV, doc.id)

    reloaded = store.read(DEV, doc.id)
    assert reloaded.agronom_review.adjusted_preparations == []


async def test_run_mock_review_adjusted_id_uses_find_preparations_by_id(
    monkeypatch, store, settings
):
    doc = _doc_with_pending(store, diagnosis={"disease": "Fitoftoroz"})
    review = ExpertReview(
        verdict="adjusted", expert_summary="Boshqa kasallik moslashadi.",
        expert_notes=[], keep_preparations=False,
        adjusted_growz_disease_id="disease-42",
    )
    preps = [{"name": "SKOR", "dose_min": 0.2, "dose_max": 0.3,
              "unit": "l/ga", "type": "disease", "description": ""}]
    _patch_runner(monkeypatch, review=review, preps_by_id=preps)

    await _run_mock_review(settings, store, DEV, doc.id)

    reloaded = store.read(DEV, doc.id)
    assert reloaded.agronom_review.adjusted_preparations == preps


async def test_run_mock_review_genai_error_stays_pending(monkeypatch, store, settings):
    doc = _doc_with_pending(store, diagnosis={"disease": "Fitoftoroz"})
    _patch_runner(monkeypatch, review_exc=RuntimeError("genai down"))

    await _run_mock_review(settings, store, DEV, doc.id)  # must not raise

    reloaded = store.read(DEV, doc.id)
    assert reloaded.agronom_review.status == "pending"


async def test_run_mock_review_timeout_stays_pending(monkeypatch, store, settings):
    import asyncio

    doc = _doc_with_pending(store, diagnosis={"disease": "Fitoftoroz"})

    async def fake_get_crop_diseases(settings, crop_name, kind="disease_pest"):
        return []

    async def slow_review_diagnosis(settings, auth, review_input, growz_candidates):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(agronom_review, "get_crop_diseases", fake_get_crop_diseases)
    monkeypatch.setattr(agronom_review, "review_diagnosis", slow_review_diagnosis)

    await _run_mock_review(settings, store, DEV, doc.id)

    reloaded = store.read(DEV, doc.id)
    assert reloaded.agronom_review.status == "pending"


async def test_run_mock_review_empty_summary_stays_pending(monkeypatch, store, settings):
    doc = _doc_with_pending(store, diagnosis={"disease": "Fitoftoroz"})
    review = ExpertReview(
        verdict="confirmed", expert_summary="   ", expert_notes=[],
        keep_preparations=True,
    )
    _patch_runner(monkeypatch, review=review)

    await _run_mock_review(settings, store, DEV, doc.id)

    reloaded = store.read(DEV, doc.id)
    assert reloaded.agronom_review.status == "pending"


async def test_run_mock_review_human_wins_race(monkeypatch, store, settings):
    """A human review completes (status flips to done) WHILE the mock runner
    is mid-flight; the mock must detect this under the lock and drop itself,
    never overwriting the human's verdict."""
    doc = _doc_with_pending(store, diagnosis={"disease": "Fitoftoroz"})

    async def fake_get_crop_diseases(settings, crop_name, kind="disease_pest"):
        return []

    async def fake_review_diagnosis(settings, auth, review_input, growz_candidates):
        # Simulate the human submit landing WHILE the AI call is in flight.
        human_doc = store.read(DEV, doc.id)
        human_doc.agronom_review.status = "done"
        human_doc.agronom_review.is_mock = False
        human_doc.agronom_review.verdict = "confirmed"
        human_doc.agronom_review.expert_summary = "Inson agronomi javobi."
        store.save(human_doc)
        return _REVIEW

    monkeypatch.setattr(agronom_review, "get_crop_diseases", fake_get_crop_diseases)
    monkeypatch.setattr(agronom_review, "review_diagnosis", fake_review_diagnosis)

    await _run_mock_review(settings, store, DEV, doc.id)

    reloaded = store.read(DEV, doc.id)
    assert reloaded.agronom_review.is_mock is False
    assert reloaded.agronom_review.expert_summary == "Inson agronomi javobi."


async def test_run_mock_review_doc_missing_returns_quietly(store, settings):
    await _run_mock_review(settings, store, DEV, "a" * 32)  # no such chat


async def test_run_mock_review_status_not_pending_returns_quietly(
    monkeypatch, store, settings
):
    doc = store.create(DEV)
    doc.agronom_review = AgronomReview(status="none")
    store.save(doc)
    _patch_runner(monkeypatch, review=_REVIEW)

    await _run_mock_review(settings, store, DEV, doc.id)

    reloaded = store.read(DEV, doc.id)
    assert reloaded.agronom_review.status == "none"
