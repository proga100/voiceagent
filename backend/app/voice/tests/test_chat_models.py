"""Chat data model helpers: title derivation, summary/detail shape, message caps."""
from app.voice.chat.models import (
    STEP_ORDER,
    STEPS,
    TRIGGER_WORDS,
    AgronomReview,
    ChatDoc,
    ChatMessage,
    build_detail,
    build_summary,
    derive_title,
    new_chat_doc,
    sanitize_expert_payload,
)

DEV = "11111111-2222-3333-4444-555555555555"


def test_new_chat_doc_defaults():
    doc = new_chat_doc(DEV)
    assert len(doc.id) == 32
    assert doc.user_id == DEV
    assert doc.title == "Yangi suhbat"
    assert doc.finished is False
    assert doc.messages == []
    assert doc.created_at == doc.updated_at


def test_derive_title_crop_and_disease():
    doc = ChatDoc(id="x", user_id=DEV, crop_name="Pomidor", query_type="disease_pest")
    assert derive_title(doc) == "Pomidor — kasallik"


def test_derive_title_crop_and_weed():
    doc = ChatDoc(id="x", user_id=DEV, crop_name="Bugʻdoy", query_type="weed")
    assert derive_title(doc) == "Bugʻdoy — begona oʻt"


def test_derive_title_crop_only_no_query_type():
    doc = ChatDoc(id="x", user_id=DEV, crop_name="Bodring")
    assert derive_title(doc) == "Bodring"


def test_derive_title_query_type_only():
    doc = ChatDoc(id="x", user_id=DEV, query_type="disease_pest")
    assert derive_title(doc) == "Kasalliklar va zararkunandalar"
    doc2 = ChatDoc(id="x", user_id=DEV, query_type="weed")
    assert derive_title(doc2) == "Begona oʻt"


def test_derive_title_falls_back_to_first_farmer_text_message():
    doc = ChatDoc(
        id="x", user_id=DEV,
        messages=[
            ChatMessage(role="rais", kind="question", text="ignored"),
            ChatMessage(role="farmer", kind="answer", text="also ignored (not text kind)"),
            ChatMessage(role="farmer", kind="text", text="Barglari sarg'ayib qoldi"),
        ],
    )
    assert derive_title(doc) == "Barglari sarg'ayib qoldi"


def test_derive_title_truncates_farmer_text_to_60_chars():
    long_text = "x" * 100
    doc = ChatDoc(
        id="x", user_id=DEV,
        messages=[ChatMessage(role="farmer", kind="text", text=long_text)],
    )
    assert derive_title(doc) == "x" * 60


def test_derive_title_default_new_chat():
    doc = ChatDoc(id="x", user_id=DEV)
    assert derive_title(doc) == "Yangi suhbat"


# ---- general title rule (contract §3.6 rule 1, v2) -------------------------


def test_derive_title_general_uses_general_question_first():
    doc = ChatDoc(
        id="x", user_id=DEV, query_type="general",
        general_question="Pomidorimni qachon sug'orishim kerak?",
    )
    assert derive_title(doc) == "Pomidorimni qachon sug'orishim kerak?"


def test_derive_title_general_truncates_general_question_to_60_chars():
    doc = ChatDoc(
        id="x", user_id=DEV, query_type="general",
        general_question="x" * 100,
    )
    assert derive_title(doc) == "x" * 60


def test_derive_title_general_falls_back_to_first_farmer_text():
    doc = ChatDoc(
        id="x", user_id=DEV, query_type="general",
        messages=[ChatMessage(role="farmer", kind="text", text="Savolim bor")],
    )
    assert derive_title(doc) == "Savolim bor"


def test_derive_title_general_default_when_nothing_captured_yet():
    doc = ChatDoc(id="x", user_id=DEV, query_type="general")
    assert derive_title(doc) == "Umumiy savol"


def test_derive_title_general_rule_fires_before_crop_rule():
    # A general chat that later switched to diagnostic keeps its
    # general_question, but query_type is no longer "general" so rule 1 no
    # longer fires — rules 2-5 (crop/query_type) take over instead.
    doc = ChatDoc(
        id="x", user_id=DEV, query_type="disease_pest", crop_name="Pomidor",
        general_question="Eski savol",
    )
    assert derive_title(doc) == "Pomidor — kasallik"


def test_build_summary_shape_empty_chat():
    doc = new_chat_doc(DEV)
    summary = build_summary(doc)
    assert summary == {
        "id": doc.id,
        "user_id": DEV,
        "title": "Yangi suhbat",
        "query_type": "",
        "crop_id": "",
        "crop_name": "",
        "plant_part": "",
        "symptom_done": False,
        "symptom_summary": "",
        "crop_context_answers": {},
        "general_question": "",
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        "finished": False,
        "message_count": 0,
        "last_message": None,
        "agronom_review": None,
    }


def test_build_summary_last_message():
    doc = new_chat_doc(DEV)
    doc.messages.append(ChatMessage(role="farmer", kind="text", text="Salom", ts="t1"))
    doc.messages.append(ChatMessage(role="rais", kind="text", text="Assalomu alaykum", ts="t2"))
    summary = build_summary(doc)
    assert summary["message_count"] == 2
    assert summary["last_message"] == {
        "role": "rais", "text": "Assalomu alaykum", "ts": "t2", "kind": "text",
    }


def test_build_detail_includes_messages_and_diagnosis_no_summary_fields():
    doc = new_chat_doc(DEV)
    doc.last_diagnosis = {"disease": "Fitoftoroz", "confidence": "high", "date": "2026-07-11"}
    doc.messages.append(ChatMessage(role="farmer", kind="text", text="Salom", ts="t1"))
    detail = build_detail(doc)
    assert "message_count" not in detail
    assert "last_message" not in detail
    assert detail["last_diagnosis"] == doc.last_diagnosis
    assert detail["messages"] == [{"role": "farmer", "kind": "text", "text": "Salom", "ts": "t1"}]


def test_plant_part_step_options_match_contract_order_and_labels():
    ids = [oid for oid, _ in STEPS["plant_part"]["options"]]
    assert ids == [
        "leaf", "stem", "fruit", "flower", "root", "branch", "bark", "whole_plant", "soil",
    ]
    labels = dict(STEPS["plant_part"]["options"])
    assert labels["leaf"] == "Bargida"
    assert labels["whole_plant"] == "Butun oʻsimlik"
    assert labels["soil"] == "Tuproqda"


def test_query_type_and_crop_and_photo_step_shapes():
    assert STEPS["query_type"]["kind"] == "buttons"
    assert dict(STEPS["query_type"]["options"]) == {
        "disease_pest": "Kasalliklar va zararkunandalar",
        "weed": "Begona oʻt",
        "general": "Umumiy savol berish",
    }
    assert STEPS["crop"]["kind"] == "crop_picker"
    assert dict(STEPS["crop"]["options"]) == {"open_crop_picker": "Ekinlar"}
    assert STEPS["photo"]["kind"] == "photo"
    # A photo is mandatory (2026-08-05): no skip option exists at all.
    assert STEPS["photo"]["options"] == [
        ("take_photo", "Rasm tanlash"),
        ("done_photos", "Tayyor"),
    ]
    assert not [o for o in STEPS["photo"]["options"] if o[0] == "skip"]


def test_symptom_general_diag_offer_step_shapes():
    assert STEPS["symptom"]["kind"] == "symptom"
    assert STEPS["symptom"]["prompt"] == "Belgilar haqida gapirib bering"
    assert dict(STEPS["symptom"]["options"]) == {"to_photo": "Rasmga oʻtish"}

    assert STEPS["general"]["kind"] == "free"
    assert STEPS["general"]["prompt"] == "Savolingizni bemalol ayting"
    assert STEPS["general"]["options"] == []

    assert STEPS["diag_offer"]["kind"] == "buttons"
    assert STEPS["diag_offer"]["prompt"] == "Aniqlash jarayonini boshlaymizmi?"
    assert dict(STEPS["diag_offer"]["options"]) == {
        "switch_diag": "Ha, aniqlaymiz",
        "stay_general": "Yoʻq, davom etamiz",
    }


def test_step_order_reflects_crop_context_before_plant_part():
    assert STEP_ORDER == [
        "query_type", "crop", "crop_context", "plant_part", "symptom", "photo",
    ]


def test_query_type_prompt_revised_in_v2():
    assert STEPS["query_type"]["prompt"] == "Nima boʻyicha maslahat kerak?"


# ---- §1.3 crop-context step -------------------------------------------------


def test_crop_context_step_shape():
    assert STEPS["crop_context"]["kind"] == "symptom"
    assert STEPS["crop_context"]["prompt"] == "Ekin haqida bir-ikki savol"
    assert dict(STEPS["crop_context"]["options"]) == {
        "to_symptom": "Belgilarga oʻtish",
    }


def test_chat_doc_crop_context_defaults():
    doc = new_chat_doc(DEV)
    assert doc.crop_in_profile is True
    assert doc.crop_context_done is False


def test_chat_doc_old_persisted_doc_without_new_fields_parses_with_defaults():
    # A doc saved before crop_in_profile/crop_context_done existed must still
    # load — old chats fail-open to "skip crop_context" (contract A4).
    raw = {"id": "x", "user_id": DEV}
    doc = ChatDoc.model_validate(raw)
    assert doc.crop_in_profile is True
    assert doc.crop_context_done is False


def test_trigger_words_frozen_list():
    assert TRIGGER_WORDS[0] == "kasallik"
    assert "zararkunanda" in TRIGGER_WORDS
    assert "bit" in TRIGGER_WORDS
    # 32 Latin + 32 Cyrillic (Uzbek Cyrillic mirror of the same words).
    assert "касаллик" in TRIGGER_WORDS
    assert "зараркунанда" in TRIGGER_WORDS
    assert len(TRIGGER_WORDS) == 64


# ---- Phase 3 (contract addendum P3.1): AgronomReview -----------------------


def test_agronom_review_defaults():
    r = AgronomReview()
    assert r.status == "none"
    assert r.requested_at == ""
    assert r.reviewed_at == ""
    assert r.is_mock is False
    assert r.verdict == ""
    assert r.expert_summary == ""
    assert r.expert_notes == []
    assert r.adjusted_preparations == []


def test_chat_doc_agronom_review_defaults_to_none():
    doc = new_chat_doc(DEV)
    assert doc.agronom_review is None


def test_build_summary_agronom_review_null_when_none():
    doc = new_chat_doc(DEV)
    summary = build_summary(doc)
    assert summary["agronom_review"] is None


def test_build_summary_agronom_review_carries_the_full_shape():
    doc = new_chat_doc(DEV)
    doc.agronom_review = AgronomReview(
        status="done", requested_at="t1", reviewed_at="t2", is_mock=True,
        verdict="adjusted", expert_summary="Tashxis toʻgʻri.",
        expert_notes=["Ertalab bering."],
        adjusted_preparations=[{"name": "TOPAZ", "dose_min": 0.3, "dose_max": 0.5,
                                 "unit": "l/ga", "type": "disease", "description": ""}],
    )
    summary = build_summary(doc)
    assert summary["agronom_review"] == {
        "status": "done", "requested_at": "t1", "reviewed_at": "t2",
        "is_mock": True, "verdict": "adjusted",
        "expert_summary": "Tashxis toʻgʻri.",
        "expert_notes": ["Ertalab bering."],
        "adjusted_preparations": [
            {"name": "TOPAZ", "dose_min": 0.3, "dose_max": 0.5,
             "unit": "l/ga", "type": "disease", "description": ""}
        ],
    }


def test_build_detail_agronom_review_null_when_none():
    doc = new_chat_doc(DEV)
    detail = build_detail(doc)
    assert detail["agronom_review"] is None


def test_build_detail_agronom_review_carries_the_full_shape():
    doc = new_chat_doc(DEV)
    doc.agronom_review = AgronomReview(status="pending", requested_at="t1")
    detail = build_detail(doc)
    assert detail["agronom_review"]["status"] == "pending"
    assert detail["agronom_review"]["requested_at"] == "t1"


# ---- Phase 3 (P3.1): sanitize_expert_payload caps/coercion/drop rules ------


def test_sanitize_expert_payload_clamps_summary_length():
    summary, notes, preps = sanitize_expert_payload("x" * 700, [], [])
    assert len(summary) == 600
    assert summary == "x" * 600


def test_sanitize_expert_payload_strips_summary():
    summary, _, _ = sanitize_expert_payload("  hello  ", [], [])
    assert summary == "hello"


def test_sanitize_expert_payload_notes_drop_empty_truncate_and_cap_at_six():
    notes_in = ["  ", "note1", "y" * 400, "", "n2", "n3", "n4", "n5", "n6", "n7"]
    summary, notes, _ = sanitize_expert_payload("s", notes_in, [])
    assert len(notes) == 6
    assert notes[0] == "note1"
    assert len(notes[1]) == 300
    # The overflow items (n6, n7 and beyond) are dropped — first 6 kept.
    assert notes == ["note1", "y" * 300, "n2", "n3", "n4", "n5"]


def test_sanitize_expert_payload_notes_non_iterable_fails_open_to_empty():
    # A non-iterable expert_notes (e.g. a bare int) must never raise —
    # fail-open to [] rather than crash the caller.
    _, notes, _ = sanitize_expert_payload("s", 42, [])
    assert notes == []


def test_sanitize_expert_payload_preparations_coerces_frozen_shape():
    raw = [{"name": "TOPAZ", "dose_min": "0.3", "dose_max": "0.5"}]
    _, _, preps = sanitize_expert_payload("s", [], raw)
    assert preps == [{
        "name": "TOPAZ", "dose_min": 0.3, "dose_max": 0.5,
        "unit": "", "type": "", "description": "",
    }]


def test_sanitize_expert_payload_preparations_non_numeric_dose_is_null():
    raw = [{"name": "TOPAZ", "dose_min": "not-a-number", "dose_max": None}]
    _, _, preps = sanitize_expert_payload("s", [], raw)
    assert preps[0]["dose_min"] is None
    assert preps[0]["dose_max"] is None


def test_sanitize_expert_payload_preparations_drops_nameless_items():
    raw = [{"name": ""}, {"name": "  "}, {"name": "Real"}]
    _, _, preps = sanitize_expert_payload("s", [], raw)
    assert [p["name"] for p in preps] == ["Real"]


def test_sanitize_expert_payload_preparations_caps_at_four():
    raw = [{"name": f"P{i}"} for i in range(10)]
    _, _, preps = sanitize_expert_payload("s", [], raw)
    assert len(preps) == 4
    assert [p["name"] for p in preps] == ["P0", "P1", "P2", "P3"]


def test_sanitize_expert_payload_preparations_type_lowercased():
    raw = [{"name": "X", "type": "DISEASE"}]
    _, _, preps = sanitize_expert_payload("s", [], raw)
    assert preps[0]["type"] == "disease"


def test_sanitize_expert_payload_preparations_non_list_returns_empty():
    _, _, preps = sanitize_expert_payload("s", [], "not-a-list")
    assert preps == []
    _, _, preps2 = sanitize_expert_payload("s", [], {"name": "not-a-list-of-dicts"})
    assert preps2 == []


def test_sanitize_expert_payload_never_raises_on_garbage_input():
    summary, notes, preps = sanitize_expert_payload(None, None, None)
    assert summary == ""
    assert notes == []
    assert preps == []
