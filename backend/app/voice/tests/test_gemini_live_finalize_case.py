"""GeminiLiveSession._run_finalize_case wiring (contract addendum P2.3/P2.4):
photo ranking -> diagnose -> guarded preparations lookup -> case.diagnosis
event -> last_diagnosis -> [TIZIM] spoken text. Ranking/preparations are
monkeypatched at the gemini_live module namespace (diagnose.py / photo_select
/ treatments already have their own unit tests)."""
import asyncio

from app.config import Settings
from app.voice.pipeline.diagnosis import DiagnosisResult, Differential
from app.voice.pipeline.tools import PhotoAttachment
from app.voice.providers import gemini_live
from app.voice.providers.gemini_live import GeminiLiveSession, _diagnosis_spoken

_RESULT = DiagnosisResult(
    is_plant=True,
    likely_disease="Un shudring",
    confidence="high",
    differentials=[Differential(name="Alternarioz", why="boshqa shakl")],
    immediate_treatment=["oltingugurt sepish"],
    prevention=["namlikni kamaytirish"],
    spoken_summary="Bu un shudring kasalligi.",
    language="uz",
)

_PREP2 = [
    {"name": "TOPAZ 10% EM.K", "dose_min": 0.3, "dose_max": 0.4, "unit": "l/ga",
     "type": "disease", "description": "Fungitsid"},
    {"name": "SKOR", "dose_min": None, "dose_max": None, "unit": "",
     "type": "disease", "description": ""},
]

_PREP4 = _PREP2 + [
    {"name": "THIRD", "dose_min": None, "dose_max": None, "unit": "",
     "type": "disease", "description": ""},
    {"name": "FOURTH", "dose_min": None, "dose_max": None, "unit": "",
     "type": "disease", "description": ""},
]


def _make_session(
    monkeypatch, *, preparations=None, prep_exc=None, diagnose_exc=None,
    analyses=None, analyze_exc=None,
):
    sent: list[dict] = []

    async def send_json(payload):
        sent.append(payload)

    async def send_bytes(data):
        pass

    session = GeminiLiveSession(
        settings=Settings(), auth=object(),
        send_json=send_json, send_bytes=send_bytes,
        system_prompt="x",
    )
    session._photos = [
        PhotoAttachment(photo_id=f"p{i}", data=f"d{i}".encode(), mime="image/jpeg")
        for i in range(5)
    ]

    ranked_calls: list = []

    async def fake_select_best_photos(settings, auth, photos, max_n=3):
        ranked_calls.append((list(photos), max_n))
        return photos[:max_n]

    diagnose_calls: list = []

    async def fake_diagnose(
        settings, auth, summary, photos, growz_diseases=None, per_image_analyses=None,
    ):
        diagnose_calls.append((summary, list(photos), growz_diseases, per_image_analyses))
        if diagnose_exc is not None:
            raise diagnose_exc
        return _RESULT

    async def fake_get_crop_diseases(settings, crop_name, kind="disease_pest"):
        return []

    byid_calls: list = []

    async def fake_find_preparations_by_id(settings, disease_id):
        byid_calls.append(disease_id)
        return preparations if preparations is not None else []

    prep_calls: list = []

    async def fake_find_preparations(settings, disease_name, kind, crop_name=""):
        prep_calls.append((disease_name, kind, crop_name))
        if prep_exc is not None:
            raise prep_exc
        return preparations if preparations is not None else []

    analyze_calls: list = []

    async def fake_analyze_selected_photos(settings, auth, photos):
        analyze_calls.append(list(photos))
        if analyze_exc is not None:
            raise analyze_exc
        if analyses is not None:
            return analyses
        return [None] * len(photos)

    monkeypatch.setattr(gemini_live, "select_best_photos", fake_select_best_photos)
    monkeypatch.setattr(gemini_live, "diagnose", fake_diagnose)
    monkeypatch.setattr(gemini_live, "get_crop_diseases", fake_get_crop_diseases)
    monkeypatch.setattr(
        gemini_live, "find_preparations_by_id", fake_find_preparations_by_id
    )
    monkeypatch.setattr(gemini_live, "find_preparations", fake_find_preparations)
    monkeypatch.setattr(
        gemini_live, "analyze_selected_photos", fake_analyze_selected_photos
    )

    return session, sent, ranked_calls, diagnose_calls, prep_calls


async def test_finalize_case_wires_ranking_diagnosis_and_preparations(monkeypatch):
    session, sent, ranked_calls, diagnose_calls, prep_calls = _make_session(
        monkeypatch, preparations=_PREP2,
    )
    session.diagnosis_kind = "weed"

    await session._run_finalize_case("case_1", {"crop": "bugʻdoy"})

    # Ranking ran on the full photo list, capped at max_n=3.
    assert ranked_calls == [(session._photos, 3)]
    # diagnose() received the RANKED (<=3) list, not the raw session photos.
    assert diagnose_calls[0][1] == session._photos[:3]
    # preparations looked up with the diagnosed name + session.diagnosis_kind.
    assert prep_calls == [("Un shudring", "weed", "bugʻdoy")]

    # §5.5 — bytes-free refs for all 5 session photos; first 3 selected.
    expected_refs = [
        {"photo_id": f"p{i}", "stored_path": None, "selected": i < 3,
         "image_confidence": "ok", "duplicate_of": None,
         "per_image_analysis": None}
        for i in range(5)
    ]
    diag_event = next(p for p in sent if p["type"] == "case.diagnosis")
    assert diag_event == {
        "type": "case.diagnosis", "case_id": "case_1",
        "result": _RESULT.model_dump(), "summary": {"crop": "bugʻdoy"},
        "preparations": _PREP2,
        "photos": expected_refs,
    }
    assert session.last_diagnosis == {
        "disease": "Un shudring", "confidence": "high",
        "date": session.last_diagnosis["date"],
        "preparations": ["TOPAZ 10% EM.K", "SKOR"],
        # Phase 3 (P3.2): additive keys for the agronom reviewer.
        "result": _RESULT.model_dump(),
        "summary": {"crop": "bugʻdoy"},
        "preparations_full": _PREP2,
        # §5.5: bytes-free photo refs for the agronom payload.
        "photos": expected_refs,
    }
    # self._photos is NEVER mutated by ranking/finalize.
    assert len(session._photos) == 5


async def test_finalize_case_injects_saved_planting_profile(monkeypatch):
    # «Profilda bo'lsa — Sistem avtomatik oladi»: a crop that matches a saved
    # planting adds summary.farmer_profile before diagnose() sees it.
    session, sent, ranked_calls, diagnose_calls, prep_calls = _make_session(monkeypatch)

    await session._run_finalize_case("case_1", {"crop": "Achchiq qalampir"})

    profile = diagnose_calls[0][0].get("farmer_profile")
    assert profile is not None
    assert profile["crop"] == "Achchiq qalampir"
    assert profile["field"]["name"] == "gwyw"
    assert profile["current_agrotech_task"].startswith("Qalampirning ildiz tizimi")


async def test_finalize_case_no_profile_for_unmatched_crop(monkeypatch):
    session, sent, ranked_calls, diagnose_calls, prep_calls = _make_session(monkeypatch)
    await session._run_finalize_case("case_1", {"crop": "bugʻdoy"})
    assert "farmer_profile" not in diagnose_calls[0][0]


async def test_finalize_case_no_preparations_yields_empty_array_and_v1_speech(
    monkeypatch,
):
    session, sent, *_ = _make_session(monkeypatch, preparations=[])
    await session._run_finalize_case("case_1", {"crop": "pomidor"})

    diag_event = next(p for p in sent if p["type"] == "case.diagnosis")
    assert diag_event["preparations"] == []
    assert session.last_diagnosis["preparations"] == []


async def test_finalize_case_caps_last_diagnosis_names_at_three(monkeypatch):
    session, sent, *_ = _make_session(monkeypatch, preparations=_PREP4)
    await session._run_finalize_case("case_1", {"crop": "bugʻdoy"})

    diag_event = next(p for p in sent if p["type"] == "case.diagnosis")
    # The wire event ships the full (<=4, PREP_CAP) preparations list...
    assert len(diag_event["preparations"]) == 4
    # ...but the memory-facing last_diagnosis caps names at 3 (P2.3).
    assert session.last_diagnosis["preparations"] == [
        "TOPAZ 10% EM.K", "SKOR", "THIRD",
    ]


async def test_finalize_case_preparations_lookup_failure_fails_open(monkeypatch):
    """A raised exception from find_preparations must never consume an
    already-computed diagnosis (P2.3/P2.10) — the event still ships, with an
    empty preparations array, and the apology path is NOT taken."""
    session, sent, *_ = _make_session(
        monkeypatch, prep_exc=RuntimeError("growz down"),
    )
    await session._run_finalize_case("case_1", {"crop": "pomidor"})

    types = [p["type"] for p in sent]
    assert "error" not in types
    diag_event = next(p for p in sent if p["type"] == "case.diagnosis")
    assert diag_event["preparations"] == []
    assert session.last_diagnosis["preparations"] == []


async def test_finalize_case_diagnose_failure_still_sends_error_and_apology(
    monkeypatch,
):
    """Unchanged v1 failure path: a diagnose() error still produces the
    error event + Uzbek apology (ranking runs, but no diagnosis is emitted)."""
    session, sent, *_ = _make_session(monkeypatch, diagnose_exc=RuntimeError("boom"))
    await session._run_finalize_case("case_1", {"crop": "pomidor"})

    types = [p["type"] for p in sent]
    assert types == ["error"]
    assert session.last_diagnosis is None


def test_diagnosis_spoken_no_preparations_is_byte_identical_to_v1():
    text = _diagnosis_spoken(_RESULT, [])
    assert text == (
        "[TIZIM] Tashxis tayyor. Fermerga shu xulosani qisqa o'qib ber: "
        + _RESULT.spoken_summary
    )


def test_diagnosis_spoken_with_preparations_appends_frozen_sentence():
    text = _diagnosis_spoken(_RESULT, _PREP2)
    assert text == (
        "[TIZIM] Tashxis tayyor. Fermerga shu xulosani qisqa o'qib ber: "
        + _RESULT.spoken_summary
        + " Soʻngra bir jumlada qoʻshib ayt: davolash uchun "
        "TOPAZ 10% EM.K va SKOR kabi preparatlar bor, ularni Growz "
        "Agroaptekasidan olsa boʻladi."
    )


def test_diagnosis_spoken_uses_top_two_names_only():
    text = _diagnosis_spoken(_RESULT, _PREP4)
    assert "THIRD" not in text and "FOURTH" not in text
    assert "TOPAZ 10% EM.K va SKOR" in text


# ---- Phase 3 (contract addendum P3.6): the agronom-offer suffix ------------


def test_diagnosis_spoken_offer_agronom_false_is_byte_identical_default():
    # Default kwarg (offer_agronom unset) must reproduce the exact P2.4 bytes.
    assert _diagnosis_spoken(_RESULT, []) == (
        "[TIZIM] Tashxis tayyor. Fermerga shu xulosani qisqa o'qib ber: "
        + _RESULT.spoken_summary
    )
    assert _diagnosis_spoken(_RESULT, _PREP2) == (
        "[TIZIM] Tashxis tayyor. Fermerga shu xulosani qisqa o'qib ber: "
        + _RESULT.spoken_summary
        + " Soʻngra bir jumlada qoʻshib ayt: davolash uchun "
        "TOPAZ 10% EM.K va SKOR kabi preparatlar bor, ularni Growz "
        "Agroaptekasidan olsa boʻladi."
    )


def test_diagnosis_spoken_offer_agronom_true_appends_suffix_no_preparations():
    from app.voice.providers.gemini_live import _AGRONOM_OFFER_SPOKEN

    text = _diagnosis_spoken(_RESULT, [], offer_agronom=True)
    assert text == (
        "[TIZIM] Tashxis tayyor. Fermerga shu xulosani qisqa o'qib ber: "
        + _RESULT.spoken_summary
        + _AGRONOM_OFFER_SPOKEN
    )


def test_diagnosis_spoken_offer_agronom_true_appends_suffix_after_preparations():
    from app.voice.providers.gemini_live import _AGRONOM_OFFER_SPOKEN

    text = _diagnosis_spoken(_RESULT, _PREP2, offer_agronom=True)
    assert text.endswith(_AGRONOM_OFFER_SPOKEN)
    assert "TOPAZ 10% EM.K va SKOR" in text
    # The offer suffix is appended LAST, after the preparations sentence.
    assert text.index("Agroaptekasidan") < text.index("Agronomga yuborish")


# ---- Phase 3 (P3.2): last_diagnosis additive keys --------------------------


async def test_finalize_case_last_diagnosis_carries_the_three_new_keys(monkeypatch):
    session, sent, *_ = _make_session(monkeypatch, preparations=_PREP2)
    await session._run_finalize_case("case_1", {"crop": "bugʻdoy"})

    assert session.last_diagnosis["result"] == _RESULT.model_dump()
    assert session.last_diagnosis["summary"] == {"crop": "bugʻdoy"}
    assert session.last_diagnosis["preparations_full"] == _PREP2


async def test_agronom_offer_defaults_to_false(monkeypatch):
    """A GeminiLiveSession defaults agronom_offer=False (byte-identical
    Phase 2 behaviour) unless voice_agent.py sets it."""
    session, *_ = _make_session(monkeypatch, preparations=[])
    assert session.agronom_offer is False


# ---- §4/§5 (diagnostic_flow.md) — duplicates, per-image analysis, refs ------


async def test_finalize_case_excludes_duplicates_from_ranking_input(monkeypatch):
    """§4 — near-duplicate photos add no information: they are excluded from
    the ranking input (but stay in self._photos and in the refs)."""
    session, sent, ranked_calls, *_ = _make_session(monkeypatch)
    session._photos[1].duplicate_of = "p0"
    session._photos[4].duplicate_of = "p2"

    await session._run_finalize_case("case_1", {"crop": "bugʻdoy"})

    ranked_photos, max_n = ranked_calls[0]
    assert [p.photo_id for p in ranked_photos] == ["p0", "p2", "p3"]
    assert max_n == 3
    # ...but the full session list (dups included) still ships in the refs.
    diag_event = next(p for p in sent if p["type"] == "case.diagnosis")
    assert [r["photo_id"] for r in diag_event["photos"]] == [
        "p0", "p1", "p2", "p3", "p4",
    ]
    assert diag_event["photos"][1]["duplicate_of"] == "p0"


async def test_finalize_case_all_duplicates_falls_back_to_full_list(monkeypatch):
    """Degenerate edge: every photo flagged as a dup -> ranking still gets the
    full list (never an empty input while photos exist)."""
    session, sent, ranked_calls, *_ = _make_session(monkeypatch)
    for p in session._photos:
        p.duplicate_of = "px"

    await session._run_finalize_case("case_1", {"crop": "bugʻdoy"})

    assert ranked_calls[0][0] == session._photos


async def test_finalize_case_attaches_per_image_analysis_to_selected_only(monkeypatch):
    """§5.4 — analyses are attached to the SELECTED photos (by position) and
    surfaced only on selected entries in the refs; diagnose() receives the
    same list as its per_image_analyses kwarg."""
    from app.voice.pipeline.photo_analysis import PerImageAnalysis

    a0 = PerImageAnalysis(
        symptoms_seen=["dogʻlar"], organ="leaf",
        likely_disease_hypotheses=["fitoftoroz"], likely_pest_hypotheses=[],
        nutrient_deficiency_suspected=False, agrotech_stress_suspected=False,
        confidence="medium",
    )
    a2 = PerImageAnalysis(
        symptoms_seen=["chirish"], organ="stem",
        likely_disease_hypotheses=[], likely_pest_hypotheses=["qurt"],
        nutrient_deficiency_suspected=True, agrotech_stress_suspected=True,
        confidence="low",
    )
    session, sent, ranked_calls, diagnose_calls, _ = _make_session(
        monkeypatch, analyses=[a0, None, a2],
    )

    await session._run_finalize_case("case_1", {"crop": "bugʻdoy"})

    assert session._photos[0].per_image_analysis == a0.model_dump()
    assert session._photos[1].per_image_analysis is None
    assert session._photos[2].per_image_analysis == a2.model_dump()
    assert session._photos[3].per_image_analysis is None  # unselected untouched

    # diagnose() got the aligned kwarg list.
    assert diagnose_calls[0][3] == [a0.model_dump(), None, a2.model_dump()]

    diag_event = next(p for p in sent if p["type"] == "case.diagnosis")
    refs = diag_event["photos"]
    assert refs[0]["per_image_analysis"] == a0.model_dump()
    assert refs[1]["per_image_analysis"] is None
    assert refs[2]["per_image_analysis"] == a2.model_dump()
    assert refs[3]["per_image_analysis"] is None and refs[3]["selected"] is False


async def test_finalize_case_per_image_analysis_failure_fails_open(monkeypatch):
    """§5.4 fail-open: a raised analyze_selected_photos never consumes the
    diagnosis — all analyses stay None and the event still ships."""
    session, sent, *_ = _make_session(
        monkeypatch, analyze_exc=RuntimeError("flash down"),
    )
    await session._run_finalize_case("case_1", {"crop": "bugʻdoy"})

    types = [p["type"] for p in sent]
    assert "error" not in types
    diag_event = next(p for p in sent if p["type"] == "case.diagnosis")
    assert all(r["per_image_analysis"] is None for r in diag_event["photos"])


async def test_finalize_case_photo_refs_never_contain_bytes(monkeypatch):
    """§5.5 — refs are bytes-free: no `data`/`mime` keys anywhere."""
    session, sent, *_ = _make_session(monkeypatch)
    await session._run_finalize_case("case_1", {"crop": "bugʻdoy"})

    diag_event = next(p for p in sent if p["type"] == "case.diagnosis")
    for ref in diag_event["photos"]:
        assert set(ref) == {
            "photo_id", "stored_path", "selected", "image_confidence",
            "duplicate_of", "per_image_analysis",
        }


# ---- finalize_from_guide (deterministic guide trigger + double-dx guard) ----


async def test_finalize_from_guide_runs_when_no_case_task(monkeypatch):
    session, sent, *_ = _make_session(monkeypatch)
    ran: list[dict] = []

    async def fake_run(case_id, summary):
        ran.append(summary)

    monkeypatch.setattr(session, "_run_finalize_case", fake_run)
    assert session._case_task is None

    await session.finalize_from_guide({"crop": "pomidor", "farmer_language": "uz"})

    assert any(p["type"] == "diagnosis.started" for p in sent)
    assert session._case_task is not None
    await session._case_task
    assert ran == [{"crop": "pomidor", "farmer_language": "uz"}]


async def test_finalize_from_guide_noops_while_a_case_is_running(monkeypatch):
    session, sent, *_ = _make_session(monkeypatch)
    release = asyncio.Event()
    started = asyncio.Event()

    async def long_task():
        started.set()
        await release.wait()

    session._case_task = asyncio.create_task(long_task())
    await started.wait()

    await session.finalize_from_guide({"crop": "pomidor"})
    # double-diagnosis guard: no diagnosis.started emitted while running.
    assert not any(p["type"] == "diagnosis.started" for p in sent)

    release.set()
    await session._case_task


class _FakeFC:
    """Minimal Live function-call stand-in for _handle_tool_call."""

    def __init__(self, name, args=None, fc_id="fc1"):
        self.name = name
        self.args = args or {}
        self.id = fc_id


async def test_model_finalize_case_noops_while_a_guide_case_is_running(monkeypatch):
    # Guard is BI-directional: a farmer tap (finalize_from_guide) that started a
    # case must block a later model-driven finalize_case from double-running.
    session, sent, *_ = _make_session(monkeypatch)
    release = asyncio.Event()
    started = asyncio.Event()

    async def long_task():
        started.set()
        await release.wait()

    running = asyncio.create_task(long_task())
    session._case_task = running
    await started.wait()

    await session._handle_tool_call(
        _FakeFC("finalize_case", {"summary": {"crop": "pomidor"}})
    )
    # No SECOND diagnosis.started, and the running task handle is not orphaned.
    assert not any(p["type"] == "diagnosis.started" for p in sent)
    assert session._case_task is running

    release.set()
    await running


async def test_model_finalize_case_runs_when_no_case_task(monkeypatch):
    session, sent, *_ = _make_session(monkeypatch)
    assert session._case_task is None

    await session._handle_tool_call(
        _FakeFC("finalize_case", {"summary": {"crop": "bugʻdoy"}})
    )
    assert any(p["type"] == "diagnosis.started" for p in sent)
    assert session._case_task is not None
    await session._case_task


async def test_model_finalize_case_blocked_without_photo(monkeypatch):
    # HARD photo requirement: a voice-driven finalize_case with NO photo must
    # NOT diagnose — no diagnosis.started, no case task; the ack asks for a photo.
    session, sent, *_ = _make_session(monkeypatch)
    session._photos = []  # farmer pressured for a diagnosis without any image
    assert session._case_task is None

    await session._handle_tool_call(
        _FakeFC("finalize_case", {"summary": {"crop": "bugʻdoy"}})
    )
    assert not any(p["type"] == "diagnosis.started" for p in sent)
    assert session._case_task is None


async def test_finalize_from_guide_blocked_without_photo(monkeypatch):
    # The deterministic guide trigger also never diagnoses with 0 photos.
    session, sent, *_ = _make_session(monkeypatch)
    session._photos = []
    await session.finalize_from_guide({"crop": "pomidor"})
    assert not any(p["type"] == "diagnosis.started" for p in sent)
    assert session._case_task is None


async def test_finalize_from_guide_runs_when_previous_case_done(monkeypatch):
    session, sent, *_ = _make_session(monkeypatch)

    async def noop():
        return

    session._case_task = asyncio.create_task(noop())
    await session._case_task  # now .done()

    ran: list[dict] = []

    async def fake_run(case_id, summary):
        ran.append(summary)

    monkeypatch.setattr(session, "_run_finalize_case", fake_run)
    await session.finalize_from_guide({"crop": "bugʻdoy"})

    assert any(p["type"] == "diagnosis.started" for p in sent)
    await session._case_task
    assert ran == [{"crop": "bugʻdoy"}]
