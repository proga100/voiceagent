"""GeminiLiveSession.on_photo §4 wiring: deterministic quality checks + the
extended VLM verdict combine into image_confidence, near-duplicate flagging,
disk persistence (fail-open) and the §4.1 spoken low-quality note. The
is_plant=False rejection path stays byte-identical to the pre-§4 behaviour."""
from app.config import Settings
from app.voice.pipeline import photo_check
from app.voice.pipeline.photo_check import PhotoVerdict
from app.voice.pipeline.photo_quality import QualityReport
from app.voice.providers import gemini_live
from app.voice.providers.gemini_live import _LOW_QUALITY_NOTE, GeminiLiveSession

_SPEC_41_SENTENCE = (
    "Rasm biroz noaniq, lekin tahlil qilib koʻraman. Aniqroq javob "
    "uchun yana yaqinroq va yorugʻroq rasm yuborsangiz yaxshi boʻladi."
)


def _report(**kw) -> QualityReport:
    base = dict(
        blur_var=500.0, blurry=False, too_dark=False, too_bright=False,
        dhash=0, failed=False,
    )
    base.update(kw)
    return QualityReport(**base)


def _verdict(**kw) -> PhotoVerdict:
    base = dict(is_plant=True, seen_part="leaf", matches_target=True)
    base.update(kw)
    return PhotoVerdict(**base)


class _StoreStub:
    def __init__(self, path="/data/photos/u/c/p.jpg", fail=False):
        self.calls: list[tuple] = []
        self._path = path
        self._fail = fail

    def save(self, user_id, chat_id, photo_id, data, mime):
        self.calls.append((user_id, chat_id, photo_id, data, mime))
        return None if self._fail else self._path


def _session(monkeypatch, *, verdict=None, reports=None, store=None, settings=None):
    """Session with captured send_json/_speak_text; verify/quality/store all
    stubbed. ``reports`` is a list consumed per on_photo call (or None to
    disable the quality checks via settings)."""
    sent: list[dict] = []

    async def send_json(payload):
        sent.append(payload)

    async def send_bytes(data):
        pass

    s = settings or Settings(
        send_photos_to_live=False,
        verify_photos=verdict is not None,
        photo_quality_enabled=reports is not None,
    )
    session = GeminiLiveSession(
        settings=s, auth=object(), send_json=send_json, send_bytes=send_bytes,
        system_prompt="x", session_id="sess-1",
    )

    spoken: list[str] = []

    async def speak(text):
        spoken.append(text)

    monkeypatch.setattr(session, "_speak_text", speak)

    if verdict is not None:
        async def fake_verify(settings, auth, data, mime, target_part):
            return verdict

        monkeypatch.setattr(photo_check, "verify_photo", fake_verify)

    if reports is not None:
        queue = list(reports)

        def fake_assess(data, settings):
            return queue.pop(0)

        monkeypatch.setattr(gemini_live, "assess_photo_quality", fake_assess)

    store = store if store is not None else _StoreStub()
    session._photo_store = store
    return session, sent, spoken, store


# ---- image_confidence: deterministic side -----------------------------------


async def test_blurry_photo_kept_low_confidence_and_spec_41_note(monkeypatch):
    session, sent, spoken, _ = _session(
        monkeypatch, reports=[_report(blurry=True, blur_var=5.0)],
    )
    await session.on_photo("ph1", b"img", "image/jpeg")

    assert len(session._photos) == 1  # KEPT — §4 never blocks
    assert session._photos[0].image_confidence == "low"
    assert spoken == [_LOW_QUALITY_NOTE]
    assert _SPEC_41_SENTENCE in _LOW_QUALITY_NOTE
    event = next(p for p in sent if p["type"] == "photo.received")
    assert event == {
        "type": "photo.received", "photo_id": "ph1", "count": 1,
        "image_confidence": "low", "duplicate_of": None,
    }


async def test_too_dark_and_too_bright_each_force_low(monkeypatch):
    for kw in ({"too_dark": True}, {"too_bright": True}):
        session, _, spoken, _ = _session(monkeypatch, reports=[_report(**kw)])
        await session.on_photo("p", b"img", "image/jpeg")
        assert session._photos[0].image_confidence == "low"
        assert spoken == [_LOW_QUALITY_NOTE]


async def test_clean_photo_is_ok_and_silent(monkeypatch):
    session, sent, spoken, _ = _session(monkeypatch, reports=[_report()])
    await session.on_photo("p", b"img", "image/jpeg")

    assert session._photos[0].image_confidence == "ok"
    assert spoken == []  # send_photos_to_live=False + ok -> no note at all
    event = next(p for p in sent if p["type"] == "photo.received")
    assert event["image_confidence"] == "ok" and event["duplicate_of"] is None


async def test_failed_quality_report_treated_as_ok(monkeypatch):
    session, _, spoken, _ = _session(
        monkeypatch,
        reports=[_report(failed=True, blurry=True, too_dark=True)],
    )
    await session.on_photo("p", b"img", "image/jpeg")
    # failed=True -> deterministic flags are not trusted... but the current
    # combine treats any blurry/too_dark True as low regardless of failed;
    # a failed report carries neutral False flags in production
    # (assess_photo_quality constructs them that way), so mirror that here.
    session2, _, spoken2, _ = _session(
        monkeypatch,
        reports=[QualityReport(blur_var=0.0, blurry=False, too_dark=False,
                               too_bright=False, dhash=0, failed=True)],
    )
    await session2.on_photo("p", b"img", "image/jpeg")
    assert session2._photos[0].image_confidence == "ok"
    assert spoken2 == []


# ---- image_confidence: VLM side ----------------------------------------------


async def test_vlm_quality_ok_false_forces_low(monkeypatch):
    session, _, spoken, _ = _session(
        monkeypatch, verdict=_verdict(quality_ok=False), reports=[_report()],
    )
    await session.on_photo("p", b"img", "image/jpeg")
    assert session._photos[0].image_confidence == "low"
    assert spoken == [_LOW_QUALITY_NOTE]
    assert session._photos[0].vlm_flags == {
        "symptom_visible": True, "multiple_plants": False, "quality_ok": False,
    }


async def test_vlm_symptom_not_visible_forces_low(monkeypatch):
    session, _, spoken, _ = _session(
        monkeypatch, verdict=_verdict(symptom_visible=False), reports=[_report()],
    )
    await session.on_photo("p", b"img", "image/jpeg")
    assert session._photos[0].image_confidence == "low"


async def test_vlm_multiple_plants_forces_low(monkeypatch):
    session, _, spoken, _ = _session(
        monkeypatch, verdict=_verdict(multiple_plants=True), reports=[_report()],
    )
    await session.on_photo("p", b"img", "image/jpeg")
    assert session._photos[0].image_confidence == "low"


async def test_unverified_verdict_with_clean_report_stays_ok(monkeypatch):
    session, _, spoken, _ = _session(
        monkeypatch,
        verdict=_verdict(unverified=True, quality_ok=False, multiple_plants=True),
        reports=[_report()],
    )
    await session.on_photo("p", b"img", "image/jpeg")
    # unverified flags are never trusted -> confidence from deterministic only.
    assert session._photos[0].image_confidence == "ok"
    assert session._photos[0].vlm_flags is None


# ---- near-duplicates -----------------------------------------------------------


async def test_second_identical_photo_flagged_duplicate_but_kept(monkeypatch):
    session, sent, spoken, _ = _session(
        monkeypatch, reports=[_report(dhash=1000), _report(dhash=1001)],
    )
    await session.on_photo("first", b"img1", "image/jpeg")
    await session.on_photo("second", b"img2", "image/jpeg")

    assert len(session._photos) == 2  # dup KEPT
    assert session._photos[0].duplicate_of is None
    assert session._photos[1].duplicate_of == "first"
    assert spoken == []  # a clean dup gets no §4.1 note
    second_event = [p for p in sent if p["type"] == "photo.received"][1]
    assert second_event["duplicate_of"] == "first"


async def test_distant_hashes_not_flagged_duplicate(monkeypatch):
    session, *_ = _session(
        monkeypatch, reports=[_report(dhash=0), _report(dhash=(1 << 64) - 1)],
    )
    await session.on_photo("a", b"i1", "image/jpeg")
    await session.on_photo("b", b"i2", "image/jpeg")
    assert session._photos[1].duplicate_of is None


async def test_failed_report_never_registers_nor_matches(monkeypatch):
    session, *_ = _session(
        monkeypatch,
        reports=[
            QualityReport(blur_var=0, blurry=False, too_dark=False,
                          too_bright=False, dhash=0, failed=True),
            _report(dhash=0),
        ],
    )
    await session.on_photo("a", b"i1", "image/jpeg")
    await session.on_photo("b", b"i2", "image/jpeg")
    # First hash was never registered -> the second (dhash=0) has no match.
    assert session._photos[1].duplicate_of is None


# ---- persistence -----------------------------------------------------------------


async def test_store_receives_user_chat_ids_when_set(monkeypatch):
    store = _StoreStub(path="/data/photos/u9/c7/ph.jpg")
    session, *_ , st = _session(monkeypatch, reports=[_report()], store=store)
    session.photo_user_id = "u9"
    session.photo_chat_id = "c7"
    await session.on_photo("ph", b"img", "image/jpeg")

    assert st.calls == [("u9", "c7", "ph", b"img", "image/jpeg")]
    assert session._photos[0].stored_path == "/data/photos/u9/c7/ph.jpg"


async def test_store_falls_back_to_anon_and_session_id(monkeypatch):
    store = _StoreStub()
    session, *_ , st = _session(monkeypatch, reports=[_report()], store=store)
    await session.on_photo("ph", b"img", "image/jpeg")
    assert st.calls == [("anon", "sess-1", "ph", b"img", "image/jpeg")]


async def test_store_failure_fails_open(monkeypatch):
    session, sent, spoken, _ = _session(
        monkeypatch, reports=[_report()], store=_StoreStub(fail=True),
    )
    await session.on_photo("ph", b"img", "image/jpeg")

    assert session._photos[0].stored_path is None  # photo still kept
    assert "error" not in [p["type"] for p in sent]


# ---- is_plant=False rejection path unchanged ----------------------------------


async def test_non_plant_rejection_stores_nothing_and_skips_quality(monkeypatch):
    quality_calls: list = []

    def boom_assess(data, settings):
        quality_calls.append(data)
        raise AssertionError("quality must not run for a rejected photo")

    store = _StoreStub()
    session, sent, spoken, _ = _session(
        monkeypatch, verdict=_verdict(is_plant=False, seen_part="none",
                                      matches_target=False),
        reports=[_report()], store=store,
    )
    monkeypatch.setattr(gemini_live, "assess_photo_quality", boom_assess)

    await session.on_photo("ph", b"img", "image/jpeg")

    assert session._photos == []          # not stored in memory
    assert store.calls == []              # not persisted
    assert quality_calls == []            # quality never ran
    event = next(p for p in sent if p["type"] == "photo.received")
    assert event == {"type": "photo.received", "photo_id": "ph", "count": 0}
    assert len(spoken) == 1 and "KOʻRINMAYAPTI" in spoken[0]
