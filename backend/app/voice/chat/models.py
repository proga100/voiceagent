"""Chat document model + pure helpers (title derivation, summary shape).

Mirrors the split in ``pipeline/memory.py`` (``FarmerProfile`` = the stored
shape, ``build_memory_context`` = pure functions over it): ``ChatDoc`` is the
persisted per-chat document (see ``docs/multichat_contract.md`` §3.2),
``ChatStore`` (``store.py``) owns the file I/O, and this module holds the pure
functions plus the ONE shared Uzbek string table + guided-flow step/option
tables that ``guide.py`` builds on.

The ``UZ`` table, step ids, option ids and Uzbek labels below are FROZEN by
the contract — the mobile client (``lib/features/chat/strings.dart``) mirrors
them exactly. Do not edit without updating the contract.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.voice.pipeline.tools import TARGET_PARTS

# ---------------------------------------------------------------------------
# Uzbek strings (contract §6) — shared with mobile's ``S`` string table.
# ---------------------------------------------------------------------------

UZ: dict[str, str] = {
    "homeTitle": "Suhbatlar",
    "newChat": "Yangi chat",
    "emptyList": "Hali suhbatlar yoʻq. «Yangi chat» tugmasini bosing.",
    "listLoadFailed": "Suhbatlar roʻyxati yuklanmadi",
    "chatLoadFailed": "Suhbat ochilmadi. Qayta urinib koʻring.",
    "retry": "Qayta urinish",
    "offlineChat": "Suhbat saqlanmaydi — server bilan aloqa yoʻq.",
    "today": "Bugun",
    "yesterday": "Kecha",
    "newChatTitle": "Yangi suhbat",
    "qQueryType": "Nima boʻyicha maslahat kerak?",
    "optDiseasePest": "Kasalliklar va zararkunandalar",
    "optWeed": "Begona oʻt",
    "optGeneral": "Umumiy savol berish",
    "qCrop": "Bu ekin profilingizda bormi?",
    "optCrops": "Ekinlar",
    # Server-driven «Ha»/«Yoʻq» for the crop step (backend-only labels: they
    # ride the chat.question payload; the app renders whatever arrives).
    "optCropYes": "Ha",
    "optCropNo": "Yoʻq",
    "qPlantPart": "Muammo oʻsimlikning qaysi qismida?",
    "partLeaf": "Bargida",
    "partStem": "Poyasida",
    "partFruit": "Mevasida",
    "partFlower": "Gulida",
    "partRoot": "Ildizida",
    "partBranch": "Shoxida",
    "partBark": "Poʻstlogʻida",
    "partWhole": "Butun oʻsimlik",
    "partSoil": "Tuproqda",
    "qSymptom": "Belgilar haqida gapirib bering",
    "optToPhoto": "Rasmga oʻtish",
    # §1.3 crop-context step — backend-only, NOT mirrored in the mobile
    # strings.dart (same rule as prepPrefix): prompt + option label ride the
    # chat.question payload, mobile renders them generically (kind "symptom").
    "qCropContext": "Ekin haqida bir-ikki savol",
    "optToSymptom": "Belgilarga oʻtish",
    "qPhoto": "Kasallangan qismning rasmini yuboring",
    "optTakePhoto": "Rasm tanlash",
    # Multi-photo loop terminal option — backend-only, NOT mirrored in the
    # mobile strings.dart. Same rule as qCropContext above: this label rides
    # the chat.question payload and the mobile client renders it generically
    # from question.options, so the frozen/mirrored contract in the module
    # docstring explicitly does NOT cover this option.
    "optDonePhotos": "Tayyor",
    "optSkipPhoto": "Rasmsiz davom etish",
    "photoMarker": "[rasm]",
    "photoBubble": "📷 Rasm",
    "qGeneral": "Savolingizni bemalol ayting",
    "qDiagOffer": "Aniqlash jarayonini boshlaymizmi?",
    "optSwitchDiag": "Ha, aniqlaymiz",
    "optStayGeneral": "Yoʻq, davom etamiz",
    "generalTitle": "Umumiy savol",
    "qtDisease": "kasallik",
    "qtWeed": "begona oʻt",
    "diagPrefix": "Tashxis:",
    # Phase 2 (contract addendum P2.5): backend-only, NOT mirrored in the
    # mobile strings.dart — the stored diagnosis message text is composed
    # server-side only.
    "prepPrefix": "Preparatlar:",
    # Phase 3 (contract addendum P3.8): mirrored keys — backend `UZ` <->
    # mobile `lib/features/chat/strings.dart` `S`, byte-exact.
    "agronomSend": "Agronomga yuborish",
    "agronomPending": "Agronom tekshirmoqda…",
    "agronomCardTitle": "Agronom (ekspert) javobi",
    "agronomBadge": "Agronom tasdiqlagan javob",
    "agronomMockLabel": "AI yordamchi (sinov)",
    "agronomConfirmed": "Tashxis tasdiqlandi",
    "agronomAdjusted": "Tavsiya aniqlashtirildi",
    "agronomKeepPreps": "AI tavsiya etgan preparatlar roʻyxati oʻz kuchida qoladi.",
    "agronomAdjustedPreps": "Yangilangan preparatlar roʻyxati",
    "agronomRequestFailed": "Yuborib boʻlmadi. Qayta urinib koʻring.",
    # Phase 3 backend-ONLY (P3.8): NOT mirrored in strings.dart, same rule as
    # prepPrefix — the stored agronom message text is composed server-side.
    "agronomPrefix": "Agronom javobi:",
}

# ---------------------------------------------------------------------------
# Guided-flow step/option tables (contract §4.2, §4.3) — single source of
# truth for the WS chat.question payloads AND the select_option tool built
# in guide.py.
# ---------------------------------------------------------------------------

# TARGET_PARTS order/ids mirror pipeline/tools.py exactly (the photo-request
# tool already speaks this vocabulary).
_PART_LABEL_KEYS = {
    "leaf": "partLeaf", "stem": "partStem", "fruit": "partFruit",
    "flower": "partFlower", "root": "partRoot", "branch": "partBranch",
    "bark": "partBark", "whole_plant": "partWhole", "soil": "partSoil",
}

PLANT_PART_OPTIONS: list[tuple[str, str]] = [
    (part, UZ[_PART_LABEL_KEYS[part]]) for part in TARGET_PARTS
]

STEP_ORDER = ["query_type", "crop", "crop_context", "plant_part", "symptom", "photo"]

# §1.3 crop-context anketa — SERVER-driven, fixed order (team decision:
# question logic lives in the backend, the model only voices each question).
# field-key -> the exact question sent as chat.question.prompt AND stored
# under ChatDoc.crop_context_answers[field].
CROP_CONTEXT_QUESTIONS: list[tuple[str, str]] = [
    ("region", "Qaysi viloyat va tumandasiz?"),
    ("planted_at", "Ekin qachon ekilgan?"),
    ("growth_phase", "Hozir taxminan qaysi rivojlanish fazasida?"),
    ("last_agro", "Oxirgi agrotexnik ishlar qachon va qanday boʻlgan?"),
]

STEPS: dict[str, dict] = {
    "query_type": {
        "kind": "buttons",
        "prompt": UZ["qQueryType"],
        "options": [
            ("disease_pest", UZ["optDiseasePest"]),
            ("weed", UZ["optWeed"]),
            ("general", UZ["optGeneral"]),
        ],
    },
    "crop": {
        "kind": "crop_picker",
        "prompt": UZ["qCrop"],
        "options": [("open_crop_picker", UZ["optCrops"])],
    },
    "plant_part": {
        "kind": "buttons",
        "prompt": UZ["qPlantPart"],
        "options": PLANT_PART_OPTIONS,
    },
    "crop_context": {
        "kind": "symptom",
        "prompt": UZ["qCropContext"],
        "options": [("to_symptom", UZ["optToSymptom"])],
    },
    "symptom": {
        "kind": "symptom",
        "prompt": UZ["qSymptom"],
        "options": [("to_photo", UZ["optToPhoto"])],
    },
    "photo": {
        "kind": "photo",
        "prompt": UZ["qPhoto"],
        "options": [
            ("take_photo", UZ["optTakePhoto"]),
            ("done_photos", UZ["optDonePhotos"]),
            ("skip", UZ["optSkipPhoto"]),
        ],
    },
    "general": {
        "kind": "free",
        "prompt": UZ["qGeneral"],
        "options": [],
    },
    "diag_offer": {
        "kind": "buttons",
        "prompt": UZ["qDiagOffer"],
        "options": [
            ("switch_diag", UZ["optSwitchDiag"]),
            ("stay_general", UZ["optStayGeneral"]),
        ],
    },
}

# ---------------------------------------------------------------------------
# Trigger-word detection (contract §0.3 / §4.10) — server-side backstop that
# runs alongside the model-side detection taught by the general policy block.
# Both disease and pest groups map to the single Phase-1 "disease_pest" bucket.
# ---------------------------------------------------------------------------

TRIGGER_WORDS: tuple[str, ...] = (
    # ---- Latin ----
    # disease
    "kasallik", "kasal", "dogʻ", "sargʻayish", "qorayish", "qoʻngʻir dogʻ",
    "chirish", "soʻlish", "barg qurishi", "barg buralishi", "poya chirishi",
    "ildiz chirishi", "zamburugʻ", "bakteriya", "virus", "nima boʻlgan",
    "nega bunday", "davolash kerak",
    # pest
    "zararkunanda", "hasharot", "qurt", "shira", "trips", "kana", "kuya",
    "bit", "lichinka", "barg yeyilgan", "teshiklar bor",
    "hasharot koʻrinyapti", "qanday dori sepaman", "nima bilan ishlov beraman",
    # ---- Cyrillic (Uzbek Cyrillic — farmers/STT may use it) ----
    # disease
    "касаллик", "касал", "доғ", "сарғайиш", "қорайиш", "қўнғир доғ",
    "чириш", "сўлиш", "барг қуриши", "барг бурилиши", "поя чириши",
    "илдиз чириши", "замбуруғ", "бактерия", "вирус", "нима бўлган",
    "нега бундай", "даволаш керак",
    # pest
    "зараркунанда", "ҳашарот", "қурт", "шира", "трипс", "кана", "куя",
    "бит", "личинка", "барг ейилган", "тешиклар бор",
    "ҳашарот кўриняпти", "қандай дори сепаман", "нима билан ишлов бераман",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Persisted shape
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str  # farmer | rais | system | agronom
    kind: str  # text | question | answer | photo | diagnosis | agronom_review
    text: str = ""
    ts: str = Field(default_factory=now_iso)
    # For kind == "photo": the uploaded image's public URL (DO Spaces CDN, or a
    # local path on fallback) so a reader of the message stream — e.g. the
    # agronom admin UI rendering the dialog as the farmer sees it — can show the
    # photo card and enlarge it without correlating against last_diagnosis.
    photo_url: str = ""


class AgronomReview(BaseModel):
    """Spec §7 agronom verification (contract Phase 3). Lives on the chat
    document; surfaced verbatim in build_summary/build_detail."""
    status: str = "none"          # "none" | "pending" | "done"
    requested_at: str = ""        # now_iso() when the farmer requested
    reviewed_at: str = ""         # now_iso() when the review landed
    is_mock: bool = False         # True = AI second-opinion stub (P3.5)
    verdict: str = ""             # "" | "confirmed" | "adjusted"
    expert_summary: str = ""      # Uzbek, farmer-facing, <=600 chars
    expert_notes: list[str] = Field(default_factory=list)   # <=6 x <=300
    # SAME frozen P2.1 Preparation dict shape (all six keys). [] means the
    # AI preparations list stands unchanged.
    adjusted_preparations: list[dict] = Field(default_factory=list)


class ChatDoc(BaseModel):
    id: str
    user_id: str
    title: str = UZ["newChatTitle"]
    query_type: str = ""  # "" | disease_pest | weed | general
    crop_id: str = ""
    crop_name: str = ""
    plant_part: str = ""  # "" | one of TARGET_PARTS
    crop_in_profile: bool = True  # §1.2: committed crop found in the Growz profile
    crop_context_done: bool = False  # §1.3 crop-context dialogue completed (to_symptom)
    # §1.3 anketa answers, keyed by CROP_CONTEXT_QUESTIONS field names.
    # Persisted so a reconnect resumes at the first unanswered question.
    crop_context_answers: dict[str, str] = Field(default_factory=dict)
    symptom_done: bool = False  # symptom dialogue completed (to_photo)
    symptom_summary: str = ""  # to_photo's summary arg, <=300 chars
    photos_collected: int = 0  # multi-photo loop counter; photos themselves are never stored (§3.2)
    general_question: str = ""  # first farmer turn of the general phase, <=300 chars
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    finished: bool = False
    last_diagnosis: dict | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    agronom_review: AgronomReview | None = None   # NEW in Phase 3


# Phase 3 (contract addendum P3.1) — caps shared by the human submit
# endpoint AND the mock runner. Frozen.
_AGRONOM_SUMMARY_MAX = 600
_AGRONOM_NOTE_MAX = 300
_AGRONOM_NOTES_CAP = 6
_AGRONOM_PREP_CAP = 4          # same cap as P2 PREP_CAP


def _coerce_agronom_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def sanitize_expert_payload(
    expert_summary: object,
    expert_notes: object,
    adjusted_preparations: object,
) -> tuple[str, list[str], list[dict]]:
    """Clamp untrusted expert fields to the frozen shape. Never raises."""
    try:
        summary = str(expert_summary if expert_summary is not None else "").strip()[
            :_AGRONOM_SUMMARY_MAX
        ]
    except Exception:  # noqa: BLE001
        summary = ""

    notes: list[str] = []
    try:
        for item in (expert_notes or []):
            text = str(item).strip()
            if not text:
                continue
            notes.append(text[:_AGRONOM_NOTE_MAX])
            if len(notes) >= _AGRONOM_NOTES_CAP:
                break
    except Exception:  # noqa: BLE001
        notes = []

    preps: list[dict] = []
    try:
        if isinstance(adjusted_preparations, list):
            for item in adjusted_preparations:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                preps.append({
                    "name": name,
                    "dose_min": _coerce_agronom_float(item.get("dose_min")),
                    "dose_max": _coerce_agronom_float(item.get("dose_max")),
                    "unit": str(item.get("unit") or ""),
                    "type": str(item.get("type") or "").lower(),
                    "description": str(item.get("description") or "").strip()[:300],
                })
                if len(preps) >= _AGRONOM_PREP_CAP:
                    break
    except Exception:  # noqa: BLE001
        preps = []

    return summary, notes, preps


def new_chat_doc(user_id: str) -> ChatDoc:
    now = now_iso()
    return ChatDoc(id=uuid.uuid4().hex, user_id=user_id, created_at=now, updated_at=now)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def derive_title(doc: ChatDoc) -> str:
    """Recomputed whenever a selection lands (contract §3.6); first
    non-empty rule wins."""
    if doc.query_type == "general":
        if doc.general_question:
            return doc.general_question[:60]
        for msg in doc.messages:
            if msg.role == "farmer" and msg.kind == "text" and msg.text:
                return msg.text[:60]
        return UZ["generalTitle"]
    if doc.crop_name:
        if doc.query_type == "disease_pest":
            return f"{doc.crop_name} — {UZ['qtDisease']}"
        if doc.query_type == "weed":
            return f"{doc.crop_name} — {UZ['qtWeed']}"
        return doc.crop_name
    if doc.query_type:
        for option_id, label in STEPS["query_type"]["options"]:
            if option_id == doc.query_type:
                return label
    for msg in doc.messages:
        if msg.role == "farmer" and msg.kind == "text" and msg.text:
            return msg.text[:60]
    return UZ["newChatTitle"]


def _message_dict(m: ChatMessage) -> dict:
    out = {"role": m.role, "text": m.text, "ts": m.ts, "kind": m.kind}
    if m.photo_url:  # only present on photo messages — keeps other shapes stable
        out["photo_url"] = m.photo_url
    return out


def build_summary(doc: ChatDoc) -> dict:
    """The list/create response shape (contract §2.1/§2.2)."""
    last = doc.messages[-1] if doc.messages else None
    return {
        "id": doc.id,
        "user_id": doc.user_id,
        "title": doc.title,
        "query_type": doc.query_type,
        "crop_id": doc.crop_id,
        "crop_name": doc.crop_name,
        "plant_part": doc.plant_part,
        "symptom_done": doc.symptom_done,
        "symptom_summary": doc.symptom_summary,
        "crop_context_answers": dict(doc.crop_context_answers),
        "general_question": doc.general_question,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        "finished": doc.finished,
        "message_count": len(doc.messages),
        "last_message": _message_dict(last) if last is not None else None,
        "agronom_review": (
            doc.agronom_review.model_dump() if doc.agronom_review is not None else None
        ),
    }


def build_detail(doc: ChatDoc) -> dict:
    """The single-chat fetch response shape (contract §2.3): summary fields
    plus ``messages`` and ``last_diagnosis`` (no message_count/last_message)."""
    return {
        "id": doc.id,
        "user_id": doc.user_id,
        "title": doc.title,
        "query_type": doc.query_type,
        "crop_id": doc.crop_id,
        "crop_name": doc.crop_name,
        "plant_part": doc.plant_part,
        "symptom_done": doc.symptom_done,
        "symptom_summary": doc.symptom_summary,
        "crop_context_answers": dict(doc.crop_context_answers),
        "general_question": doc.general_question,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        "finished": doc.finished,
        "last_diagnosis": doc.last_diagnosis,
        "messages": [_message_dict(m) for m in doc.messages],
        "agronom_review": (
            doc.agronom_review.model_dump() if doc.agronom_review is not None else None
        ),
    }
