"""Integration-style tests for the multichat wiring in voice_agent.py: a fake
Live-shaped session drives run_voice_agent end-to-end and asserts the exact
chat.* WS events + REST-visible ChatStore side effects (contract §1, §4.8)."""
import json

import pytest

from app.config import Settings
from app.voice.chat.store import ChatStore
from app.voice.pipeline import voice_agent

DEV = "11111111-2222-3333-4444-555555555555"


class _FakeChatSession:
    """A Live-shaped fake exposing every hook voice_agent.py wires (memory,
    enrichment, and the new chat extension seam)."""

    def __init__(self) -> None:
        self.calls: list = []
        self.on_turn_committed = None
        self.last_diagnosis = None
        # Phase 2 (P2.3): mirrors GeminiLiveSession's default so a chatless
        # session's untouched value is observable.
        self.diagnosis_kind = "disease_pest"

    def set_input_sample_rate(self, sr):
        self.calls.append(("sr", sr))

    def set_voice(self, v):
        self.calls.append(("voice", v))

    def set_memory(self, block):
        self.calls.append(("set_memory", block))

    def set_tool_extension(self, tools, handler):
        self.calls.append(("set_tool_extension", tools))

    async def start(self):
        self.calls.append(("start",))

    async def speak_system(self, text):
        self.calls.append(("speak_system", text))

    async def _speak_text(self, text):
        self.calls.append(("speak_raw", text))

    async def on_audio_chunk(self, data):
        self.calls.append(("audio", data))

    async def on_user_interrupt(self):
        self.calls.append(("interrupt",))

    async def on_user_text(self, text):
        self.calls.append(("user_text", text))

    async def on_photo(self, photo_id, data, mime, target_part):
        self.calls.append(("photo", photo_id, data, mime, target_part))
        # Mirror the real session: True == photo accepted/stored, so the guide
        # advances/counts it (rejected uploads return False).
        return True

    async def on_photo_quality(self, status, reason, target_part):
        self.calls.append(("quality", status, reason, target_part))

    async def on_camera_cancelled(self):
        self.calls.append(("cancel",))

    def transcript_text(self):
        return ""

    async def close(self):
        self.calls.append(("close",))


class _FakeWebSocket:
    def __init__(self, messages) -> None:
        self._messages = list(messages)
        self.sent_json: list[dict] = []

    async def receive(self):
        if self._messages:
            return self._messages.pop(0)
        return {"type": "websocket.disconnect"}

    async def send_json(self, payload: dict) -> None:
        self.sent_json.append(payload)

    async def send_bytes(self, data: bytes) -> None:
        pass


def _text_frame(event: dict) -> dict:
    return {"type": "websocket.receive", "text": json.dumps(event)}


def _sent_types(ws: _FakeWebSocket) -> list[str]:
    return [p["type"] for p in ws.sent_json]


def _last(ws: _FakeWebSocket, etype: str) -> dict:
    return [p for p in ws.sent_json if p["type"] == etype][-1]


@pytest.fixture(autouse=True)
def _stub_photo_download(monkeypatch):
    """photo.upload carries a URL now — stub the fetch for every test here."""

    async def fake_download(url, max_bytes):
        return b"\x89PNG", "image/png"

    monkeypatch.setattr(voice_agent, "_download_photo", fake_download)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        chats_dir=str(tmp_path / "chats"),
        memory_enabled=False, enrich_enabled=False,
    )


async def test_new_chat_full_guided_flow_over_the_socket(monkeypatch, settings):
    store = ChatStore(settings)
    doc = store.create(DEV)

    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)

    events = [
        {"type": "chat.start", "user_id": DEV, "chat_id": doc.id},
        {"type": "chat.answer", "chat_id": doc.id, "step_id": "query_type",
         "option_id": "disease_pest"},
        {"type": "chat.answer", "chat_id": doc.id, "step_id": "crop",
         "option_id": "uuid-pomidor",
         "crop": {"id": "uuid-pomidor", "name": "Pomidor"}},
        # "Pomidor" is not in the (disabled) Growz profile -> crop routes
        # DIRECTLY into the §1.3 crop_context phase, BEFORE plant_part.
        {"type": "chat.answer", "chat_id": doc.id, "step_id": "crop_context",
         "option_id": "to_symptom"},
        {"type": "chat.answer", "chat_id": doc.id, "step_id": "plant_part",
         "option_id": "leaf"},
        {"type": "chat.answer", "chat_id": doc.id, "step_id": "symptom",
         "option_id": "to_photo"},
        # A photo is mandatory (2026-08-05): done_photos with nothing
        # collected is rejected, so upload one first.
        {"type": "photo.upload", "photo_id": "photo-1",
         "value": "https://cdn.example/photo-1.png"},
        {"type": "chat.answer", "chat_id": doc.id, "step_id": "photo",
         "option_id": "done_photos"},
    ]
    ws = _FakeWebSocket([_text_frame(e) for e in events])
    await voice_agent.run_voice_agent(ws, settings, "t")

    # set_tool_extension wired BEFORE start() (tools fixed at connect time).
    kinds = [c[0] for c in session.calls]
    assert kinds.index("set_tool_extension") < kinds.index("start")
    # Memory kickoff is suppressed; the guide speaks first instead.
    assert "speak_system" not in kinds

    # chat.state merged into chat.step: connect emits a SNAPSHOT step
    # (option_id == ""), transitions ride on the answer step itself, and
    # _finish adds a consult snapshot after the closing answer.
    assert _sent_types(ws) == [
        "chat.step", "chat.question",   # guide.start(): snapshot + question
        "chat.step", "chat.question",   # query_type -> crop
        "chat.step",                    # crop -> crop_context (anketa: savol ovozda)
        "chat.step", "chat.question",   # crop_context -> plant_part
        "chat.step", "chat.question",   # plant_part -> symptom
        "chat.step", "chat.question",   # symptom -> photo
        "chat.step", "chat.question",   # photo.upload -> counted, bar re-shown
        "chat.step", "chat.step",       # done_photos (consult) + finish snapshot
    ]
    assert ws.sent_json[0].get("option_id", "") == ""      # connect snapshot
    assert ws.sent_json[-1].get("option_id", "") == ""     # finish snapshot
    assert ws.sent_json[-1]["phase"] == "consult"
    assert ws.sent_json[0]["phase"] == "guide"
    assert ws.sent_json[1]["step_id"] == "query_type"
    assert _last(ws, "chat.step")["phase"] == "consult"

    # The stored chat reflects every accepted selection.
    saved = store.read(DEV, doc.id)
    assert saved.query_type == "disease_pest"
    assert saved.crop_id == "uuid-pomidor"
    assert saved.crop_name == "Pomidor"
    assert saved.plant_part == "leaf"
    assert saved.symptom_done is True
    assert saved.finished is True
    assert saved.title == "Pomidor — kasallik"


async def test_resumed_finished_chat_skips_guide_and_records_consult_turns(
    monkeypatch, settings
):
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.finished = True
    doc.query_type = "disease_pest"
    doc.crop_id = "uuid-pomidor"
    doc.crop_name = "Pomidor"
    store.save(doc)

    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)

    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    kinds = [c[0] for c in session.calls]
    # A finished chat's guide never attaches the select_option tool.
    assert "set_tool_extension" not in kinds
    assert _sent_types(ws) == ["chat.step"]
    assert ws.sent_json[0]["phase"] == "consult"
    speak_raw = [c[1] for c in session.calls if c[0] == "speak_raw"]
    assert any("Fermer avvalgi suhbatga qaytdi" in s for s in speak_raw)

    # A consult-phase committed turn is recorded via the wired hook.
    assert session.on_turn_committed is not None
    session.on_turn_committed("farmer", "Bargi sarg'ayib qoldi")
    saved = store.read(DEV, doc.id)
    assert saved.messages[-1].role == "farmer"
    assert saved.messages[-1].kind == "text"
    assert saved.messages[-1].text == "Bargi sarg'ayib qoldi"


async def test_language_uz_cyrl_appends_cyrillic_reply_directive(monkeypatch, settings):
    from app.voice.pipeline.prompts import CYRILLIC_REPLY_DIRECTIVE

    store = ChatStore(settings)
    doc = store.create(DEV)
    store.save(doc)
    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)

    ws = _FakeWebSocket([
        _text_frame({
            "type": "chat.start", "user_id": DEV, "chat_id": doc.id,
            "language": "uz-Cyrl",
        }),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    memories = [c[1] for c in session.calls if c[0] == "set_memory"]
    assert any(m == CYRILLIC_REPLY_DIRECTIVE for m in memories)
    # ...and the on-screen button labels/prompt are transliterated too.
    questions = [p for p in ws.sent_json if p["type"] == "chat.question"]
    assert questions and questions[0]["prompt"] == "Нима бўйича маслаҳат керак?"
    assert questions[0]["options"][0]["label"] == "Касалликлар ва зараркунандалар"


async def test_language_uz_latn_omits_cyrillic_directive(monkeypatch, settings):
    from app.voice.pipeline.prompts import CYRILLIC_REPLY_DIRECTIVE

    store = ChatStore(settings)
    doc = store.create(DEV)
    store.save(doc)
    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)

    ws = _FakeWebSocket([
        _text_frame({
            "type": "chat.start", "user_id": DEV, "chat_id": doc.id,
            "language": "uz-UZ",
        }),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    memories = [c[1] for c in session.calls if c[0] == "set_memory"]
    assert all(m != CYRILLIC_REPLY_DIRECTIVE for m in memories)
    # Latin session -> labels stay in the frozen Latin table.
    questions = [p for p in ws.sent_json if p["type"] == "chat.question"]
    assert questions and questions[0]["prompt"] == "Nima boʻyicha maslahat kerak?"


async def test_unknown_chat_id_is_auto_created(monkeypatch, settings):
    # 2026-08-05 (App <-> Main <-> AI topology): the main backend mints the
    # id — an unknown but well-formed chat_id starts a FRESH chat under it.
    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ext = "550e8400-e29b-41d4-a716-446655440000"
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": ext}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")
    # The guide wired up and spoke: snapshot + first question went out.
    assert _sent_types(ws)[:2] == ["chat.step", "chat.question"]
    saved = ChatStore(settings).read(DEV, ext)
    assert saved is not None and saved.id == ext


async def test_hostile_chat_id_falls_back_to_plain_session(monkeypatch, settings):
    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": "../etc/x"}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")
    assert ws.sent_json == []
    assert ("set_tool_extension") not in [c[0] for c in session.calls]


async def test_chats_disabled_ignores_chat_id(monkeypatch, tmp_path):
    settings = Settings(chats_dir=str(tmp_path / "chats"), chats_enabled=False)
    store = ChatStore(settings)
    doc = store.create(DEV)
    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")
    assert ws.sent_json == []


async def test_photo_upload_advances_guide_photo_step(monkeypatch, settings):
    
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.query_type = "disease_pest"
    doc.crop_id = "uuid-pomidor"
    doc.crop_name = "Pomidor"
    doc.plant_part = "leaf"
    doc.symptom_done = True
    store.save(doc)

    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
        _text_frame({
            "type": "photo.upload", "photo_id": "photo-1",
            "value": "https://cdn.example/photo-1.png",
        }),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    assert ("photo", "photo-1", b"\x89PNG", "image/png", None) in session.calls
    assert _last(ws, "chat.step")["step_id"] == "photo"
    assert _last(ws, "chat.step")["option_id"] == "photo-1"
    # Multi-photo loop: one photo no longer finishes — the guide re-emits the
    # photo question, stays in the "guide" phase, and counts the photo.
    assert _last(ws, "chat.question")["step_id"] == "photo"
    assert _last(ws, "chat.step")["phase"] == "guide"
    saved = store.read(DEV, doc.id)
    assert saved.finished is False
    assert saved.photos_collected == 1


async def test_photo_message_carries_the_client_url_for_agronom_ui(monkeypatch, settings):
    """2026-08-05: the URL in photo.upload's `value` (minted by POST /photos)
    is the canonical location — it is attached verbatim to the kind="photo"
    message so the agronom admin UI can render the photo card, and it surfaces
    in build_detail."""
    from app.voice.chat.models import build_detail

    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.query_type = "disease_pest"
    doc.crop_id = "uuid-pomidor"
    doc.crop_name = "Pomidor"
    doc.plant_part = "leaf"
    doc.symptom_done = True
    store.save(doc)

    url = "https://cdn.example/photo-1.png"  # what the WS event carries below
    session = _FakeChatSession()

    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
        _text_frame({
            "type": "photo.upload", "photo_id": "photo-1",
            "value": "https://cdn.example/photo-1.png",
        }),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    saved = store.read(DEV, doc.id)
    photo_msgs = [m for m in saved.messages if m.kind == "photo"]
    assert len(photo_msgs) == 1
    assert photo_msgs[0].photo_url == url
    # build_detail (what the admin UI reads) exposes it on the photo message only.
    detail = build_detail(saved)
    photo_dicts = [m for m in detail["messages"] if m["kind"] == "photo"]
    assert photo_dicts[0]["photo_url"] == url
    # non-photo messages stay bytes-clean (no photo_url key).
    assert all("photo_url" not in m for m in detail["messages"] if m["kind"] != "photo")


async def test_rejected_photo_does_not_advance_or_count_the_guide(monkeypatch, settings):
    """A photo the session rejects (non-plant/oversized/over-cap -> on_photo
    returns False) must NOT emit a chat.step or bump photos_collected — else a
    farmer's rejected shots would inflate the count toward the auto-finalize cap."""
    
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.query_type = "disease_pest"
    doc.crop_id = "uuid-pomidor"
    doc.crop_name = "Pomidor"
    doc.plant_part = "leaf"
    doc.symptom_done = True
    store.save(doc)

    session = _FakeChatSession()

    async def reject_photo(photo_id, data, mime, target_part):
        session.calls.append(("photo", photo_id, data, mime, target_part))
        return False  # session rejected it (e.g. not a plant)

    session.on_photo = reject_photo
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
        _text_frame({
            "type": "photo.upload", "photo_id": "photo-1",
            "value": "https://cdn.example/photo-1.png",
        }),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    assert ("photo", "photo-1", b"\x89PNG", "image/png", None) in session.calls
    # Snapshot steps (option_id == "") are fine — no ACCEPTED answer steps.
    assert not [
        p for p in ws.sent_json
        if p["type"] == "chat.step" and p.get("option_id")
    ]
    saved = store.read(DEV, doc.id)
    assert saved.photos_collected == 0
    assert saved.finished is False


async def test_recorder_records_symptom_phase_turns(monkeypatch, settings):
    """contract §4.7: records_transcript() gates the recorder, not just
    guide.finished — the symptom phase's voice turns are the diagnostic
    substance and must be persisted raw."""
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.query_type = "disease_pest"
    doc.crop_id = "uuid-pomidor"
    doc.crop_name = "Pomidor"
    doc.plant_part = "leaf"  # pending is "symptom"
    store.save(doc)

    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    assert session.on_turn_committed is not None
    session.on_turn_committed("farmer", "Barglar sarg'ayib qoldi, 3 kundan beri")
    saved = store.read(DEV, doc.id)
    assert saved.messages[-1].role == "farmer"
    assert saved.messages[-1].kind == "text"
    assert saved.messages[-1].text == "Barglar sarg'ayib qoldi, 3 kundan beri"


async def test_recorder_does_not_record_plain_guide_step_free_speech(
    monkeypatch, settings
):
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.query_type = "disease_pest"
    # A crop that IS in the (mock) Growz profile -> the start() resume
    # recompute keeps crop_in_profile True, so crop_context is skipped and
    # plant_part not yet answered -> pending is "plant_part", a plain guide step.
    doc.crop_id = "75412c1b-14e9-4c1c-b7de-eb74e4bf39b9"
    doc.crop_name = "Achchiq qalampir"
    store.save(doc)

    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    before = len(store.read(DEV, doc.id).messages)
    session.on_turn_committed("farmer", "shunchaki gapiryapman")
    saved = store.read(DEV, doc.id)
    assert len(saved.messages) == before


async def test_set_memory_crops_wired_from_farmer_profile(monkeypatch, tmp_path):
    """contract §4.9: when memory_crop_chips_enabled, the farmer's remembered
    crops are handed to the guide right after load_for_device, so the crop
    question can offer chips."""
    from app.voice.pipeline.memory import FarmerProfile, MemoryStore

    settings = Settings(
        chats_dir=str(tmp_path / "chats"), memory_dir=str(tmp_path / "memory"),
        memory_enabled=True, memory_crop_chips_enabled=True,
        tenant_crops_source="off", enrich_enabled=False,
    )
    store = ChatStore(settings)
    doc = store.create(DEV)

    mem_store = MemoryStore(settings)
    profile = FarmerProfile(name="Aziz", crops=["pomidor", "bodring"])
    mem_store.save(f"dev:{DEV}", profile)
    mem_store.write_index(DEV, f"dev:{DEV}")

    captured: dict = {}
    real_guide_cls = voice_agent.ChatGuide

    class _SpyGuide(real_guide_cls):
        def set_memory_crops(self, crops):
            captured["crops"] = list(crops)
            super().set_memory_crops(crops)

    monkeypatch.setattr(voice_agent, "ChatGuide", _SpyGuide)

    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    assert captured.get("crops") == ["pomidor", "bodring"]


async def test_memory_crops_not_wired_when_chips_disabled(monkeypatch, tmp_path):
    """With both memory chips AND the profile source off, the crop step shows
    only «Ekinlar» — the guide is NEVER seeded, so no cross-session shortlist
    leaks in."""
    from app.voice.pipeline.memory import FarmerProfile, MemoryStore

    settings = Settings(
        chats_dir=str(tmp_path / "chats"), memory_dir=str(tmp_path / "memory"),
        memory_enabled=True, tenant_crops_source="off", enrich_enabled=False,
    )
    store = ChatStore(settings)
    doc = store.create(DEV)

    mem_store = MemoryStore(settings)
    mem_store.save(f"dev:{DEV}", FarmerProfile(name="Aziz", crops=["pomidor", "bodring"]))
    mem_store.write_index(DEV, f"dev:{DEV}")

    captured: dict = {}
    real_guide_cls = voice_agent.ChatGuide

    class _SpyGuide(real_guide_cls):
        def set_memory_crops(self, crops):
            captured["crops"] = list(crops)
            super().set_memory_crops(crops)

    monkeypatch.setattr(voice_agent, "ChatGuide", _SpyGuide)

    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    assert "crops" not in captured  # never seeded when the flag is off


async def test_profile_crops_wired_from_tenant_mock(monkeypatch, tmp_path):
    """tenant_crops_source="mock": the guide is seeded with the farmer's REAL
    Growz crops (the mock stand-in for GET /api/tenant/crops), independent of
    per-farmer memory — this is the "profile crops next to «Ekinlar»" feature."""
    settings = Settings(
        chats_dir=str(tmp_path / "chats"), memory_dir=str(tmp_path / "memory"),
        memory_enabled=False, tenant_crops_source="mock", enrich_enabled=False,
    )
    store = ChatStore(settings)
    doc = store.create(DEV)

    captured: dict = {}
    real_guide_cls = voice_agent.ChatGuide

    class _SpyGuide(real_guide_cls):
        def set_memory_crops(self, crops):
            captured["crops"] = list(crops)
            super().set_memory_crops(crops)

    monkeypatch.setattr(voice_agent, "ChatGuide", _SpyGuide)

    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    assert captured.get("crops") == ["Achchiq qalampir"]


async def test_set_memory_crops_failure_never_breaks_the_session(monkeypatch, tmp_path):
    from app.voice.pipeline.memory import FarmerProfile, MemoryStore

    settings = Settings(
        chats_dir=str(tmp_path / "chats"), memory_dir=str(tmp_path / "memory"),
        memory_enabled=True, memory_crop_chips_enabled=True,
        tenant_crops_source="off", enrich_enabled=False,
    )
    store = ChatStore(settings)
    doc = store.create(DEV)

    mem_store = MemoryStore(settings)
    profile = FarmerProfile(name="Aziz", crops=["pomidor"])
    mem_store.save(f"dev:{DEV}", profile)
    mem_store.write_index(DEV, f"dev:{DEV}")

    real_guide_cls = voice_agent.ChatGuide

    class _BoomGuide(real_guide_cls):
        def set_memory_crops(self, crops):
            raise RuntimeError("boom")

    monkeypatch.setattr(voice_agent, "ChatGuide", _BoomGuide)

    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")  # must not raise

    assert ("start",) in session.calls
    assert session.calls[-1] == ("close",)


async def test_teardown_records_diagnosis_message_and_last_diagnosis(
    monkeypatch, settings
):
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.finished = True
    store.save(doc)

    session = _FakeChatSession()
    session.last_diagnosis = {"disease": "Fitoftoroz", "confidence": "high", "date": "2026-07-11"}
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    saved = store.read(DEV, doc.id)
    assert saved.last_diagnosis == session.last_diagnosis
    assert saved.messages[-1].kind == "diagnosis"
    assert saved.messages[-1].text == "Tashxis: Fitoftoroz (ishonch: high)"


async def test_agronom_offer_set_from_settings_flag_on_chat_setup(monkeypatch, settings):
    """contract addendum P3.6: session.agronom_offer mirrors
    settings.agronom_enabled for chat-bound sessions (fails open with
    diagnosis_kind in the same try-block)."""
    settings.agronom_enabled = True
    store = ChatStore(settings)
    doc = store.create(DEV)

    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    assert session.agronom_offer is True


async def test_agronom_offer_stays_false_when_flag_off(monkeypatch, settings):
    store = ChatStore(settings)
    doc = store.create(DEV)

    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    assert session.agronom_offer is False


class _MidCallWriteWebSocket(_FakeWebSocket):
    """Like _FakeWebSocket, but performs a side-effect write to the chat
    store right before handing back a given message — simulating a REST
    agronom-request landing on disk from another connection while this
    voice session is between WS frames (contract P3.5 "mid-call" timing)."""

    def __init__(self, messages, *, write_before_index: int, on_write) -> None:
        super().__init__(messages)
        self._write_before_index = write_before_index
        self._on_write = on_write
        self._served = 0

    async def receive(self):
        if self._served == self._write_before_index:
            self._on_write()
        self._served += 1
        return await super().receive()


async def test_teardown_kicks_off_mock_review_with_fresh_doc(monkeypatch, settings):
    """contract addendum P3.5 (call site #2): teardown re-reads the chat
    fresh from disk (picking up a mid-call REST agronom-request that the
    in-memory ChatDoc never saw) and passes THAT doc to
    maybe_start_mock_review — in its own try-block, after chat finalize."""
    from app.voice.agronom import review as agronom_review_module
    from app.voice.chat.models import AgronomReview, now_iso

    settings.agronom_enabled = True
    settings.agronom_mock_enabled = True
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.finished = True
    store.save(doc)

    session = _FakeChatSession()
    session.last_diagnosis = {"disease": "Fitoftoroz", "confidence": "high", "date": "2026-07-11"}
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)

    def write_pending_request() -> None:
        # Simulates POST /chats/{id}/agronom-request landing on disk from a
        # separate connection WHILE this voice session is still running —
        # the in-memory chat_doc loaded at session.start predates this.
        mid_call_doc = store.read(DEV, doc.id)
        mid_call_doc.agronom_review = AgronomReview(status="pending", requested_at=now_iso())
        store.save(mid_call_doc)

    captured: list = []

    def fake_maybe_start(settings_arg, store_arg, fresh_doc):
        captured.append(fresh_doc)

    monkeypatch.setattr(
        agronom_review_module, "maybe_start_mock_review", fake_maybe_start
    )

    ws = _MidCallWriteWebSocket(
        [
            _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
        ],
        write_before_index=1,  # write lands AFTER session.start, BEFORE session.end
        on_write=write_pending_request,
    )
    await voice_agent.run_voice_agent(ws, settings, "t")

    assert len(captured) == 1
    fresh_doc = captured[0]
    assert fresh_doc.id == doc.id
    # The fresh doc reflects the mid-call agronom_review the in-memory
    # chat_doc never saw, PLUS the diagnosis persisted by chat finalize.
    assert fresh_doc.agronom_review is not None
    assert fresh_doc.agronom_review.status == "pending"
    assert fresh_doc.last_diagnosis == session.last_diagnosis


async def test_agronom_teardown_kickoff_failure_never_breaks_teardown(
    monkeypatch, settings
):
    """A broken maybe_start_mock_review must never break socket teardown or
    prevent the diagnosis from being persisted (contract P3.11)."""
    from app.voice.agronom import review as agronom_review_module

    settings.agronom_enabled = True
    settings.agronom_mock_enabled = True
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.finished = True
    store.save(doc)

    session = _FakeChatSession()
    session.last_diagnosis = {"disease": "Fitoftoroz", "confidence": "high", "date": "2026-07-11"}
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)

    def boom(settings_arg, store_arg, fresh_doc):
        raise RuntimeError("boom")

    monkeypatch.setattr(agronom_review_module, "maybe_start_mock_review", boom)

    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")  # must not raise

    # The diagnosis must still be persisted despite the agronom kickoff bug.
    saved = store.read(DEV, doc.id)
    assert saved.last_diagnosis == session.last_diagnosis


async def test_diagnosis_kind_set_from_weed_chat_query_type(monkeypatch, settings):
    """contract addendum P2.3: a weed-flow chat's query_type wires
    session.diagnosis_kind to "weed" (-> the weeds catalogue in find_preparations)."""
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.query_type = "weed"
    doc.crop_id = "uuid-bugdoy"
    doc.crop_name = "Bugʻdoy"
    store.save(doc)

    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    assert session.diagnosis_kind == "weed"


async def test_diagnosis_kind_set_from_disease_pest_chat_query_type(monkeypatch, settings):
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.query_type = "disease_pest"
    doc.crop_id = "uuid-pomidor"
    doc.crop_name = "Pomidor"
    store.save(doc)

    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    assert session.diagnosis_kind == "disease_pest"


async def test_diagnosis_kind_stays_default_for_chatless_session(monkeypatch, settings):
    """No chat_id -> chat_doc stays None -> the diagnosis_kind wiring line
    never runs; session keeps its own default ("disease_pest")."""
    session = _FakeChatSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    assert session.diagnosis_kind == "disease_pest"


async def test_teardown_diagnosis_message_carries_preparations_suffix(
    monkeypatch, settings
):
    """contract addendum P2.5: the stored diagnosis message gains the
    ``Preparatlar:`` suffix only when last_diagnosis carries any names."""
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.finished = True
    store.save(doc)

    session = _FakeChatSession()
    session.last_diagnosis = {
        "disease": "Un shudring", "confidence": "high", "date": "2026-07-11",
        "preparations": ["TOPAZ 10% EM.K", "SKOR"],
    }
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    saved = store.read(DEV, doc.id)
    assert saved.last_diagnosis == session.last_diagnosis
    assert saved.messages[-1].text == (
        "Tashxis: Un shudring (ishonch: high) Preparatlar: TOPAZ 10% EM.K, SKOR"
    )


async def test_teardown_diagnosis_message_no_suffix_when_preparations_empty(
    monkeypatch, settings
):
    """Additive field: an absent/empty ``preparations`` key on last_diagnosis
    reproduces the byte-identical v1 stored message (no suffix)."""
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.finished = True
    store.save(doc)

    session = _FakeChatSession()
    session.last_diagnosis = {
        "disease": "Fitoftoroz", "confidence": "high", "date": "2026-07-11",
        "preparations": [],
    }
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    saved = store.read(DEV, doc.id)
    assert saved.messages[-1].text == "Tashxis: Fitoftoroz (ishonch: high)"


async def test_teardown_diagnosis_message_no_suffix_when_preparations_absent(
    monkeypatch, settings
):
    """A v1-shaped last_diagnosis dict (no "preparations" key at all) must
    still produce the byte-identical v1 stored message."""
    store = ChatStore(settings)
    doc = store.create(DEV)
    doc.finished = True
    store.save(doc)

    session = _FakeChatSession()
    session.last_diagnosis = {
        "disease": "Fitoftoroz", "confidence": "high", "date": "2026-07-11",
    }
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "chat.start", "user_id": DEV, "chat_id": doc.id}),
    ])
    await voice_agent.run_voice_agent(ws, settings, "t")

    saved = store.read(DEV, doc.id)
    assert saved.messages[-1].text == "Tashxis: Fitoftoroz (ishonch: high)"
