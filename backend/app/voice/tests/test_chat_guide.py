"""ChatGuide: step ordering (incl. weed skip), tap vs tool answers, invalid
answers ignored, crop matching, resume-at-pending-step, degrade-to-consult.

v2 additions (docs/multichat_contract.md): the symptom dialogue phase, the
general-question phase (incl. server trigger detection + the switch/stay
overlay), memory-crop quick-pick chips, and the records_transcript gate."""
import asyncio

import pytest

import app.voice.enrich as enrich_pkg
from app.config import Settings
from app.voice.chat.guide import ChatGuide, build_guide_prompt_block, has_trigger
from app.voice.chat.models import UZ, ChatMessage, new_chat_doc
from app.voice.chat.store import ChatStore

DEV = "11111111-2222-3333-4444-555555555555"

_CROPS = [
    {"id": "uuid-pomidor", "name": "Pomidor"},
    {"id": "uuid-bodring", "name": "Bodring"},
    {"id": "uuid-behi", "name": "Behi"},
    {"id": "uuid-goza", "name": "Gʻoʻza"},  # real okina (U+02BB) in the catalogue name
]


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.spoken: list[str] = []
        self.injected: list[str] = []
        self.finalized: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def speak_raw(self, text: str) -> None:
        self.spoken.append(text)

    async def inject_context(self, text: str) -> None:
        self.injected.append(text)

    async def finalize_case(self, summary: dict) -> None:
        self.finalized.append(summary)

    def sent_types(self) -> list[str]:
        return [p["type"] for p in self.sent]

    def last(self, etype: str) -> dict:
        return [p for p in self.sent if p["type"] == etype][-1]


@pytest.fixture
def store(tmp_path):
    return ChatStore(Settings(chats_dir=str(tmp_path / "chats")))


def _guide(store, doc, settings=None, *, wire_finalize=False, finalize_case=None):
    rec = _Recorder()
    guide = ChatGuide(
        settings or Settings(), store, doc,
        send_json=rec.send_json, speak_raw=rec.speak_raw,
        inject_context=rec.inject_context,
        finalize_case=(
            finalize_case if finalize_case is not None
            else (rec.finalize_case if wire_finalize else None)
        ),
    )
    return guide, rec


@pytest.fixture(autouse=True)
def _crops(monkeypatch):
    async def _fake_get_crops(settings):
        return _CROPS

    monkeypatch.setattr(enrich_pkg, "get_crops", _fake_get_crops)


async def _drain_background_tasks() -> None:
    """note_farmer_turn schedules its follow-up work (offer / backstop) via
    asyncio.create_task; let those run to completion before asserting."""
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if tasks:
        await asyncio.gather(*tasks)


def _disease_pest_at_symptom(doc) -> None:
    doc.query_type = "disease_pest"
    doc.crop_id = "uuid-pomidor"
    doc.crop_name = "Pomidor"
    doc.plant_part = "leaf"
    # crop_context already resolved (whichever way) -> resume goes straight to
    # symptom, independent of whatever the mock profile lookup says about
    # "Pomidor" (avoids coupling this fixture to the tenant_crops mock data).
    doc.crop_context_done = True


# ---- pending-step derivation ------------------------------------------------


def test_pending_step_order_fresh_doc(store):
    doc = new_chat_doc(DEV)
    guide, _ = _guide(store, doc)
    assert guide.pending_step() == "query_type"


def test_pending_step_progression(store):
    doc = new_chat_doc(DEV)
    guide, _ = _guide(store, doc)
    doc.query_type = "disease_pest"
    assert guide.pending_step() == "crop"
    doc.crop_id = "u1"
    assert guide.pending_step() == "plant_part"
    doc.plant_part = "leaf"
    # crop_in_profile defaults True -> crop_context is skipped.
    assert guide.pending_step() == "symptom"
    doc.symptom_done = True
    assert guide.pending_step() == "photo"
    doc.finished = True
    assert guide.pending_step() is None


def test_pending_step_routes_through_crop_context_for_unsaved_crop(store):
    doc = new_chat_doc(DEV)
    guide, _ = _guide(store, doc)
    doc.query_type = "disease_pest"
    doc.crop_id = "u1"
    doc.crop_in_profile = False
    # crop_context comes BEFORE plant_part in the new order (§1.3 sits right
    # after crop, ahead of plant_part) — fires immediately, even with
    # plant_part still unset.
    assert guide.pending_step() == "crop_context"
    doc.crop_context_done = True
    assert guide.pending_step() == "plant_part"
    doc.plant_part = "leaf"
    assert guide.pending_step() == "symptom"


def test_pending_step_skips_plant_part_and_symptom_for_weed(store):
    doc = new_chat_doc(DEV)
    guide, _ = _guide(store, doc)
    doc.query_type = "weed"
    doc.crop_id = "u1"
    doc.crop_in_profile = False  # even "unsaved" -> weed never touches crop_context
    # plant_part, crop_context AND symptom stay skipped for weed -> photo.
    assert guide.pending_step() == "photo"


def test_pending_step_general_never_finishes_on_its_own(store):
    doc = new_chat_doc(DEV)
    guide, _ = _guide(store, doc)
    doc.query_type = "general"
    assert guide.pending_step() == "general"
    # only an explicit finish (degrade, or switch away from "general") ends it
    doc.finished = True
    assert guide.pending_step() is None


# ---- start() -----------------------------------------------------------


async def test_start_new_chat_emits_state_question_and_script_a(store):
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    await guide.start()
    assert rec.sent_types() == ["chat.step", "chat.question"]
    state = rec.sent[0]
    # chat.state merged into chat.step: the connect snapshot is a chat.step
    # with an EMPTY option_id carrying phase + selections.
    assert state["type"] == "chat.step"
    assert state.get("option_id", "") == ""
    # Lean wire: empty selections are omitted entirely on a fresh chat.
    assert {"chat_id": doc.id, "phase": "guide"}.items() <= state.items()
    assert "selections" not in state
    assert state["step_id"] == "query_type"
    question = rec.sent[1]
    assert question["step_id"] == "query_type"
    assert question["kind"] == "buttons"
    assert question["prompt"] == "Nima boʻyicha maslahat kerak?"
    assert question["options"] == [
        {"id": "disease_pest", "label": "Kasalliklar va zararkunandalar"},
        {"id": "weed", "label": "Begona oʻt"},
        {"id": "general", "label": "Umumiy savol berish"},
    ]
    assert len(rec.spoken) == 1
    assert rec.spoken[0].startswith("[TIZIM][SAVOL step=query_type]")
    assert "select_option" in rec.spoken[0]
    # The question prompt is recorded as a structured message (contract §4.7).
    assert doc.messages[-1].role == "rais" and doc.messages[-1].kind == "question"


async def test_start_resumed_finished_chat_emits_consult_and_script_f(store):
    doc = new_chat_doc(DEV)
    doc.finished = True
    doc.title = "Pomidor — kasallik"
    guide, rec = _guide(store, doc)
    await guide.start()
    assert rec.sent_types() == ["chat.step"]
    assert rec.sent[0]["phase"] == "consult"
    assert len(rec.spoken) == 1
    assert rec.spoken[0].startswith("[TIZIM] Fermer avvalgi suhbatga qaytdi.")
    assert "Pomidor — kasallik" in rec.spoken[0]


async def test_start_resumes_mid_guide_at_the_correct_pending_step(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"  # already answered before the disconnect
    guide, rec = _guide(store, doc)
    await guide.start()
    question = rec.last("chat.question")
    assert question["step_id"] == "crop"


async def test_crop_step_rejects_the_open_crop_picker_sentinel(store):
    # "open_crop_picker" is the UI button that OPENS the catalogue, not a crop.
    # A client that echoes it as chat.answer (the WS tester did, 2026-08-05)
    # must be ignored like any invalid answer — otherwise it persists as
    # crop_id and the diagnosis later runs against a crop named "Ekinlar".
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    await guide.start()
    await guide.on_answer("query_type", "disease_pest", "")
    steps_before = len([e for e in rec.sent if e.get("type") == "chat.step"])

    await guide.on_answer("crop", "open_crop_picker", "Ekinlar")

    assert doc.crop_id == ""
    assert doc.crop_name == ""
    assert guide.pending_step() == "crop", "the step must not advance"
    steps_after = len([e for e in rec.sent if e.get("type") == "chat.step"])
    assert steps_after == steps_before, "no ack for a rejected answer"


async def test_crop_question_offers_ha_yoq_when_profile_has_chips(store):
    # The «Ha»/«Yoʻq» pair is SERVER-driven: with profile chips resolved the
    # crop question first offers exactly the two buttons, not the chips.
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    guide._crop_chips = [{"id": "uuid-pomidor", "label": "Pomidor"}]
    await guide.start()
    await guide.on_answer("query_type", "disease_pest", "")

    q = rec.last("chat.question")
    assert q["step_id"] == "crop"
    assert q["kind"] == "crop_picker"
    assert [o["id"] for o in q["options"]] == ["saved_crops", "open_crop_picker"]
    assert [o["label"] for o in q["options"]] == ["Ha", "Yoʻq"]


async def test_saved_crops_answer_expands_chips_without_advancing(store):
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    guide._crop_chips = [{"id": "uuid-pomidor", "label": "Pomidor"}]
    await guide.start()
    await guide.on_answer("query_type", "disease_pest", "")

    await guide.on_answer("crop", "saved_crops", "")

    q = rec.last("chat.question")
    assert q["step_id"] == "crop"
    # Chips only — «Yoʻq» would contradict the «Ha» just answered.
    assert [o["id"] for o in q["options"]] == ["uuid-pomidor"]
    assert guide.pending_step() == "crop", "expanding chips must not advance"
    assert doc.crop_id == "", "saved_crops must never persist as a crop"


async def test_crop_question_always_offers_ha_yoq_even_with_empty_profile(store):
    # The pair is unconditional — the question literally asks yes/no, so both
    # buttons come regardless of the profile. «Ha» on an empty profile then
    # resolves to the catalogue-only view.
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    guide._crop_chips = []
    await guide.start()
    await guide.on_answer("query_type", "disease_pest", "")

    q = rec.last("chat.question")
    assert [o["id"] for o in q["options"]] == ["saved_crops", "open_crop_picker"]

    await guide.on_answer("crop", "saved_crops", "")
    q = rec.last("chat.question")
    assert [o["id"] for o in q["options"]] == ["open_crop_picker"]
    assert q["options"][0]["label"] == "Ekinlar"
    assert guide.pending_step() == "crop"


async def test_crop_answer_new_shape_carries_the_crop_as_an_object(store):
    # New payload: option_id names the tapped button (open_crop_picker), the
    # picked crop rides as crop={id,name}. The sentinel in option_id must NOT
    # be rejected when the crop object carries real data.
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    await guide.start()
    await guide.on_answer("query_type", "disease_pest", "")

    await guide.on_answer(
        "crop", "open_crop_picker", "",
        crop={"id": "uuid-pomidor", "name": "Pomidor"},
    )

    assert doc.crop_id == "uuid-pomidor"
    assert doc.crop_name == "Pomidor"
    step = rec.last("chat.step")
    assert step["option_id"] == "uuid-pomidor"
    assert step["label"] == "Pomidor"


async def test_crop_answer_new_shape_with_empty_crop_id_is_rejected(store):
    # A crop object without an id is as invalid as the bare sentinel.
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    await guide.start()
    await guide.on_answer("query_type", "disease_pest", "")

    await guide.on_answer(
        "crop", "open_crop_picker", "", crop={"id": "", "name": "Pomidor"}
    )

    assert doc.crop_id == ""
    assert guide.pending_step() == "crop"


async def test_crop_commit_injects_saved_planting_profile(store):
    # Committing a SAVED crop pushes its profile into the model's context so
    # Rais can answer «ekinim haqida nima bilasan?» during the chat.
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc, Settings(tenant_crops_source="mock"))
    await guide.start()
    await guide.on_answer("query_type", "disease_pest", "")

    await guide.on_answer("crop", "75412c1b-14e9-4c1c-b7de-eb74e4bf39b9", "Achchiq qalampir")

    assert rec.injected, "profile was not injected"
    text = rec.injected[-1]
    assert "Growz profilidan" in text
    assert "ekin: Achchiq qalampir" in text
    assert "oʻsish davri: 60-90 kun" in text


async def test_crop_context_prompt_for_unsaved_crop(store):
    # A crop NOT in the Growz profile -> crop routes DIRECTLY into the §1.3
    # crop_context phase (BEFORE plant_part); Rais asks viloyat/tuman,
    # ekish sanasi, faza, oxirgi agrotexnik ishlar. No hidden turn is injected.
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc, Settings(tenant_crops_source="mock"))
    await guide.start()
    await guide.on_answer("query_type", "disease_pest", "")
    await guide.on_answer("crop", "uuid-pomidor", "Pomidor")  # not in the mock -> crop_context
    assert doc.crop_in_profile is False
    assert guide.pending_step() == "crop_context"
    assert rec.sent[-1]["type"] == "chat.step"
    assert rec.sent[-1]["phase"] == "crop_context"
    # Team decision: the anketa emits NO chat.question — voice/subtitles only.
    assert not [
        q for q in rec.sent
        if q.get("type") == "chat.question" and q.get("step_id") == "crop_context"
    ]
    prompt = rec.spoken[-1]
    assert "[SAVOL step=crop_context]" in prompt
    # The model is told to voice ONLY the current question — the other three
    # must not be in the brief (the reminder mentions "agrotexnika", so match
    # the full question texts, not bare words).
    assert "Qaysi viloyat va tumandasiz?" in prompt
    assert "Ekin qachon ekilgan?" not in prompt
    assert "Oxirgi agrotexnik ishlar qachon va qanday boʻlgan?" not in prompt
    # BUG 1: the exact spec reminder sentence, said first, verbatim.
    assert (
        "Keyingi safar ekinni profilingizga qoʻshib qoʻying. Shunda faza, "
        "agrotexnika va tarixni hisobga olib, aniqroq tavsiya bera olaman."
    ) in prompt
    assert "aynan shu jumla bilan boshla" in prompt
    assert rec.injected == []  # unsaved crop -> no profile hidden turn


async def test_crop_context_skipped_for_saved_crop(store):
    # A SAVED crop -> crop routes straight into plant_part, skipping
    # crop_context entirely; plant_part then routes straight into symptom.
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc, Settings(tenant_crops_source="mock"))
    await guide.start()
    await guide.on_answer("query_type", "disease_pest", "")
    await guide.on_answer(
        "crop", "75412c1b-14e9-4c1c-b7de-eb74e4bf39b9", "Achchiq qalampir"
    )
    assert doc.crop_in_profile is True
    assert guide.pending_step() == "plant_part"
    assert rec.last("chat.question")["step_id"] == "plant_part"
    await guide.on_answer("plant_part", "leaf", "")
    assert guide.pending_step() == "symptom"
    prompt = rec.spoken[-1]
    assert "[SAVOL step=symptom]" in prompt
    assert "profilida saqlanmagan" not in prompt
    assert all(
        p.get("step_id") != "crop_context"
        for p in rec.sent if p["type"] == "chat.question"
    )


# ---- tap path (chat.answer -> on_answer) --------------------------------


async def test_on_answer_full_happy_path_disease_pest_with_photo_skip(store):
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    await guide.start()

    await guide.on_answer("query_type", "disease_pest", "")
    assert doc.query_type == "disease_pest"
    step_evt = rec.last("chat.step")
    assert step_evt.items() >= {
        "type": "chat.step", "chat_id": doc.id, "step_id": "query_type",
        "option_id": "disease_pest", "label": "Kasalliklar va zararkunandalar",
    }.items()
    assert rec.last("chat.question")["step_id"] == "crop"
    assert rec.spoken[-1].startswith("[TIZIM] Fermer tugma orqali")
    assert "[SAVOL step=crop]" in rec.spoken[-1]

    await guide.on_answer("crop", "uuid-pomidor", "Pomidor")
    assert doc.crop_id == "uuid-pomidor"
    assert doc.crop_name == "Pomidor"
    # "Pomidor" is not in the (mock) Growz profile -> crop routes DIRECTLY
    # into the §1.3 crop_context phase (BEFORE plant_part).
    assert doc.crop_in_profile is False
    assert rec.sent_types()[-1] == "chat.step"
    assert rec.sent[-1]["phase"] == "crop_context"
    # anketa chat.question yubormaydi — savol faqat ovozda
    assert rec.spoken[-1].startswith("[TIZIM] Fermer tugma orqali «Pomidor»")
    assert "[SAVOL step=crop_context]" in rec.spoken[-1]

    await guide.on_answer("crop_context", "to_symptom", "")
    assert doc.crop_context_done is True
    # crop_context -> plant_part (the FIRST §2 question), mirroring how
    # switch_to_diagnostic re-enters at the crop step.
    assert rec.sent_types()[-2:] == ["chat.step", "chat.question"]
    assert rec.sent[-2]["phase"] == "guide"
    assert rec.last("chat.question")["step_id"] == "plant_part"
    assert rec.spoken[-1].startswith("[TIZIM] Fermer tugma orqali «Belgilarga oʻtish»")
    assert "[SAVOL step=plant_part]" in rec.spoken[-1]

    await guide.on_answer("plant_part", "leaf", "")
    assert doc.plant_part == "leaf"
    assert rec.sent_types()[-2:] == ["chat.step", "chat.question"]
    assert rec.sent[-2]["phase"] == "symptom"
    assert rec.last("chat.question")["step_id"] == "symptom"
    assert rec.last("chat.question")["kind"] == "symptom"
    assert rec.spoken[-1].startswith("[TIZIM] Fermer tugma orqali «Bargida»")
    assert "[SAVOL step=symptom]" in rec.spoken[-1]

    await guide.on_answer("symptom", "to_photo", "")
    assert doc.symptom_done is True
    step_evt = rec.last("chat.step")
    assert step_evt.items() >= {
        "type": "chat.step", "chat_id": doc.id, "step_id": "symptom",
        "option_id": "to_photo", "label": "Rasmga oʻtish",
    }.items()
    assert rec.sent_types()[-2:] == ["chat.step", "chat.question"]
    assert rec.sent[-2]["phase"] == "guide"
    assert rec.last("chat.question")["step_id"] == "photo"

    # A photo is mandatory (2026-08-05): send one, then close with «Tayyor».
    await guide.on_photo_received("photo-1", None)
    await guide.on_answer("photo", "done_photos", "")
    assert doc.finished is True
    assert rec.sent_types()[-1] == "chat.step"
    assert rec.sent[-1]["phase"] == "consult"
    assert rec.spoken[-1].startswith("[TIZIM] Yoʻriqli soʻrov tugadi.")
    assert "ekin — Pomidor" in rec.spoken[-1]
    assert "muammo turi — Kasalliklar va zararkunandalar" in rec.spoken[-1]
    assert "qismi — Bargida" in rec.spoken[-1]
    assert "belgilar — suhbatda aytilgan" in rec.spoken[-1]
    assert "yigʻilgan belgilar va rasm asosida" in rec.spoken[-1]
    assert "rasm yoʻq" not in rec.spoken[-1], "a photo was sent"
    # Chat title recomputed from the accepted selections.
    assert doc.title == "Pomidor — kasallik"
    # finalize_case=None (default wiring) -> the deterministic finalize is a
    # no-op; skip still finishes/consults exactly as before.
    assert rec.finalized == []


async def test_weed_flow_skips_plant_part_and_photo_clause_says_rasm_keldi(store):
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc, wire_finalize=True)
    await guide.start()
    await guide.on_answer("query_type", "weed", "")
    assert rec.last("chat.question")["step_id"] == "crop"
    await guide.on_answer("crop", "uuid-bodring", "Bodring")
    # plant_part skipped entirely -> goes straight to photo.
    assert rec.last("chat.question")["step_id"] == "photo"
    # A single photo no longer finishes: the loop re-shows the photo bar and
    # nudges the farmer for more / «Tayyor».
    await guide.on_photo_received("photo-7")
    assert doc.finished is False
    assert doc.photos_collected == 1
    assert rec.last("chat.question")["step_id"] == "photo"
    assert rec.sent[-1]["type"] == "chat.question"
    assert rec.spoken[-1].startswith("[TIZIM] Rasm qabul qilindi.")
    assert rec.finalized == []
    # «Tayyor» ends the loop -> deterministic diagnosis fires, chat finishes.
    await guide.on_answer("photo", "done_photos", "")
    assert doc.finished is True
    assert len(rec.finalized) == 1
    assert rec.finalized[0]["crop"] == "Bodring"
    assert rec.finalized[0]["farmer_language"] == "uz"
    assert "qismi —" not in rec.spoken[-1]
    assert "rasm keldi" in rec.spoken[-1]


async def test_on_answer_wrong_step_id_ignored(store):
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    await guide.start()
    rec.sent.clear()
    rec.spoken.clear()
    await guide.on_answer("crop", "uuid-pomidor", "Pomidor")  # pending is query_type
    assert doc.query_type == ""
    assert rec.sent == []
    assert rec.spoken == []


async def test_on_answer_unknown_option_ignored(store):
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    await guide.start()
    rec.sent.clear()
    await guide.on_answer("query_type", "not_a_real_option", "")
    assert doc.query_type == ""
    assert rec.sent == []


async def test_on_answer_after_finished_is_noop(store):
    doc = new_chat_doc(DEV)
    doc.finished = True
    guide, rec = _guide(store, doc)
    await guide.on_answer("query_type", "disease_pest", "")
    assert doc.query_type == ""
    assert rec.sent == []


# ---- photo step specifics --------------------------------------------------


async def test_on_photo_received_noop_when_not_pending_photo(store):
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    await guide.start()  # pending is query_type, not photo
    await guide.on_photo_received("photo-1")
    assert doc.finished is False
    assert not any(m.kind == "photo" for m in doc.messages)


# camera.cancelled left the protocol (2026-08-05): request_photo is acked
# immediately, so nothing server-side waits on the camera and the bar never
# went away — the event had no remaining job.


def _disease_pest_at_photo(doc) -> None:
    doc.query_type = "disease_pest"
    doc.crop_id = "uuid-pomidor"
    doc.crop_name = "Pomidor"
    doc.plant_part = "leaf"
    doc.symptom_done = True
    doc.symptom_summary = "Barglarda sargʻish dogʻlar"


async def test_photo_loop_re_emits_and_does_not_finish(store):
    # Each accepted photo (below the cap) re-shows the photo bar and nudges the
    # farmer — it must NOT finish, and must NOT add a duplicate question row.
    doc = new_chat_doc(DEV)
    _disease_pest_at_photo(doc)
    guide, rec = _guide(store, doc, wire_finalize=True)
    await guide.start()  # emits + records the photo question once
    question_messages_before = [m for m in doc.messages if m.kind == "question"]

    for n in range(1, 5):
        rec.sent.clear()
        rec.spoken.clear()
        await guide.on_photo_received(f"photo-{n}")
        assert doc.photos_collected == n
        assert doc.finished is False
        assert rec.finalized == []
        # a [rasm] farmer message was appended this turn.
        assert doc.messages[-1].role == "farmer"
        assert doc.messages[-1].kind == "photo"
        assert doc.messages[-1].text == UZ["photoMarker"]
        # chat.step{photo} then re-emitted chat.question{photo}.
        types = [p["type"] for p in rec.sent]
        assert "chat.step" in types
        assert rec.sent[-1]["type"] == "chat.question"
        assert rec.sent[-1]["step_id"] == "photo"
        assert rec.spoken[-1].startswith("[TIZIM] Rasm qabul qilindi.")

    # No duplicate question transcript rows despite 4 re-emits.
    question_messages_after = [m for m in doc.messages if m.kind == "question"]
    assert len(question_messages_after) == len(question_messages_before)


async def test_done_photos_after_two_photos_finalizes_and_finishes(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_photo(doc)
    guide, rec = _guide(store, doc, wire_finalize=True)
    await guide.start()
    await guide.on_photo_received("photo-1")
    await guide.on_photo_received("photo-2")
    assert doc.photos_collected == 2
    assert doc.finished is False
    assert rec.finalized == []

    await guide.on_answer("photo", "done_photos", "")
    assert doc.finished is True
    assert len(rec.finalized) == 1
    summary = rec.finalized[0]
    assert summary["crop"] == "Pomidor"
    assert summary["symptoms"] == "Barglarda sargʻish dogʻlar"
    assert summary["farmer_language"] == "uz"
    assert summary["location_on_plant"] == "Bargida"
    assert rec.last("chat.step")["phase"] == "consult"


async def test_skip_is_rejected_a_photo_is_mandatory(store):
    # Team decision (2026-08-05): the interview cannot finish photo-less.
    doc = new_chat_doc(DEV)
    _disease_pest_at_photo(doc)
    guide, rec = _guide(store, doc, wire_finalize=True)
    await guide.start()
    await guide.on_answer("photo", "skip", "")
    assert doc.photos_collected == 0
    assert doc.finished is False, "skip must not finish the interview"
    assert rec.finalized == [], "skip must not trigger a diagnosis"
    assert guide.pending_step() == "photo"


async def test_done_photos_with_zero_photos_is_rejected(store):
    # …and done_photos must not become a back-door skip.
    doc = new_chat_doc(DEV)
    _disease_pest_at_photo(doc)
    guide, rec = _guide(store, doc, wire_finalize=True)
    await guide.start()
    await guide.on_answer("photo", "done_photos", "")
    assert doc.finished is False
    assert rec.finalized == []
    assert guide.pending_step() == "photo"


async def test_done_photos_button_hidden_until_a_photo_lands(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_photo(doc)
    guide, rec = _guide(store, doc, wire_finalize=True)
    await guide.start()
    assert [o["id"] for o in rec.last("chat.question")["options"]] == ["take_photo"]

    await guide.on_photo_received("photo-1", None)
    assert [o["id"] for o in rec.last("chat.question")["options"]] == [
        "take_photo", "done_photos",
    ]


async def test_photo_cap_auto_finalizes_at_five(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_photo(doc)
    guide, rec = _guide(store, doc, wire_finalize=True)
    await guide.start()
    for n in range(1, 6):
        await guide.on_photo_received(f"photo-{n}")
    assert doc.photos_collected == 5
    assert doc.finished is True
    assert len(rec.finalized) == 1
    # start() emitted the photo question once, photos 1-4 re-emitted it (4x);
    # the 5th photo finalizes WITHOUT re-emitting -> no 6th photo question.
    photo_questions = [
        p for p in rec.sent if p["type"] == "chat.question" and p["step_id"] == "photo"
    ]
    assert len(photo_questions) == 5


async def test_done_photos_is_none_safe_without_a_finalize_callback(store):
    # finalize_case=None (default) -> done_photos still finishes, no raise.
    doc = new_chat_doc(DEV)
    _disease_pest_at_photo(doc)
    guide, rec = _guide(store, doc)  # no finalize wired
    await guide.start()
    await guide.on_photo_received("photo-1", None)
    await guide.on_answer("photo", "done_photos", "")
    assert doc.finished is True
    assert guide.degraded is False
    assert rec.finalized == []


async def test_finalize_callback_failure_degrades_to_consult(store):
    # A raising finalize callback must fail-open: the guide degrades, and the
    # last chat.state carries the consult phase (via _degrade).
    async def _boom(summary):
        raise RuntimeError("diagnosis down")

    doc = new_chat_doc(DEV)
    _disease_pest_at_photo(doc)
    guide, rec = _guide(store, doc, finalize_case=_boom)
    await guide.start()
    await guide.on_photo_received("photo-1", None)
    await guide.on_answer("photo", "done_photos", "")
    assert guide.degraded is True
    assert rec.last("chat.step")["phase"] == "consult"


async def test_done_photos_voice_path_records_and_finalizes(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_photo(doc)
    guide, rec = _guide(store, doc, wire_finalize=True)
    await guide.start()
    await guide.on_photo_received("photo-1")
    result = await guide.handle_tool(
        "select_option", {"step_id": "photo", "option_id": "done_photos"}
    )
    assert result["status"] == "recorded"
    assert doc.finished is True
    assert len(rec.finalized) == 1
    assert rec.finalized[0]["crop"] == "Pomidor"


async def test_photos_collected_persists_to_store(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_photo(doc)
    guide, _ = _guide(store, doc, wire_finalize=True)
    await guide.start()
    await guide.on_photo_received("photo-1")
    await guide.on_photo_received("photo-2")
    reloaded = store.read(doc.user_id, doc.id)
    assert reloaded is not None
    assert reloaded.photos_collected == 2


# ---- voice path (select_option tool -> handle_tool) ------------------------


async def test_handle_tool_ignores_other_tool_names(store):
    doc = new_chat_doc(DEV)
    guide, _ = _guide(store, doc)
    assert await guide.handle_tool("request_photo", {}) is None


async def test_handle_tool_records_valid_answer(store):
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool(
        "select_option", {"step_id": "query_type", "option_id": "disease_pest"}
    )
    assert result["status"] == "recorded"
    assert "Qabul qilindi" in result["note"]
    assert "[SAVOL step=crop]" in result["note"]
    assert doc.query_type == "disease_pest"
    # Voice-advance transition goes into the ack note, never spoken directly.
    assert len(rec.spoken) == 1  # only the initial script A from start()


async def test_handle_tool_crop_folds_profile_into_note(store, monkeypatch):
    # Voice path: selecting a SAVED crop folds its profile into the tool-ack
    # note (the model reads it as the function response — no separate turn,
    # which would be unsafe while a tool call is pending).
    async def _cat(settings):
        return [{"id": "75412c1b-14e9-4c1c-b7de-eb74e4bf39b9", "name": "Achchiq qalampir"}]

    monkeypatch.setattr(enrich_pkg, "get_crops", _cat)
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc, Settings(tenant_crops_source="mock"))
    await guide.start()
    await guide.handle_tool(
        "select_option", {"step_id": "query_type", "option_id": "disease_pest"}
    )
    result = await guide.handle_tool(
        "select_option",
        {
            "step_id": "crop",
            "option_id": "75412c1b-14e9-4c1c-b7de-eb74e4bf39b9",
            "value": "Achchiq qalampir",
        },
    )
    assert result["status"] == "recorded"
    assert "Growz profilidan" in result["note"]
    assert "ekin: Achchiq qalampir" in result["note"]
    assert rec.injected == []  # voice path must NOT push a separate context turn


async def test_handle_tool_invalid_step_mismatch(store):
    doc = new_chat_doc(DEV)
    guide, _ = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool(
        "select_option", {"step_id": "crop", "option_id": "uuid-pomidor"}
    )
    assert result["status"] == "invalid"
    assert "[SAVOL step=query_type]" in result["note"]
    assert doc.query_type == ""


async def test_handle_tool_invalid_unknown_option(store):
    doc = new_chat_doc(DEV)
    guide, _ = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool(
        "select_option", {"step_id": "query_type", "option_id": "bogus"}
    )
    assert result["status"] == "invalid"


async def test_handle_tool_after_finished(store):
    doc = new_chat_doc(DEV)
    doc.finished = True
    guide, _ = _guide(store, doc)
    result = await guide.handle_tool("select_option", {"step_id": "query_type"})
    assert result["status"] == "invalid"


# ---- crop matching (voice path only) ---------------------------------------


async def test_handle_tool_crop_exact_match(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    guide, _ = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool(
        "select_option", {"step_id": "crop", "value": "pomidor"}
    )
    assert result["status"] == "recorded"
    assert doc.crop_id == "uuid-pomidor"
    assert doc.crop_name == "Pomidor"


async def test_handle_tool_crop_okina_normalisation_match(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    guide, _ = _guide(store, doc)
    await guide.start()
    # Catalogue name has a real okina (ʻ); the scribe's transcript of the
    # farmer's speech uses a plain apostrophe — both sides normalise the same.
    result = await guide.handle_tool(
        "select_option", {"step_id": "crop", "value": "g'o'za"}
    )
    assert result["status"] == "recorded"
    assert doc.crop_id == "uuid-goza"


async def test_handle_tool_crop_unique_substring_match(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    guide, _ = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool(
        "select_option", {"step_id": "crop", "value": "pomidorim"}  # contains "pomidor"
    )
    assert result["status"] == "recorded"
    assert doc.crop_id == "uuid-pomidor"


async def test_handle_tool_crop_no_match_is_invalid(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    guide, _ = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool(
        "select_option", {"step_id": "crop", "value": "traktor"}
    )
    assert result["status"] == "invalid"
    assert "Ekinlar" in result["note"]
    assert doc.crop_id == ""


async def test_handle_tool_crop_empty_catalogue_is_invalid(store, monkeypatch):
    async def _empty(settings):
        return []

    monkeypatch.setattr(enrich_pkg, "get_crops", _empty)
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    guide, _ = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool("select_option", {"step_id": "crop", "value": "pomidor"})
    assert result["status"] == "invalid"


async def test_handle_tool_crop_fetch_error_is_invalid_not_raised(store, monkeypatch):
    async def _boom(settings):
        raise RuntimeError("growz down")

    monkeypatch.setattr(enrich_pkg, "get_crops", _boom)
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    guide, _ = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool("select_option", {"step_id": "crop", "value": "pomidor"})
    assert result["status"] == "invalid"
    assert guide.degraded is False  # a failed crop match is not a guide-level error


# ---- degrade-to-consult on error -------------------------------------------


async def test_degrade_via_broken_step_table(store, monkeypatch):
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    await guide.start()

    import app.voice.chat.guide as guide_mod

    monkeypatch.setattr(guide_mod, "STEPS", {})  # any lookup now raises KeyError
    await guide.on_answer("query_type", "disease_pest", "")
    assert guide.degraded is True
    assert doc.finished is True
    assert rec.sent[-1]["type"] == "chat.step"
    assert rec.sent[-1]["phase"] == "consult"


async def test_handle_tool_degrades_on_unexpected_error(store, monkeypatch):
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    await guide.start()

    import app.voice.chat.guide as guide_mod

    monkeypatch.setattr(guide_mod, "STEPS", {})
    result = await guide.handle_tool("select_option", {"step_id": "query_type"})
    assert result["status"] == "invalid"
    assert guide.degraded is True
    assert doc.finished is True


# ---- prompt block (contract §4.5) ------------------------------------------


def test_prompt_block_unfinished_chat_is_the_static_policy():
    doc = new_chat_doc(DEV)
    block = build_guide_prompt_block(doc)
    assert block.startswith("[YOʻRIQLI SOʻROV]")
    assert "select_option" in block


def test_prompt_block_finished_chat_is_history_recap():
    doc = new_chat_doc(DEV)
    doc.finished = True
    doc.crop_name = "Pomidor"
    doc.query_type = "disease_pest"
    doc.plant_part = "leaf"
    doc.messages = [
        ChatMessage(role="farmer", kind="text", text="Salom"),
        ChatMessage(role="rais", kind="text", text="Assalomu alaykum"),
    ]
    block = build_guide_prompt_block(doc)
    assert block.startswith("[SUHBAT TARIXI]")
    assert "ekin — Pomidor" in block
    assert "muammo — Kasalliklar va zararkunandalar" in block
    assert "qismi — Bargida" in block
    assert "FERMER: Salom" in block
    assert "RAIS: Assalomu alaykum" in block


def test_prompt_block_guide_policy_gained_the_two_v2_sentences():
    doc = new_chat_doc(DEV)
    block = build_guide_prompt_block(doc)
    assert block.startswith("[YOʻRIQLI SOʻROV]")
    assert "Umumiy savol berish" in block
    # §1.3 mirror: the policy names BOTH functions (to_symptom for the
    # crop-context phase, to_photo for the symptom phase).
    assert "to_symptom" in block and "to_photo" in block


def test_prompt_block_unfinished_diagnostic_without_messages_has_no_recap():
    doc = new_chat_doc(DEV)
    block = build_guide_prompt_block(doc)
    assert "[SUHBAT TARIXI]" not in block


def test_prompt_block_unfinished_diagnostic_with_messages_appends_recap():
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    doc.crop_name = "Pomidor"
    doc.plant_part = "leaf"
    doc.messages = [ChatMessage(role="farmer", kind="text", text="Bargim sarg'aydi")]
    block = build_guide_prompt_block(doc)
    assert block.startswith("[YOʻRIQLI SOʻROV]")
    assert "[SUHBAT TARIXI]" in block
    assert "FERMER: Bargim sarg'aydi" in block


def test_prompt_block_general_unfinished_chat_is_general_policy():
    doc = new_chat_doc(DEV)
    doc.query_type = "general"
    block = build_guide_prompt_block(doc)
    assert block.startswith("[UMUMIY SAVOL]")
    assert "switch_to_diagnostic" in block
    assert "[SUHBAT TARIXI]" not in block
    # Trigger words are taught in BOTH scripts so the model's own detection
    # matches the server-side has_trigger backstop.
    assert "kasallik" in block and "касаллик" in block
    assert "zararkunanda" in block and "зараркунанда" in block


def test_prompt_block_general_unfinished_chat_with_messages_appends_recap():
    doc = new_chat_doc(DEV)
    doc.query_type = "general"
    doc.messages = [ChatMessage(role="farmer", kind="text", text="Savolim bor")]
    block = build_guide_prompt_block(doc)
    assert block.startswith("[UMUMIY SAVOL]")
    assert "[SUHBAT TARIXI]" in block
    assert "FERMER: Savolim bor" in block


# ---- start() resume matrix (contract §4.11) --------------------------------


async def test_start_resumes_at_symptom_step_emits_symptom_phase_and_speaks_SY_A(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_symptom(doc)
    guide, rec = _guide(store, doc)
    await guide.start()
    assert rec.sent_types() == ["chat.step", "chat.question"]
    assert rec.sent[0]["phase"] == "symptom"
    question = rec.last("chat.question")
    assert question["step_id"] == "symptom"
    assert question["kind"] == "symptom"
    # BUG 2: the advance button is hidden until _SYMPTOM_CHIP_AFTER farmer
    # turns have accrued in this phase (resets on resume — runtime-only).
    assert question["options"] == []
    assert len(rec.spoken) == 1
    assert rec.spoken[0].startswith("[TIZIM][SAVOL step=symptom]")
    assert "to_photo funksiyasini chaqir" in rec.spoken[0]


async def test_start_resumes_at_general_step_emits_general_phase_and_speaks_G_A(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "general"
    doc.title = "Umumiy savol"
    guide, rec = _guide(store, doc)
    await guide.start()
    assert rec.sent_types() == ["chat.step", "chat.question"]
    assert rec.sent[0]["phase"] == "general"
    question = rec.last("chat.question")
    assert question["step_id"] == "general"
    assert question["kind"] == "free"
    assert question["options"] == []
    assert len(rec.spoken) == 1
    assert rec.spoken[0].startswith(
        "[TIZIM] Fermer avvalgi umumiy savol suhbatiga qaytdi (Umumiy savol)."
    )


# ---- symptom phase (contract §4.4 a/b, §4.6 SY, §4.8) -----------------------


async def test_symptom_to_photo_tool_records_summary_and_advances(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_symptom(doc)
    guide, rec = _guide(store, doc)
    await guide.start()  # pending is symptom
    result = await guide.handle_tool(
        "to_photo", {"summary": "Barglarda sargʻish dogʻlar, 3 kundan beri"}
    )
    assert result["status"] == "recorded"
    assert doc.symptom_done is True
    assert doc.symptom_summary == "Barglarda sargʻish dogʻlar, 3 kundan beri"
    step_evt = rec.last("chat.step")
    assert step_evt.items() >= {
        "type": "chat.step", "chat_id": doc.id, "step_id": "symptom",
        "option_id": "to_photo", "value": "Barglarda sargʻish dogʻlar, 3 kundan beri",
        "label": "Rasmga oʻtish",
    }.items()
    assert rec.sent[-2]["type"] == "chat.step" and rec.sent[-2]["phase"] == "guide"
    assert rec.last("chat.question")["step_id"] == "photo"
    assert "Qabul qilindi" in result["note"]
    assert "[SAVOL step=photo]" in result["note"]


async def test_symptom_tap_button_advances_even_without_a_summary(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_symptom(doc)
    guide, rec = _guide(store, doc)
    await guide.start()
    await guide.on_answer("symptom", "to_photo", "")
    assert doc.symptom_done is True
    assert doc.symptom_summary == ""
    assert rec.last("chat.question")["step_id"] == "photo"
    assert rec.spoken[-1].startswith("[TIZIM] Fermer tugma orqali «Rasmga oʻtish»")
    assert "[SAVOL step=photo]" in rec.spoken[-1]


async def test_symptom_answer_with_wrong_option_id_is_ignored(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_symptom(doc)
    guide, rec = _guide(store, doc)
    await guide.start()
    rec.sent.clear()
    rec.spoken.clear()
    await guide.on_answer("symptom", "something_else", "")
    assert doc.symptom_done is False
    assert rec.sent == []
    assert rec.spoken == []


async def test_symptom_backstop_fires_after_12_farmer_turns(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_symptom(doc)
    guide, rec = _guide(store, doc)
    await guide.start()
    for i in range(11):
        guide.note_farmer_turn(f"belgi {i}")
    await _drain_background_tasks()
    assert doc.symptom_done is False  # 11 turns — not yet
    guide.note_farmer_turn("belgi 12")
    await _drain_background_tasks()
    assert doc.symptom_done is True
    assert rec.last("chat.question")["step_id"] == "photo"
    assert rec.spoken[-1].startswith("[TIZIM] Belgilar suhbati yetarli.")


async def test_symptom_backstop_fires_at_most_once(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_symptom(doc)
    guide, rec = _guide(store, doc)
    await guide.start()
    for i in range(20):
        guide.note_farmer_turn(f"belgi {i}")
        await _drain_background_tasks()
    photo_questions = [
        p for p in rec.sent if p["type"] == "chat.question" and p["step_id"] == "photo"
    ]
    assert len(photo_questions) == 1


# ---- BUG 2: advance-chip revealed only after enough turns ------------------


async def test_symptom_chip_revealed_after_7_farmer_turns(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_symptom(doc)
    guide, rec = _guide(store, doc)
    await guide.start()
    assert rec.last("chat.question")["options"] == []  # hidden from turn 1

    for i in range(6):
        guide.note_farmer_turn(f"belgi {i}")
    await _drain_background_tasks()
    symptom_questions = [
        p for p in rec.sent if p["type"] == "chat.question" and p["step_id"] == "symptom"
    ]
    assert len(symptom_questions) == 1  # still under the threshold
    assert symptom_questions[-1]["options"] == []

    guide.note_farmer_turn("belgi 7")
    await _drain_background_tasks()
    symptom_questions = [
        p for p in rec.sent if p["type"] == "chat.question" and p["step_id"] == "symptom"
    ]
    assert len(symptom_questions) == 2  # re-emitted exactly once
    assert symptom_questions[-1]["options"] == [{"id": "to_photo", "label": "Rasmga oʻtish"}]
    assert doc.symptom_done is False  # the phase itself did not advance

    # further turns (short of the 12-turn backstop) must not re-emit again.
    for i in range(3):
        guide.note_farmer_turn(f"belgi {8 + i}")
    await _drain_background_tasks()
    symptom_questions = [
        p for p in rec.sent if p["type"] == "chat.question" and p["step_id"] == "symptom"
    ]
    assert len(symptom_questions) == 2


async def test_symptom_tap_advance_before_chip_threshold_still_works(store):
    # BUG 2 requirement 4: a tap must still be accepted before the threshold.
    doc = new_chat_doc(DEV)
    _disease_pest_at_symptom(doc)
    guide, rec = _guide(store, doc)
    await guide.start()
    guide.note_farmer_turn("belgi 1")  # well under the chip threshold
    await _drain_background_tasks()
    await guide.on_answer("symptom", "to_photo", "")
    assert doc.symptom_done is True
    assert rec.last("chat.question")["step_id"] == "photo"


async def test_select_option_during_symptom_returns_D_SY_note(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_symptom(doc)
    guide, _ = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool(
        "select_option", {"step_id": "plant_part", "option_id": "leaf"}
    )
    assert result["status"] == "invalid"
    assert "belgilar bosqichi" in result["note"]
    assert doc.symptom_done is False


async def test_to_photo_outside_symptom_phase_is_invalid(store):
    doc = new_chat_doc(DEV)  # pending is query_type
    guide, _ = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool("to_photo", {"summary": "x"})
    assert result["status"] == "invalid"
    assert "belgilar bosqichi emas" in result["note"]


async def test_to_photo_after_finished_is_invalid(store):
    doc = new_chat_doc(DEV)
    doc.finished = True
    guide, _ = _guide(store, doc)
    result = await guide.handle_tool("to_photo", {"summary": "x"})
    assert result["status"] == "invalid"


# ---- crop-context phase (§1.3) ----------------------------------------------


def _disease_pest_at_crop_context(doc) -> None:
    doc.query_type = "disease_pest"
    doc.crop_id = "uuid-pomidor"
    doc.crop_name = "Pomidor"
    # crop_context now comes BEFORE plant_part (§1.3) — a genuine new-order
    # doc reaches this step with plant_part still unset.
    doc.crop_in_profile = False


async def test_start_resumes_at_crop_context_step_speaks_CC_A(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_crop_context(doc)
    guide, rec = _guide(store, doc)
    await guide.start()
    # Anketa: chat.state keladi, lekin chat.question YOʻQ — savol ovozda.
    assert rec.sent_types() == ["chat.step"]
    assert rec.sent[0]["phase"] == "crop_context"
    assert len(rec.spoken) == 1
    assert rec.spoken[0].startswith("[TIZIM][SAVOL step=crop_context]")
    assert "Qaysi viloyat va tumandasiz?" in rec.spoken[0]


async def test_to_symptom_tool_advances_to_plant_part_step(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_crop_context(doc)
    guide, rec = _guide(store, doc)
    await guide.start()  # pending is crop_context
    result = await guide.handle_tool("to_symptom", {})
    assert result["status"] == "recorded"
    assert doc.crop_context_done is True
    step_evt = rec.last("chat.step")
    assert step_evt.items() >= {
        "type": "chat.step", "chat_id": doc.id, "step_id": "crop_context",
        "option_id": "to_symptom", "label": "Belgilarga oʻtish",
    }.items()
    # Advances into plant_part (the FIRST §2 question), not straight to symptom.
    assert rec.sent[-2]["type"] == "chat.step" and rec.sent[-2]["phase"] == "guide"
    question = rec.last("chat.question")
    assert question["step_id"] == "plant_part"
    assert "Qabul qilindi" in result["note"]
    assert "[SAVOL step=plant_part]" in result["note"]
    assert "Muammo oʻsimlikning qaysi qismida" in result["note"]


async def test_crop_context_tap_button_advances_to_plant_part(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_crop_context(doc)
    guide, rec = _guide(store, doc)
    await guide.start()
    await guide.on_answer("crop_context", "to_symptom", "")
    assert doc.crop_context_done is True
    question = rec.last("chat.question")
    assert question["step_id"] == "plant_part"
    assert rec.spoken[-1].startswith("[TIZIM] Fermer tugma orqali «Belgilarga oʻtish»")
    assert "[SAVOL step=plant_part]" in rec.spoken[-1]


async def test_crop_context_answer_with_wrong_option_id_is_ignored(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_crop_context(doc)
    guide, rec = _guide(store, doc)
    await guide.start()
    rec.sent.clear()
    rec.spoken.clear()
    await guide.on_answer("crop_context", "something_else", "")
    assert doc.crop_context_done is False
    assert rec.sent == []
    assert rec.spoken == []


async def test_to_symptom_outside_crop_context_phase_is_invalid(store):
    doc = new_chat_doc(DEV)  # pending is query_type
    guide, _ = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool("to_symptom", {})
    assert result["status"] == "invalid"
    assert "ekin konteksti bosqichi emas" in result["note"]


async def test_to_symptom_after_finished_is_invalid(store):
    doc = new_chat_doc(DEV)
    doc.finished = True
    guide, _ = _guide(store, doc)
    result = await guide.handle_tool("to_symptom", {})
    assert result["status"] == "invalid"


async def test_select_option_during_crop_context_returns_D_CC_note(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_crop_context(doc)
    guide, _ = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool(
        "select_option", {"step_id": "plant_part", "option_id": "leaf"}
    )
    assert result["status"] == "invalid"
    assert "ekin konteksti bosqichi" in result["note"]
    assert doc.crop_context_done is False


async def test_crop_context_anketa_completes_after_four_answers(store):
    # Server-driven questionnaire: each farmer turn answers the current fixed
    # question; the fourth answer advances deterministically — no model
    # judgement, no to_symptom needed.
    doc = new_chat_doc(DEV)
    _disease_pest_at_crop_context(doc)
    guide, rec = _guide(store, doc)
    await guide.start()

    guide.note_farmer_turn("Toshkent, Yangiyoʻl")
    await _drain_background_tasks()
    assert doc.crop_context_answers == {"region": "Toshkent, Yangiyoʻl"}
    assert "Ekin qachon ekilgan?" in rec.spoken[-1]
    assert doc.crop_context_done is False

    guide.note_farmer_turn("10 kun oldin")
    await _drain_background_tasks()
    guide.note_farmer_turn("Usmirlik fazasida")
    await _drain_background_tasks()
    assert "Oxirgi agrotexnik ishlar qachon va qanday boʻlgan?" in rec.spoken[-1]
    guide.note_farmer_turn("Kecha sugʻordim")
    await _drain_background_tasks()

    assert doc.crop_context_done is True
    assert doc.crop_context_answers == {
        "region": "Toshkent, Yangiyoʻl",
        "planted_at": "10 kun oldin",
        "growth_phase": "Usmirlik fazasida",
        "last_agro": "Kecha sugʻordim",
    }
    assert rec.last("chat.question")["step_id"] == "plant_part"
    assert rec.spoken[-1].startswith("[TIZIM] Ekin konteksti yetarli.")


async def test_crop_context_backstop_fires_at_most_once(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_crop_context(doc)
    guide, rec = _guide(store, doc)
    await guide.start()
    for i in range(20):
        guide.note_farmer_turn(f"javob {i}")
        await _drain_background_tasks()
    plant_part_questions = [
        p for p in rec.sent if p["type"] == "chat.question" and p["step_id"] == "plant_part"
    ]
    assert len(plant_part_questions) == 1


# ---- BUG 2: advance-chip revealed only after enough turns ------------------


async def test_crop_context_anketa_advances_question_by_question(store):
    # Each answer re-emits chat.question with the NEXT fixed prompt; options
    # stay empty throughout, empty turns don't advance, and the model is told
    # to voice exactly the current question.
    doc = new_chat_doc(DEV)
    _disease_pest_at_crop_context(doc)
    guide, rec = _guide(store, doc)
    await guide.start()
    assert "Qaysi viloyat va tumandasiz?" in rec.spoken[-1]

    guide.note_farmer_turn("   ")  # a blank turn answers nothing
    await _drain_background_tasks()
    assert doc.crop_context_answers == {}
    assert len(rec.spoken) == 1  # keyingi savol aytilmadi

    guide.note_farmer_turn("Yangiyoʻl")
    await _drain_background_tasks()
    # chat.question anketa uchun HECH QACHON chiqmaydi…
    assert not [
        p for p in rec.sent
        if p["type"] == "chat.question" and p["step_id"] == "crop_context"
    ]
    # …keyingi savol faqat ovoz orqali.
    assert "Ekin qachon ekilgan?" in rec.spoken[-1]
    assert rec.spoken[-1].startswith("[TIZIM] Javob qabul qilindi.")
    assert doc.crop_context_done is False  # the phase itself did not advance


async def test_crop_context_tap_advance_before_chip_threshold_still_works(store):
    # BUG 2 requirement 4: a tap must still be accepted before the threshold —
    # no new guard on the tap/tool route.
    doc = new_chat_doc(DEV)
    _disease_pest_at_crop_context(doc)
    guide, rec = _guide(store, doc)
    await guide.start()
    guide.note_farmer_turn("javob 1")  # well under the chip threshold
    await _drain_background_tasks()
    await guide.on_answer("crop_context", "to_symptom", "")
    assert doc.crop_context_done is True
    assert rec.last("chat.question")["step_id"] == "plant_part"


def test_symptom_body_is_clean_of_no_profile_directive(store):
    # §2 checklist: _symptom_body() carries the pure §2 Asosiy savollar and
    # no longer mentions the (now removed) profile-ask directive.
    doc = new_chat_doc(DEV)
    guide, _ = _guide(store, doc)
    body = guide._symptom_body()
    assert "Nima sodir boʻlyapti" in body
    assert "Belgilar qachondan beri bor" in body
    assert "Muammo butun daladami" in body
    assert "Oxirgi ishlov qachon" in body
    assert "Oxirgi sugʻorish qachon" in body
    assert "Oxirgi oʻgʻitlash qachon" in body
    assert "Growz profilida saqlanmagan" not in body


async def test_resume_recomputes_crop_in_profile_for_old_persisted_doc(store):
    # An old doc persisted before crop_in_profile existed loads with the
    # fail-open default True; on resume the guide recomputes it from the
    # (mocked, empty) profile lookup, persists the correction, and resumes
    # at crop_context instead of silently skipping §1.3.
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    doc.crop_id = "uuid-pomidor"
    doc.crop_name = "Pomidor"
    doc.plant_part = "leaf"
    assert doc.crop_in_profile is True  # old-doc default
    assert doc.crop_context_done is False
    guide, rec = _guide(store, doc)  # default settings -> mocked lookup returns ""
    await guide.start()
    assert doc.crop_in_profile is False
    assert guide.pending_step() == "crop_context"
    assert rec.sent[0]["phase"] == "crop_context"
    assert rec.spoken[0].startswith("[TIZIM][SAVOL step=crop_context]")
    # The correction was persisted.
    reloaded = store.read(doc.user_id, doc.id)
    assert reloaded is not None
    assert reloaded.crop_in_profile is False


async def test_resume_stays_at_symptom_when_profile_recompute_still_true(store, monkeypatch):
    # A genuinely profiled crop recomputes to the same True on resume and
    # proceeds straight to symptom (no crop_context detour).
    async def _fake_planting(settings, crop_name):
        return {"cropName": crop_name}

    import app.voice.enrich.tenant_crops as tenant_crops_mod

    monkeypatch.setattr(tenant_crops_mod, "get_tenant_planting", _fake_planting)
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    doc.crop_id = "uuid-pomidor"
    doc.crop_name = "Pomidor"
    doc.plant_part = "leaf"
    guide, rec = _guide(store, doc)
    await guide.start()
    assert doc.crop_in_profile is True
    assert rec.sent[0]["phase"] == "symptom"


# ---- general phase (contract §4.4 c/d/e/f, §4.6 G/TRIG/SW/STAY, §4.10) ------


async def test_query_type_general_accepted_by_tap_enters_general_phase(store):
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    await guide.start()
    await guide.on_answer("query_type", "general", "")
    assert doc.query_type == "general"
    step_evt = rec.last("chat.step")
    assert step_evt.items() >= {
        "type": "chat.step", "chat_id": doc.id, "step_id": "query_type",
        "option_id": "general", "label": "Umumiy savol berish",
    }.items()
    assert rec.sent_types()[-2:] == ["chat.step", "chat.question"]
    assert rec.sent[-2]["phase"] == "general"
    question = rec.last("chat.question")
    assert question["step_id"] == "general"
    assert question["kind"] == "free"
    assert question["prompt"] == "Savolingizni bemalol ayting"
    assert question["options"] == []
    assert rec.spoken[-1].startswith("[TIZIM] Fermer tugma orqali «Umumiy savol berish»")
    assert "[UMUMIY SAVOL]" in rec.spoken[-1]


async def test_query_type_general_accepted_by_voice_returns_ack_note(store):
    doc = new_chat_doc(DEV)
    guide, rec = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool(
        "select_option", {"step_id": "query_type", "option_id": "general"}
    )
    assert result["status"] == "recorded"
    assert result["note"].startswith("Qabul qilindi: «Umumiy savol berish».")
    assert "[UMUMIY SAVOL]" in result["note"]
    assert doc.query_type == "general"
    # Voice-advance transition goes into the ack note, never spoken directly.
    assert len(rec.spoken) == 1  # only script A from start()


async def test_general_step_chat_answer_is_always_ignored(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "general"
    guide, rec = _guide(store, doc)
    await guide.start()
    rec.sent.clear()
    await guide.on_answer("general", "whatever", "some text")
    assert rec.sent == []


async def test_note_farmer_turn_captures_first_general_question_and_title(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "general"
    guide, _ = _guide(store, doc)
    await guide.start()
    guide.note_farmer_turn("Pomidorimni qachon sug'orishim kerak?")
    await _drain_background_tasks()
    assert doc.general_question == "Pomidorimni qachon sug'orishim kerak?"
    assert doc.title == "Pomidorimni qachon sug'orishim kerak?"
    # a later farmer turn does not overwrite the captured question.
    guide.note_farmer_turn("Boshqa savol ham bor")
    await _drain_background_tasks()
    assert doc.general_question == "Pomidorimni qachon sug'orishim kerak?"


async def test_note_farmer_turn_offers_diagnostic_once_on_trigger_word(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "general"
    guide, rec = _guide(store, doc)
    await guide.start()
    guide.note_farmer_turn("Barglarida kasallik bor")
    await _drain_background_tasks()
    question = rec.last("chat.question")
    assert question["step_id"] == "diag_offer"
    assert question["kind"] == "buttons"
    assert question["options"] == [
        {"id": "switch_diag", "label": "Ha, aniqlaymiz"},
        {"id": "stay_general", "label": "Yoʻq, davom etamiz"},
    ]
    assert rec.spoken[-1].startswith("[TIZIM] Fermer soʻzlarida kasallik")

    # a second trigger word later in the same session must not offer again.
    rec.sent.clear()
    rec.spoken.clear()
    guide.note_farmer_turn("Yana hasharot koʻryapman")
    await _drain_background_tasks()
    assert rec.sent == []
    assert rec.spoken == []


async def test_note_farmer_turn_no_offer_without_a_trigger_word(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "general"
    guide, rec = _guide(store, doc)
    await guide.start()
    guide.note_farmer_turn("Bugun ob-havo qanday boʻladi?")
    await _drain_background_tasks()
    assert all(
        p.get("step_id") != "diag_offer" for p in rec.sent if p["type"] == "chat.question"
    )


async def test_diag_offer_switch_diag_tap_reenters_diagnostic_at_crop(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "general"
    doc.general_question = "Barglarida kasallik bor"
    guide, rec = _guide(store, doc)
    await guide.start()
    await guide.on_answer("diag_offer", "switch_diag", "")
    assert doc.query_type == "disease_pest"
    step_evt = rec.last("chat.step")
    assert step_evt.items() >= {
        "type": "chat.step", "chat_id": doc.id, "step_id": "diag_offer",
        "option_id": "switch_diag", "label": "Ha, aniqlaymiz",
    }.items()
    assert rec.sent_types()[-2:] == ["chat.step", "chat.question"]
    assert rec.sent[-2]["phase"] == "guide"
    assert rec.last("chat.question")["step_id"] == "crop"
    assert rec.spoken[-1].startswith("[TIZIM] Fermer aniqlash jarayoniga oʻtishni tanladi.")
    assert "[SAVOL step=crop]" in rec.spoken[-1]
    # general_question survives the switch for context (contract §4.4 e).
    assert doc.general_question == "Barglarida kasallik bor"


async def test_switch_to_diagnostic_tool_reenters_diagnostic_at_crop(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "general"
    guide, rec = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool("switch_to_diagnostic", {"reason": "ayting"})
    assert result["status"] == "recorded"
    assert doc.query_type == "disease_pest"
    assert "Aniqlash jarayoni boshlandi." in result["note"]
    assert "[SAVOL step=crop]" in result["note"]
    assert rec.last("chat.question")["step_id"] == "crop"


async def test_switch_to_diagnostic_outside_general_phase_is_invalid(store):
    doc = new_chat_doc(DEV)  # pending is query_type, not general
    guide, _ = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool("switch_to_diagnostic", {})
    assert result["status"] == "invalid"
    assert "umumiy savol rejimi emas" in result["note"]
    assert doc.query_type == ""


async def test_switch_to_diagnostic_after_finished_is_invalid(store):
    doc = new_chat_doc(DEV)
    doc.finished = True
    guide, _ = _guide(store, doc)
    result = await guide.handle_tool("switch_to_diagnostic", {})
    assert result["status"] == "invalid"


async def test_select_option_during_general_returns_D_G_note(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "general"
    guide, _ = _guide(store, doc)
    await guide.start()
    result = await guide.handle_tool(
        "select_option", {"step_id": "crop", "option_id": "uuid-pomidor"}
    )
    assert result["status"] == "invalid"
    assert "umumiy savol rejimi" in result["note"]
    assert doc.crop_id == ""


async def test_diag_offer_stay_general_re_sends_without_duplicate_message(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "general"
    guide, rec = _guide(store, doc)
    await guide.start()  # records the "general" question once
    question_messages_before = [m for m in doc.messages if m.kind == "question"]
    await guide.on_answer("diag_offer", "stay_general", "")
    step_evt = rec.last("chat.step")
    assert step_evt.items() >= {
        "type": "chat.step", "chat_id": doc.id, "step_id": "diag_offer",
        "option_id": "stay_general", "label": "Yoʻq, davom etamiz",
    }.items()
    assert rec.last("chat.question")["step_id"] == "general"
    assert rec.spoken[-1] == (
        "[TIZIM] Fermer umumiy savolda davom etishni tanladi. Savoliga qaytib "
        "javob berishni davom ettir."
    )
    question_messages_after = [m for m in doc.messages if m.kind == "question"]
    assert len(question_messages_after) == len(question_messages_before)
    assert doc.query_type == "general"  # no switch happened


async def test_diag_offer_answer_ignored_when_pending_is_not_general(store):
    doc = new_chat_doc(DEV)  # pending is query_type
    guide, rec = _guide(store, doc)
    await guide.start()
    rec.sent.clear()
    await guide.on_answer("diag_offer", "switch_diag", "")
    assert rec.sent == []
    assert doc.query_type == ""


# ---- memory-crop quick-pick chips (contract §4.9) ---------------------------


async def test_memory_crop_chips_resolved_and_prepended_to_crop_options(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    guide, rec = _guide(store, doc)
    guide.set_memory_crops(["pomidor", "bodring"])
    await guide.start()
    # Server-driven «Ha»/«Yoʻq»: chips exist -> the pair comes first…
    question = rec.last("chat.question")
    assert question["step_id"] == "crop"
    assert [o["id"] for o in question["options"]] == [
        "saved_crops", "open_crop_picker",
    ]
    # …and answering «Ha» reveals the resolved chips.
    await guide.on_answer("crop", "saved_crops", "")
    question = rec.last("chat.question")
    assert question["options"] == [
        {"id": "uuid-pomidor", "label": "Pomidor"},
        {"id": "uuid-bodring", "label": "Bodring"},
    ]


async def test_memory_crop_chips_capped_at_4(store, monkeypatch):
    extra_crops = _CROPS + [
        {"id": "uuid-kartoshka", "name": "Kartoshka"},
        {"id": "uuid-piyoz", "name": "Piyoz"},
    ]

    async def _fake(settings):
        return extra_crops

    monkeypatch.setattr(enrich_pkg, "get_crops", _fake)
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    guide, rec = _guide(store, doc)
    guide.set_memory_crops(
        ["pomidor", "bodring", "behi", "g'o'za", "kartoshka", "piyoz"]
    )
    await guide.start()
    # Server-driven «Ha»/«Yoʻq»: chips are revealed by answering saved_crops.
    await guide.on_answer("crop", "saved_crops", "")
    question = rec.last("chat.question")
    chip_ids = [o["id"] for o in question["options"] if o["id"] != "open_crop_picker"]
    assert chip_ids == ["uuid-pomidor", "uuid-bodring", "uuid-behi", "uuid-goza"]


async def test_memory_crop_chips_deduped_by_growz_id(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    guide, rec = _guide(store, doc)
    guide.set_memory_crops(["pomidor", "pomidor"])
    await guide.start()
    await guide.on_answer("crop", "saved_crops", "")
    question = rec.last("chat.question")
    chip_ids = [o["id"] for o in question["options"] if o["id"] != "open_crop_picker"]
    assert chip_ids == ["uuid-pomidor"]


async def test_memory_crop_chips_unresolved_names_are_dropped(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    guide, rec = _guide(store, doc)
    guide.set_memory_crops(["traktor", "pomidor"])  # "traktor" matches nothing
    await guide.start()
    await guide.on_answer("crop", "saved_crops", "")
    question = rec.last("chat.question")
    assert question["options"] == [{"id": "uuid-pomidor", "label": "Pomidor"}]


async def test_memory_crop_chips_absent_still_offers_ha_yoq(store):
    # The pair is unconditional now; an empty profile only changes what «Ha»
    # expands into (see the dedicated test above).
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    guide, rec = _guide(store, doc)
    # set_memory_crops never called
    await guide.start()
    question = rec.last("chat.question")
    assert [o["id"] for o in question["options"]] == [
        "saved_crops", "open_crop_picker",
    ]


async def test_memory_crop_chips_fail_open_on_catalogue_error(store, monkeypatch):
    async def _boom(settings):
        raise RuntimeError("growz down")

    monkeypatch.setattr(enrich_pkg, "get_crops", _boom)
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    guide, rec = _guide(store, doc)
    guide.set_memory_crops(["pomidor"])
    await guide.start()
    # The pair still shows (it is unconditional); «Ha» then fails open to the
    # catalogue-only view instead of degrading the whole guide.
    question = rec.last("chat.question")
    assert [o["id"] for o in question["options"]] == [
        "saved_crops", "open_crop_picker",
    ]
    await guide.on_answer("crop", "saved_crops", "")
    question = rec.last("chat.question")
    assert question["options"] == [{"id": "open_crop_picker", "label": "Ekinlar"}]
    assert guide.degraded is False


async def test_memory_crop_chips_included_in_the_spoken_opts_string(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "disease_pest"
    guide, rec = _guide(store, doc)
    guide.set_memory_crops(["pomidor"])
    await guide.start()
    # §1.1 «profilda bormi?» — the model is told the profile crops behind «Ha»
    # and «Yoʻq» for the catalogue.
    assert "«Pomidor»" in rec.spoken[-1]
    assert "profildagi ekinlar" in rec.spoken[-1]
    assert "Ha=" in rec.spoken[-1] and "Yoʻq=" in rec.spoken[-1]


# ---- records_transcript gating (contract §4.7) ------------------------------


def test_records_transcript_false_during_plain_guide_steps(store):
    doc = new_chat_doc(DEV)
    guide, _ = _guide(store, doc)
    assert guide.records_transcript() is False  # pending query_type
    doc.query_type = "disease_pest"
    assert guide.records_transcript() is False  # pending crop
    doc.crop_id = "u1"
    assert guide.records_transcript() is False  # pending plant_part


def test_records_transcript_true_once_symptom_phase_begins(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_symptom(doc)  # pending is now "symptom"
    guide, _ = _guide(store, doc)
    assert guide.records_transcript() is True


def test_records_transcript_false_again_once_photo_step_reached(store):
    doc = new_chat_doc(DEV)
    _disease_pest_at_symptom(doc)
    doc.symptom_done = True  # pending is now "photo" — a plain guide step again
    guide, _ = _guide(store, doc)
    assert guide.records_transcript() is False


def test_records_transcript_true_during_general_phase(store):
    doc = new_chat_doc(DEV)
    doc.query_type = "general"
    guide, _ = _guide(store, doc)
    assert guide.records_transcript() is True


def test_records_transcript_true_when_finished(store):
    doc = new_chat_doc(DEV)
    doc.finished = True
    guide, _ = _guide(store, doc)
    assert guide.records_transcript() is True


# ---- trigger-word detection (contract §4.10) --------------------------------


def test_has_trigger_short_word_matches_only_whole_word():
    assert has_trigger("bargida bit bor") is True
    assert has_trigger("bittasi sarg'aymadi") is False  # "bitta" must not fire "bit"


def test_has_trigger_long_word_matches_as_a_prefix():
    assert has_trigger("bu kasalligi nima ekan") is True  # "kasalligi" fires "kasal"


def test_has_trigger_multiword_phrase_matches_as_substring():
    assert has_trigger("bargida qo'ng'ir dog' paydo bo'ldi") is True


def test_has_trigger_cyrillic_disease_words():
    # Uzbek Cyrillic input must fire the same as Latin (spec §0.3 lists both).
    assert has_trigger("касаллик бор") is True
    assert has_trigger("доғ пайдо бўлди") is True
    assert has_trigger("замбуруғ туширди") is True
    assert has_trigger("касаллиги нима экан") is True  # prefix: "касал"


def test_has_trigger_cyrillic_pest_words():
    assert has_trigger("зараркунанда кўриняпти") is True
    assert has_trigger("шира тушибди") is True
    assert has_trigger("нима билан ишлов бераман") is True  # multi-word


def test_has_trigger_cyrillic_short_word_whole_word_only():
    assert has_trigger("баргида бит бор") is True
    assert has_trigger("битта олма") is False  # "битта" must not fire "бит"


def test_has_trigger_cyrillic_no_match_on_unrelated_text():
    assert has_trigger("бугун об-ҳаво яхши") is False


def test_has_trigger_no_match_on_unrelated_text():
    assert has_trigger("bugun ob-havo qanday bo'ladi?") is False


def test_has_trigger_okina_normalisation():
    assert has_trigger("bargda zamburug' bor-ov") is True


def test_has_trigger_pest_group_also_maps_via_same_function():
    assert has_trigger("barglar teshiklar bor ekan") is True
