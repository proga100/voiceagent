"""``ChatGuide`` — the server-driven hybrid guided-flow state machine.

The Gemini Live connection fixes its system prompt and tool list at connect
time, and the crop is only chosen at guided-flow step 2, so the guided flow
cannot ride the connect-time prompt (see ``docs/multichat_contract.md`` §0).
Instead: Rais speaks each question via the already-proven ``[TIZIM]`` text-turn
injection (``GeminiLiveSession._speak_text``), the server simultaneously sends
a deterministic ``chat.question`` event that renders the option buttons, and
either a tap (``chat.answer``) or a spoken answer (the ``select_option`` /
``to_symptom`` / ``to_photo`` / ``switch_to_diagnostic`` Live tools) funnels into
:meth:`ChatGuide.on_answer` / :meth:`ChatGuide.handle_tool` — both advance the
flow identically.

v2 (contract §0.1) extends the v1 diagnostic-only flow with a symptom
dialogue between ``plant_part`` and ``photo``, and a whole new "general
question" conversation (phase ``general``) that a server-detected trigger
word or a spoken/tapped decision can switch into the diagnostic flow.

Everything here is FAIL-OPEN (contract §8): any unexpected error marks the
guide ``degraded`` and hands the call back to a plain consult session; it
never raises into the WebSocket loop.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable

from app.config import Settings
from app.voice.chat.models import STEPS, TRIGGER_WORDS, UZ, ChatDoc, derive_title
from app.voice.chat.store import ChatStore

logger = logging.getLogger("voice.chat.guide")

SendJson = Callable[[dict], Awaitable[None]]
SpeakRaw = Callable[[str], Awaitable[None]]

# ---------------------------------------------------------------------------
# Phase mapping (contract §4.1) — pending-step id -> phase carried on
# chat.state. A pending step id not in this table (there is none today)
# would default to "guide"; pending None means "consult".
# ---------------------------------------------------------------------------

PHASE_FOR_STEP = {
    "query_type": "guide", "crop": "guide", "plant_part": "guide",
    "crop_context": "crop_context",
    "symptom": "symptom", "general": "general", "photo": "guide",
}

# ---------------------------------------------------------------------------
# Spoken scripts (contract §4.6) — EXACT templates.
# ---------------------------------------------------------------------------

_SCRIPT_A = (
    "[TIZIM][SAVOL step={step}] Qisqa salomlash (1 jumla), soʻng soʻra: "
    "«{q}» Variantlar: {opts}. Fermer ogʻzaki javob bersa select_option chaqir."
)
_SCRIPT_B = (
    "[TIZIM] Fermer tugma orqali «{label}» deb tanladi. Buni bir soʻz bilan "
    "tasdiqlab, keyingi savolni ber. [SAVOL step={next_step}] «{q}» "
    "Variantlar: {opts}."
)
_SCRIPT_C = (
    "Qabul qilindi: «{label}». Endi keyingi savolni ber. "
    "[SAVOL step={next_step}] «{q}» Variantlar: {opts}."
)
_SCRIPT_D = (
    "Variant aniqlanmadi. Fermerdan qayta soʻra yoki ekrandagi tugmalardan "
    "foydalanishini ayt. [SAVOL step={step}] «{q}» Variantlar: {opts}."
)
_SCRIPT_D_CROP_SUFFIX = " Fermer «Ekinlar» tugmasidan roʻyxatni ochishi mumkin."
_SCRIPT_F = (
    "[TIZIM] Fermer avvalgi suhbatga qaytdi. Qisqa salomlash va oxirgi mavzu "
    "({title}) boʻyicha hozir qanday yordam kerakligini soʻra. 1-2 jumla."
)

_GUIDE_POLICY_BLOCK = (
    "[YOʻRIQLI SOʻROV] Suhbat boshida sen fermerdan bir nechta qisqa savol "
    "bilan muammo turini, ekinni va oʻsimlik qismini aniqlaysan. "
    "[TIZIM][SAVOL] bilan kelgan savolni OʻZ SOʻZLARING bilan qisqa ber "
    "(1-2 jumla), boshqa mavzuga oʻtma. Ekranda fermerga tugmalar ham "
    "koʻrinadi — u bosishi yoki ogʻzaki aytishi mumkin. "
    "MUHIM: har bosqichda fermerning OʻZI tugma bosishini yoki ogʻzaki "
    "aytishini KUT. Eslagan maʼlumotlaring (masalan fermer avval qaysi "
    "ekin ekkani yoki oldingi muammosi) asosida javobni OʻZING toʻldirma "
    "va bosqichlarni oʻzing oʻtkazib yuborma — faqat fermer aytganini "
    "select_option bilan yozib bor. Fermer OGʻZAKI javob bersa, DARHOL "
    "select_option funksiyasini chaqir (step_id — hozirgi bosqich). Tugma "
    "bosilsa [TIZIM] xabari keladi — qayta soʻrama, keyingi savolga oʻt. "
    "Yoʻriqli soʻrov tugaguncha tashxis qoʻyma, rasm soʻrama va fermerning "
    "eski muammolarini eslatma. Fermer «Umumiy savol berish»ni tanlasa yoki "
    "belgilar bosqichi boshlansa, [TIZIM] xabaridagi koʻrsatmaga oʻt. "
    "Ekin konteksti bosqichida ham, belgilar bosqichida ham savollar berib, "
    "kontekst yetarli boʻlgach mos funksiyani chaqirasan (ekin kontekstida "
    "to_symptom, belgilar bosqichida to_photo)."
)

_GENERAL_POLICY_BLOCK = (
    "[UMUMIY SAVOL] Bu suhbat umumiy savol rejimida. Fermer savolini "
    "aytadi. Kontekst yetarli boʻlsa darhol javob ber; yetmasa BITTADAN, "
    "hammasi boʻlib koʻpi bilan 5 ta aniqlashtiruvchi savol ber (qaysi "
    "ekin; qaysi viloyat/tuman; qaysi faza; ekish sanasi; oxirgi sugʻorish; "
    "oxirgi oʻgʻitlash; tuproq/dala holati). Javob tartibi: qisqa xulosa; "
    "amaliy tavsiya; nima qilish kerak; nimani qilmaslik; muddat/meʼyor "
    "(agar kerak boʻlsa); keyingi qadam. Savol oʻgʻit, preparat yoki himoya "
    "vositasi haqida boʻlsa: mos kategoriyani, taʼsir etuvchi modda yoki "
    "mahsulot turini va ehtiyot chorasini ayt, Agroapteka boʻlimini matnda "
    "eslatib oʻt. Trigger soʻzlar (kasallik: kasallik, kasal, dogʻ, "
    "sargʻayish, qorayish, qoʻngʻir dogʻ, chirish, soʻlish, barg qurishi, "
    "barg buralishi, poya chirishi, ildiz chirishi, zamburugʻ, bakteriya, "
    "virus, «nima boʻlgan», «nega bunday», «davolash kerak»; zararkunanda: "
    "zararkunanda, hasharot, qurt, shira, trips, kana, kuya, bit, lichinka, "
    "barg yeyilgan, teshiklar bor, hasharot koʻrinyapti, «qanday dori "
    "sepaman», «nima bilan ishlov beraman»). Bu soʻzlarning KIRILL yozuvi ham "
    "aynan trigger (масалан: касаллик, касал, доғ, сарғайиш, чириш, сўлиш, "
    "замбуруғ, бактерия, вирус; зараркунанда, ҳашарот, қурт, шира, кана, куя, "
    "бит, личинка, «нима билан ишлов бераман» ва ҳ.к.). Shu yoki shunga oʻxshash "
    "belgilar sezilsa fermerga ayt: «Bu kasallik yoki zararkunanda bilan "
    "bogʻliq boʻlishi mumkin. Aniqlash uchun bir nechta savol beraman» va "
    "fermer rozi boʻlsa switch_to_diagnostic funksiyasini chaqir."
)

# contract §4.6: the photo step's spoken prompt is the spec-§3 sentence, NOT
# the on-screen UZ["qPhoto"] caption (which chat.question keeps carrying).
_PHOTO_SPOKEN_Q = (
    "Aniqroq tahlil uchun rasm yuboring: umumiy koʻrinish, zararlangan joy "
    "yaqindan, imkon boʻlsa ildiz. Yuborganingizdan soʻng javob beraman."
)

# contract §4.6: the photo step's {opts} carries an extra note (only "skip"
# is a spoken option; the photo itself comes from the on-screen button).
_PHOTO_OPTS = (
    f"take_photo=«{UZ['optTakePhoto']}» (ekranda kamera ochadi), "
    f"done_photos=«{UZ['optDonePhotos']}» (tashxisni boshlaydi), "
    f"skip=«{UZ['optSkipPhoto']}»"
)

_SY_BODY = (
    "Endi belgilar suhbati — rasmdan OLDIN belgilarni ogʻzaki aniqlaysan; "
    "tashxis hech qachon faqat rasmga qurilmaydi. Har safar FAQAT BITTA "
    "qisqa savol ber. Asosiy savollar: «Nima sodir boʻlyapti?», «Belgilar "
    "qachondan beri bor?», «Muammo butun daladami yoki ayrim "
    "oʻsimliklardami?», «Belgilar kuchayyaptimi yoki bir xilmi?», «Oxirgi "
    "ishlov qachon va nima bilan?», «Oxirgi sugʻorish qachon?», «Oxirgi "
    "oʻgʻitlash qachon?». Kerak boʻlsa qoʻshimcha savollar (koʻpi bilan 10 "
    "ta): oʻsimlik belgilari, ishlov tarixi, oziqlanish, sugʻorish, tuproq, "
    "oldingi ekin. Kontekst yetarli boʻlgach DARHOL to_photo funksiyasini "
    "chaqir (summary — belgilarning 1-2 jumlalik xulosasi). Fermer "
    "keyinroq ekranda «Rasmga oʻtish» tugmasini koʻrishi mumkin — bosilsa "
    "[TIZIM] xabari keladi."
)

_GEN_BODY = (
    "Fermerdan savolini soʻra: «Savolingizni bemalol ayting». Savolni "
    "eshitgach kontekst yetarliligini baholab koʻr: yetarli boʻlsa darhol "
    "javob ber; yetmasa BITTADAN, hammasi boʻlib koʻpi bilan 5 ta "
    "aniqlashtiruvchi savol ber (qaysi ekin; qaysi viloyat/tuman; qaysi "
    "faza; ekish sanasi; oxirgi sugʻorish; oxirgi oʻgʻitlash; tuproq/dala "
    "holati). Javob tartibi: qisqa xulosa; amaliy tavsiya; nima qilish "
    "kerak; nimani qilmaslik; muddat/meʼyor (agar kerak boʻlsa); keyingi "
    "qadam. Savol oʻgʻit, preparat yoki himoya vositasi haqida boʻlsa: mos "
    "kategoriyani, taʼsir etuvchi modda yoki mahsulot turini va ehtiyot "
    "chorasini ayt, Agroapteka boʻlimini matnda eslatib oʻt. Suhbatda "
    "kasallik yoki zararkunanda belgilari sezilsa: «Bu kasallik yoki "
    "zararkunanda bilan bogʻliq boʻlishi mumkin. Aniqlash uchun bir nechta "
    "savol beraman» deb ayt va fermer rozi boʻlsa switch_to_diagnostic "
    "funksiyasini chaqir."
)

# BUG 1 fix: the exact spec-§1.3 reminder sentence Rais must say VERBATIM, as
# its own utterance, BEFORE asking anything. Do not paraphrase or shorten it.
_CROP_CONTEXT_REMINDER = (
    "Keyingi safar ekinni profilingizga qoʻshib qoʻying. Shunda faza, "
    "agrotexnika va tarixni hisobga olib, aniqroq tavsiya bera olaman."
)

# §1.3 crop-context phase (crop NOT in the Growz profile): gather the profile
# facts by ASKING, one at a time, BEFORE the symptom dialogue. Mirrors _SY_BODY.
# BUG 1 fix: Rais must (a) say _CROP_CONTEXT_REMINDER aynan shu jumla bilan —
# as its OWN first utterance, never paraphrased and never merged with a
# question — and only THEN (b) ask the four §1.3 questions ONE PER TURN, in
# order, never two in one message.
_CROP_CONTEXT_BODY = (
    f"AVVAL, aynan shu jumla bilan boshla — soʻzini oʻzgartirma, boshqa gap "
    f"bilan qoʻshib yuborma, bu ALOHIDA gap boʻlsin: «{_CROP_CONTEXT_REMINDER}» "
    "Shundan KEYIN, boshqa xabarda, ekin haqida qisqa kontekst yigʻishga "
    "oʻt. Har safar FAQAT BITTA qisqa savol ber — hech qachon ikkita savol "
    "bir xabarda boʻlmasin va savolni eslatma bilan qoʻshib yuborma. "
    "Savollarni shu tartibda, bittadan ber: «Qaysi viloyat va "
    "tumandasiz?», soʻng «Ekin qachon ekilgan?», soʻng «Hozir taxminan "
    "qaysi rivojlanish fazasida?», soʻng «Oxirgi agrotexnik ishlar qachon "
    "va qanday boʻlgan?». Har bir savoldan keyin fermer javobini kut, "
    "keyingi savolga shundan keyingina oʻt. Javoblarni keyin tashxisda "
    "ishlat. Toʻrttala maʼlumot ham yigʻilgach DARHOL to_symptom "
    "funksiyasini chaqir. Fermer keyinroq ekranda «Belgilarga oʻtish» "
    "tugmasini koʻrishi mumkin — bosilsa [TIZIM] xabari keladi."
)

_SCRIPT_CC_B = (
    "[TIZIM] Fermer tugma orqali «{label}» deb tanladi. Buni bir soʻz bilan "
    "tasdiqla. [SAVOL step=crop_context] {body}"
)
_SCRIPT_CC_C = "Qabul qilindi: «{label}». [SAVOL step=crop_context] {body}"
_SCRIPT_CC_A = (
    "[TIZIM][SAVOL step=crop_context] Qisqa salomlash (1 jumla), soʻng "
    "davom et: {body}"
)
_SCRIPT_CC_CAP = (
    "[TIZIM] Ekin konteksti yetarli. [SAVOL step=plant_part] «{q}» "
    "Variantlar: {opts}."
)

_SCRIPT_SY_B = (
    "[TIZIM] Fermer tugma orqali «{label}» deb tanladi. Buni bir soʻz bilan "
    "tasdiqla. [SAVOL step=symptom] {body}"
)
_SCRIPT_SY_C = "Qabul qilindi: «{label}». [SAVOL step=symptom] {body}"
_SCRIPT_SY_A = (
    "[TIZIM][SAVOL step=symptom] Qisqa salomlash (1 jumla), soʻng davom "
    "et: {body}"
)
_SCRIPT_SY_CAP = (
    "[TIZIM] Belgilar suhbati yetarli. [SAVOL step=photo] «{q}» "
    "Variantlar: {opts}."
)

_SCRIPT_G_B = (
    "[TIZIM] Fermer tugma orqali «{label}» deb tanladi. "
    f"[UMUMIY SAVOL] {_GEN_BODY}"
)
_SCRIPT_G_C = f"Qabul qilindi: «{{label}}». [UMUMIY SAVOL] {_GEN_BODY}"
_SCRIPT_G_A = (
    "[TIZIM] Fermer avvalgi umumiy savol suhbatiga qaytdi ({title}). Qisqa "
    "salomlash (1 jumla), soʻng savol boʻyicha qanday yordam kerakligini "
    "soʻra. [UMUMIY SAVOL] qoidalari amalda qoladi."
)

_SCRIPT_TRIG = (
    "[TIZIM] Fermer soʻzlarida kasallik yoki zararkunanda belgilariga "
    "ishora bor. Fermerga ayt: «Bu kasallik yoki zararkunanda bilan "
    "bogʻliq boʻlishi mumkin. Aniqlash uchun bir nechta savol beraman.» "
    "Fermer rozi boʻlsa switch_to_diagnostic funksiyasini chaqir. Ekranda "
    "tasdiqlash tugmalari ham chiqdi."
)

_SCRIPT_SW_B = (
    "[TIZIM] Fermer aniqlash jarayoniga oʻtishni tanladi. Muammo turi: "
    "kasallik/zararkunanda. [SAVOL step=crop] «{q}» Variantlar: {opts}."
)
_SCRIPT_SW_C = (
    "Aniqlash jarayoni boshlandi. Muammo turi: kasallik/zararkunanda. "
    "[SAVOL step=crop] «{q}» Variantlar: {opts}."
)

_SCRIPT_STAY = (
    "[TIZIM] Fermer umumiy savolda davom etishni tanladi. Savoliga qaytib "
    "javob berishni davom ettir."
)

_SCRIPT_D_SY = (
    "Hozir belgilar bosqichi: savollarni davom ettir, kontekst yetarli "
    "boʻlgach to_photo chaqir."
)
_SCRIPT_D_G = (
    "Hozir umumiy savol rejimi: savolga javob ber. Aniqlash kerak boʻlsa "
    "switch_to_diagnostic chaqir."
)
_SCRIPT_D_TP = "Hozir belgilar bosqichi emas. Joriy bosqichni davom ettir."
_SCRIPT_D_SW = "Hozir umumiy savol rejimi emas. Joriy bosqichni davom ettir."
_SCRIPT_D_CC = (
    "Hozir ekin konteksti bosqichi: savollarni davom ettir, maʼlumot "
    "yetarli boʻlgach to_symptom chaqir."
)
_SCRIPT_D_TS = "Hozir ekin konteksti bosqichi emas. Joriy bosqichni davom ettir."

_OKINA_RE = re.compile(r"[ʻ’']")

# Farmer-turn ceiling that forces the symptom -> photo advance even if the
# model never calls to_photo and the farmer never taps (contract §4.8).
_SYMPTOM_TURN_CAP = 12

# Farmer-turn ceiling that forces crop_context -> plant_part even if the model
# never calls to_symptom and the farmer never taps (mirror of contract §4.8).
_CROP_CONTEXT_TURN_CAP = 8

# BUG 2 fix: the chat.question payload for crop_context / symptom is emitted
# WITHOUT the advance button (options=[]) until the farmer has given this many
# answers in the phase — showing it from turn 1 confused farmers into tapping
# past the interview. Once the counter reaches the threshold the pending
# question is re-emitted once, now WITH the button. Both stay well under the
# phase's backstop cap above, which still fires regardless (see
# note_farmer_turn).
_CROP_CONTEXT_CHIP_AFTER = 3
_SYMPTOM_CHIP_AFTER = 7


def _norm_crop(s: str) -> str:
    return _OKINA_RE.sub("'", (s or "").strip().lower())


# Latin a-z, digits, apostrophe AND the full Cyrillic block (U+0400–U+04FF,
# covers Uzbek ў/ғ/қ/ҳ/ё) — so Cyrillic input isn't stripped before matching.
_WORD_SPLIT_RE = re.compile(r"[^a-z0-9'Ѐ-ӿ]+")


def has_trigger(text: str) -> bool:
    """Deterministic disease/pest word-boundary scan (contract §4.10):
    multi-word phrases match as a substring; long words (>=5 chars) match as
    a word-prefix (so "kasalligi" fires "kasal"); short words match only as a
    whole word (so "bitta" never fires "bit")."""
    t = _norm_crop(text)
    words = {w for w in _WORD_SPLIT_RE.split(t) if w}
    for trig in TRIGGER_WORDS:
        tn = _norm_crop(trig)
        if " " in tn:
            if tn in t:
                return True
        elif len(tn) >= 5:
            if any(w.startswith(tn) for w in words):
                return True
        elif tn in words:
            return True
    return False


def _find_crop_match(spoken: str, crops: list[dict]) -> dict | None:
    """Exact normalized name match first, else accepted only if exactly ONE
    crop name contains (or is contained by) the value (contract §1.6)."""
    spoken = (spoken or "").strip()
    if not spoken or not crops:
        return None
    norm_spoken = _norm_crop(spoken)
    for c in crops:
        if _norm_crop(c.get("name", "")) == norm_spoken:
            return c
    candidates = [
        c for c in crops
        if norm_spoken and (
            norm_spoken in _norm_crop(c.get("name", ""))
            or _norm_crop(c.get("name", "")) in norm_spoken
        )
    ]
    return candidates[0] if len(candidates) == 1 else None


def _label_for(step_id: str, option_id: str) -> str | None:
    for oid, label in STEPS[step_id]["options"]:
        if oid == option_id:
            return label
    return None


def _profile_injection_text(profile: dict) -> str:
    """A compact Uzbek one-liner of the saved-planting profile, for the hidden
    context turn pushed to the model when the crop is committed."""
    bits: list[str] = []
    if profile.get("crop"):
        bits.append(f"ekin: {profile['crop']}")
    if profile.get("planted_date"):
        d = profile["planted_date"]
        days = profile.get("days_since_planting")
        bits.append(f"ekilgan sana: {d}" + (f" ({days} kun oldin)" if days is not None else ""))
    if profile.get("growth_period_days"):
        bits.append(f"oʻsish davri: {profile['growth_period_days']} kun")
    if profile.get("region"):
        bits.append(f"hudud: {profile['region']}")
    fld = profile.get("field") or {}
    if fld.get("name"):
        label = fld["name"]
        if fld.get("area"):
            unit = fld.get("unit", "")
            label += f" ({fld['area']}{' ' + unit if unit else ''})"
        bits.append(f"dala: {label}")
    if profile.get("current_agrotech_task"):
        bits.append(f"joriy agrotexnika: {profile['current_agrotech_task']}")
    return "; ".join(bits)


# The saved-planting profile, injected on crop commit (hidden turn on tap,
# folded into the tool-ack on voice). For an UNSAVED crop the profile questions
# are driven by the crop_context phase (_CROP_CONTEXT_BODY) instead.
_PROFILE_CONTEXT = (
    "[Ichki maʼlumot — Growz profilidan, ovoz chiqarib takrorlama] {facts}. "
    "Fermer oʻzi soʻrasa (masalan «ekinim haqida nima bilasan?») shu "
    "maʼlumotlardan foydalanib javob ber."
)


def _with_context(note: str, ctx: str) -> str:
    """Fold the saved-planting profile into a VOICE tool-ack note — the model
    reads it as the function
    response (safe; no separate turn while a tool call is pending)."""
    if not ctx:
        return note
    return f"{note} {ctx}"


def _history_recap_block(doc: ChatDoc) -> str:
    """The compact "resumed chat" history recap (contract §4.5): last 12
    messages, 200 chars each, 2500-char cap. Shared by the finished-chat
    resume AND (v2) any unfinished chat resumed mid-conversation."""
    qt_label = _label_for("query_type", doc.query_type) or doc.query_type
    line = f"Soʻrov: ekin — {doc.crop_name}, muammo — {qt_label}"
    if doc.plant_part:
        part_label = _label_for("plant_part", doc.plant_part) or doc.plant_part
        line += f", qismi — {part_label}"
    line += "."

    pieces: list[str] = []
    total = 0
    for m in doc.messages[-12:]:
        role = "FERMER" if m.role == "farmer" else "RAIS"
        piece = f"{role}: {m.text[:200]}"
        if total + len(piece) > 2500:
            break
        pieces.append(piece)
        total += len(piece)
    history = "\n".join(pieces)

    return (
        "[SUHBAT TARIXI] Bu avvalgi suhbatning davomi. "
        f"{line}\nOxirgi xabarlar:\n{history}\n"
        "Suhbatni shu kontekstdan tabiiy davom ettir."
    )


def build_guide_prompt_block(doc: ChatDoc) -> str:
    """The static system-prompt block appended for a chat-bound session
    (contract §4.5): the guide policy (diagnostic or general) for an
    unfinished chat plus a history recap when resuming mid-conversation, or
    the history recap alone for a resumed (finished) one."""
    if doc.finished:
        return _history_recap_block(doc)

    block = _GENERAL_POLICY_BLOCK if doc.query_type == "general" else _GUIDE_POLICY_BLOCK
    if doc.messages:
        block = f"{block}\n\n{_history_recap_block(doc)}"
    return block


class ChatGuide:
    """One instance per chat-bound voice session; owns the pending guided
    step, emits the WS chat.* events, and drives the [TIZIM] scripts."""

    def __init__(
        self,
        settings: Settings,
        store: ChatStore,
        doc: ChatDoc,
        *,
        send_json: SendJson,
        speak_raw: SpeakRaw,
        inject_context: SpeakRaw | None = None,
        finalize_case: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self._s = settings
        self._store = store
        self.doc = doc
        self._send_json = send_json
        self._speak_raw = speak_raw
        # Optional: deterministic diagnosis trigger (done_photos / skip /
        # 5-photo cap). None in tests / when the provider can't finalize.
        self._finalize_case = finalize_case
        # Optional: push hidden context into the LIVE model mid-session (a
        # non-spoken user turn). Used to auto-load the farmer's profile when the
        # crop is committed. None in tests / when the provider can't inject.
        self._inject_context = inject_context
        self.degraded = False
        # In-memory only (contract §3.2 has no persisted photo_id field —
        # photos themselves are never stored). Reset every session.
        self._photo_id = ""
        # Memory-crop quick-pick chips (contract §4.9): the farmer's
        # remembered crop names (set via set_memory_crops), resolved against
        # the Growz catalogue and cached the first time the crop question is
        # emitted this session.
        self._memory_crop_names: list[str] = []
        self._crop_chips: list[dict] | None = None
        # Server-driven «Ha»/«Yoʻq»: the crop question first offers the two
        # buttons; answering «Ha» (saved_crops) flips this and the question is
        # re-sent with the actual profile chips. Transient — a reconnect goes
        # back to «Ha»/«Yoʻq», which is fine.
        self._crop_saved_expanded = False
        # Symptom-phase backstop counter (contract §4.8).
        self._symptom_turns = 0
        self._symptom_backstop_scheduled = False
        # BUG 2 fix: at most one advance-chip reveal per phase (mirrors the
        # backstop-scheduled flags above). Runtime-only, like the turn
        # counters themselves — a reconnect resets both to 0/False, so the
        # chip is hidden again after resume until the threshold re-accrues;
        # the backstop cap still covers the phase regardless (accepted
        # behaviour, contract §4.11 resume matrix has no "chip shown" state).
        self._symptom_chip_shown = False
        # Crop-context-phase backstop counter (mirror of contract §4.8).
        self._crop_context_turns = 0
        self._crop_context_backstop_scheduled = False
        self._crop_context_chip_shown = False
        # General-phase trigger offer (contract §4.10): at most one per
        # session, whether accepted or declined.
        self._general_offered = False

    @property
    def finished(self) -> bool:
        return self.doc.finished

    def records_transcript(self) -> bool:
        """True while every committed voice turn should be persisted raw
        (contract §4.7): the symptom/general phases are the diagnostic or
        consultative substance, same as a finished chat's free consult."""
        return self.doc.finished or self.pending_step() in ("crop_context", "symptom", "general")

    # ---- pending-step derivation (contract §4.1) ---------------------------

    def pending_step(self) -> str | None:
        d = self.doc
        if not d.query_type:
            return "query_type"
        if d.query_type == "general":
            return None if d.finished else "general"
        if not d.crop_id:
            return "crop"
        if (d.query_type == "disease_pest" and not d.crop_in_profile
                and not d.crop_context_done):
            return "crop_context"
        if d.query_type != "weed" and not d.plant_part:
            return "plant_part"
        if d.query_type == "disease_pest" and not d.symptom_done:
            return "symptom"
        if not d.finished:
            return "photo"
        return None

    # ---- memory-crop quick-pick chips (contract §4.9) -----------------------

    def set_memory_crops(self, crops: list[str]) -> None:
        """The farmer's remembered crop names, in stored order. Resolved
        lazily (and cached) at the first emission of the crop question."""
        self._memory_crop_names = list(crops or [])

    async def _resolve_crop_chips(self) -> list[dict]:
        if self._crop_chips is not None:
            return self._crop_chips
        chips: list[dict] = []
        try:
            if self._memory_crop_names:
                from app.voice.enrich import get_crops

                crops = await get_crops(self._s)
                if crops:
                    seen_ids: set[str] = set()
                    for name in self._memory_crop_names:
                        match = _find_crop_match(name, crops)
                        if match is not None and match["id"] not in seen_ids:
                            chips.append({"id": match["id"], "label": match["name"]})
                            seen_ids.add(match["id"])
                        if len(chips) >= 4:
                            break
        except Exception:  # noqa: BLE001 — chip resolution is best-effort
            logger.exception("memory crop chip resolution failed")
            chips = []
        self._crop_chips = chips
        return chips

    async def _crop_question_options(self) -> list[tuple[str, str]]:
        """The «Ha»/«Yoʻq» logic lives HERE, not in the app: the question
        ALWAYS opens with the two buttons — the farmer answers the yes/no it
        literally asks, profile or not. «Ha» (saved_crops) re-sends the same
        question expanded: the profile chips, or just the catalogue button
        when the profile turned out to be empty."""
        # Resolve chips even in the un-expanded phase: the spoken directive
        # (_opts_str) tells the model what hides behind «Ha», and the voice
        # path needs that regardless of which buttons are on screen.
        chips = await self._resolve_crop_chips()
        if not self._crop_saved_expanded:
            return [
                ("saved_crops", UZ["optCropYes"]),
                ("open_crop_picker", UZ["optCropNo"]),
            ]
        if not chips:
            return [("open_crop_picker", UZ["optCrops"])]
        return [(c["id"], c["label"]) for c in chips] + [
            ("open_crop_picker", UZ["optCropNo"])
        ]

    def _opts_str(self, step_id: str) -> str:
        if step_id == "photo":
            return _PHOTO_OPTS
        if step_id == "crop":
            chips = self._crop_chips or []
            if chips:
                chip_str = ", ".join(f"«{c['label']}»" for c in chips)
                return (
                    f"Ha=«ekin profilda bor» (profildagi ekinlar: {chip_str}), "
                    "Yoʻq=«roʻyxatdan tanlaydi». Fermer toʻgʻridan-toʻgʻri ekin "
                    "nomini aytsa ham select_option bilan yozib bor."
                )
            return (
                "Yoʻq=«roʻyxatdan tanlaydi» (profilda saqlangan ekin yoʻq). "
                "Fermer ekin nomini aytsa select_option bilan yozib bor."
            )
        return ", ".join(f"{oid}=«{label}»" for oid, label in STEPS[step_id]["options"])

    # ---- Live tools (contract §1.6) -----------------------------------------

    def build_tools(self):
        from google.genai import types

        select_option = types.FunctionDeclaration(
            name="select_option",
            description=(
                "Yoʻriqli soʻrov bosqichida fermer OGʻZAKI javob berganda "
                "uning tanlovini qayd etish. step_id — hozirgi bosqich; "
                "option_id — variant identifikatori; ekin bosqichida "
                "option_id oʻrniga value maydonida fermer aytgan ekin "
                "nomini yubor."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "step_id": types.Schema(
                        type=types.Type.STRING,
                        enum=["query_type", "crop", "plant_part", "photo"],
                    ),
                    "option_id": types.Schema(type=types.Type.STRING),
                    "value": types.Schema(type=types.Type.STRING),
                },
                required=["step_id"],
            ),
        )
        to_photo = types.FunctionDeclaration(
            name="to_photo",
            description=(
                "Belgilar suhbati yetarli boʻlganda — muammo belgilari, "
                "davomiyligi va tarqalishi aniq boʻlgach — rasm bosqichiga "
                "oʻtish. Faqat belgilar bosqichida chaqir. summary — "
                "belgilarning 1-2 jumlalik xulosasi (oʻzbekcha)."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "summary": types.Schema(type=types.Type.STRING),
                },
            ),
        )
        to_symptom = types.FunctionDeclaration(
            name="to_symptom",
            description=(
                "Ekin konteksti yigʻilganda — viloyat/tuman, ekish sanasi, "
                "rivojlanish fazasi va oxirgi agrotexnik ishlar aniq "
                "boʻlgach — keyingi savolga (oʻsimlik qismi) oʻtish. Faqat "
                "ekin konteksti bosqichida chaqir."
            ),
            parameters=types.Schema(type=types.Type.OBJECT, properties={}),
        )
        switch_to_diagnostic = types.FunctionDeclaration(
            name="switch_to_diagnostic",
            description=(
                "Umumiy savol suhbatida kasallik yoki zararkunanda belgilari "
                "sezilsa va fermer aniqlashga rozi boʻlsa, aniqlash (tashxis) "
                "jarayonini boshlash. Faqat umumiy savol rejimida chaqir."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "reason": types.Schema(type=types.Type.STRING),
                },
            ),
        )
        return [types.Tool(function_declarations=[
            select_option, to_symptom, to_photo, switch_to_diagnostic,
        ])]

    async def handle_tool(self, name: str, args: dict) -> dict | None:
        """``set_tool_extension`` handler: None for anything but the four
        multichat tools (falls through to gemini_live's unknown_tool ack)."""
        if name == "select_option":
            return await self._handle_select_option(args or {})
        if name == "to_symptom":
            return await self._handle_to_symptom(args or {})
        if name == "to_photo":
            return await self._handle_to_photo(args or {})
        if name == "switch_to_diagnostic":
            return await self._handle_switch_to_diagnostic(args or {})
        return None

    async def _handle_select_option(self, args: dict) -> dict:
        try:
            if self.degraded or self.doc.finished:
                return {"status": "invalid", "note": "Yoʻriqli soʻrov allaqachon yakunlangan."}
            step_id = str(args.get("step_id") or "")
            option_id = str(args.get("option_id") or "").strip()
            value = str(args.get("value") or "").strip()
            pending = self.pending_step()
            if pending is None:
                return {"status": "invalid", "note": "Yoʻriqli soʻrov allaqachon yakunlangan."}
            if pending == "crop_context":
                return {"status": "invalid", "note": _SCRIPT_D_CC}
            if pending == "symptom":
                return {"status": "invalid", "note": _SCRIPT_D_SY}
            if pending == "general":
                return {"status": "invalid", "note": _SCRIPT_D_G}
            if step_id != pending:
                return {"status": "invalid", "note": self._invalid_note(pending)}

            if step_id == "crop":
                match = await self._match_crop(value or option_id)
                if match is None:
                    return {"status": "invalid", "note": self._invalid_note("crop")}
                option_id, value, label = match["id"], match["name"], match["name"]
            else:
                accepted = self._validate(step_id, option_id, value)
                if accepted is None:
                    return {"status": "invalid", "note": self._invalid_note(step_id)}
                option_id, value, label = accepted

            note = await self._accept(step_id, option_id, value, label, via_voice=True)
            return {"status": "recorded", "note": note or "Qabul qilindi."}
        except Exception:  # noqa: BLE001 — select_option must NEVER raise
            logger.exception("select_option handling failed")
            await self._degrade()
            return {"status": "invalid", "note": "Xatolik yuz berdi, davom eting."}

    async def _handle_to_photo(self, args: dict) -> dict:
        try:
            if self.degraded or self.doc.finished or self.pending_step() != "symptom":
                return {"status": "invalid", "note": _SCRIPT_D_TP}
            summary = str(args.get("summary") or "").strip()
            note = await self._advance_from_symptom(summary, via_voice=True)
            return {"status": "recorded", "note": note}
        except Exception:  # noqa: BLE001 — to_photo must NEVER raise
            logger.exception("to_photo handling failed")
            await self._degrade()
            return {"status": "invalid", "note": "Xatolik yuz berdi, davom eting."}

    async def _handle_to_symptom(self, args: dict) -> dict:
        try:
            if self.degraded or self.doc.finished or self.pending_step() != "crop_context":
                return {"status": "invalid", "note": _SCRIPT_D_TS}
            note = await self._advance_from_crop_context(via_voice=True)
            return {"status": "recorded", "note": note}
        except Exception:  # noqa: BLE001 — to_symptom must NEVER raise
            logger.exception("to_symptom handling failed")
            await self._degrade()
            return {"status": "invalid", "note": "Xatolik yuz berdi, davom eting."}

    async def _handle_switch_to_diagnostic(self, args: dict) -> dict:
        try:
            if self.degraded or self.doc.finished or self.pending_step() != "general":
                return {"status": "invalid", "note": _SCRIPT_D_SW}
            note = await self._advance_switch_to_diagnostic(via_voice=True)
            return {"status": "recorded", "note": note}
        except Exception:  # noqa: BLE001 — switch_to_diagnostic must NEVER raise
            logger.exception("switch_to_diagnostic handling failed")
            await self._degrade()
            return {"status": "invalid", "note": "Xatolik yuz berdi, davom eting."}

    def _invalid_note(self, step_id: str) -> str:
        q = _PHOTO_SPOKEN_Q if step_id == "photo" else STEPS[step_id]["prompt"]
        note = _SCRIPT_D.format(step=step_id, q=q, opts=self._opts_str(step_id))
        if step_id == "crop":
            note += _SCRIPT_D_CROP_SUFFIX
        return note

    async def _match_crop(self, spoken: str) -> dict | None:
        """Fuzzy-match a spoken crop name against the cached Growz catalogue
        (contract §1.6)."""
        spoken = (spoken or "").strip()
        if not spoken:
            return None
        try:
            from app.voice.enrich import get_crops

            crops = await get_crops(self._s)
        except Exception:  # noqa: BLE001 — crop matching is best-effort
            logger.exception("crop catalogue fetch failed during select_option matching")
            return None
        return _find_crop_match(spoken, crops)

    # ---- validation for buttons/crop-picker/photo steps --------------------

    def _validate(self, step_id: str, option_id: str, value: str) -> tuple[str, str, str] | None:
        if step_id == "crop":
            option_id = (option_id or "").strip()
            # "open_crop_picker" / "saved_crops" are UI sentinels (open the
            # catalogue / expand the profile chips) — not crops. A client that
            # echoes one as the answer (seen from the WS tester, 2026-08-05)
            # would otherwise get it persisted as crop_id and the interview
            # would carry a bogus crop to diagnosis.
            if not option_id or option_id in ("open_crop_picker", "saved_crops"):
                return None
            label = (value or "").strip() or option_id
            return option_id, value, label
        if step_id == "photo":
            if option_id == "skip":
                return "skip", "", UZ["optSkipPhoto"]
            if option_id == "done_photos":
                return "done_photos", "", UZ["optDonePhotos"]
            return None
        label = _label_for(step_id, option_id)
        if label is None:
            return None
        return option_id, "", label

    # ---- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        """Called right after the Live session is up (contract §4.11 resume
        matrix)."""
        try:
            if self.doc.finished:
                await self._emit_state("consult")
                await self._speak_raw(_SCRIPT_F.format(title=self.doc.title))
                return
            d = self.doc
            if (
                d.query_type == "disease_pest" and d.crop_id
                and not d.symptom_done and not d.crop_context_done
                and d.crop_in_profile
            ):
                # Docs persisted before crop_in_profile existed default to
                # True; recompute from the profile so §1.3 isn't silently
                # skipped on resume. A profiled crop recomputes to the same
                # True. _profile_facts_for never raises (best-effort).
                d.crop_in_profile = bool(await self._profile_facts_for(d.crop_name))
                self._save()
            step_id = self.pending_step()
            if step_id is None:  # defensive — should be unreachable
                await self._finish()
                return
            phase = PHASE_FOR_STEP.get(step_id, "guide")
            await self._emit_state(phase)
            await self._emit_question(step_id)
            if step_id == "crop_context":
                await self._speak_raw(_SCRIPT_CC_A.format(body=_CROP_CONTEXT_BODY))
                return
            if step_id == "symptom":
                await self._speak_raw(_SCRIPT_SY_A.format(body=self._symptom_body()))
                return
            if step_id == "general":
                await self._speak_raw(_SCRIPT_G_A.format(title=self.doc.title))
                return
            q = _PHOTO_SPOKEN_Q if step_id == "photo" else STEPS[step_id]["prompt"]
            opts = self._opts_str(step_id)
            script = _SCRIPT_A.format(step=step_id, q=q, opts=opts)
            await self._speak_raw(script)
        except Exception:  # noqa: BLE001
            logger.exception("chat guide start failed")
            await self._degrade()

    async def on_answer(
        self, step_id: str, option_id: str, value: str,
        crop: dict | None = None,
    ) -> None:
        """A tapped ``chat.answer`` (contract §1.5). Stale/mismatched
        step_id or unknown option_id -> ignored silently. ``diag_offer`` is
        the one step id valid while pending() is a DIFFERENT id
        (``general`` — contract §4.4 f).

        For the crop step the client sends ``option_id`` as the BUTTON that was
        tapped (``open_crop_picker`` / a saved chip) and the picked crop as a
        separate ``crop: {id, name}`` object — so an option id that was never
        in the question's options list no longer doubles as a data carrier.
        """
        try:
            if step_id == "crop" and isinstance(crop, dict):
                option_id = str(crop.get("id") or "").strip()
                value = str(crop.get("name") or "").strip()
            if self.degraded or self.doc.finished:
                return
            pending = self.pending_step()
            if step_id == "diag_offer":
                if pending != "general":
                    return
                if option_id == "switch_diag":
                    await self._advance_switch_to_diagnostic(via_voice=False)
                elif option_id == "stay_general":
                    await self._decline_offer()
                return
            if pending is None or step_id != pending:
                return
            if step_id == "crop" and option_id == "saved_crops" and crop is None:
                # Server-driven «Ha»: expand the profile chips by re-sending
                # the SAME question with them. The step does not advance and
                # nothing is persisted — the real answer is still a crop.
                self._crop_saved_expanded = True
                await self._resend_question("crop")
                return
            if step_id == "crop_context":
                if option_id != "to_symptom":
                    return
                await self._advance_from_crop_context(via_voice=False)
                return
            if step_id == "symptom":
                if option_id != "to_photo":
                    return
                await self._advance_from_symptom("", via_voice=False)
                return
            accepted = self._validate(step_id, option_id, value)
            if accepted is None:
                return
            oid, val, label = accepted
            await self._accept(step_id, oid, val, label, via_voice=False)
        except Exception:  # noqa: BLE001
            logger.exception("chat guide on_answer failed")
            await self._degrade()

    async def on_photo_received(self, photo_id: str, photo_url: str = "") -> None:
        """A real photo advanced the photo step (contract §4.4). Called
        right after the existing ``session.on_photo`` handling; a no-op
        unless the guide is currently waiting on the photo step. ``photo_url``
        is the stored image URL (Spaces CDN / disk) attached to the photo
        message so the agronom admin UI can render the card from the stream."""
        try:
            if self.degraded or self.doc.finished:
                return
            if self.pending_step() != "photo":
                return
            self._photo_id = photo_id or ""
            self._store_append(
                role="farmer", kind="photo", text=UZ["photoMarker"], photo_url=photo_url,
            )
            await self._emit_step("photo", self._photo_id, "", UZ["photoBubble"])
            self.doc.photos_collected += 1
            self._save()
            cap = self._s.max_photos_per_session          # config.py:137, == 5
            if self.doc.photos_collected >= cap:
                await self._run_deterministic_finalize()
                await self._finish()
                return
            await self._resend_question("photo")           # re-shows the bar, template: on_camera_cancelled
            await self._speak_raw(
                "[TIZIM] Rasm qabul qilindi. Fermerga qisqa ayt: yana rasm yuborsin "
                "yoki «Tayyor» bossin."
            )
        except Exception:  # noqa: BLE001
            logger.exception("chat guide on_photo_received failed")
            await self._degrade()

    async def on_camera_cancelled(self) -> None:
        """``camera.cancelled`` during the photo step: re-emit the same
        question (buttons come back), nothing else (contract §4.4)."""
        try:
            if self.degraded or self.doc.finished:
                return
            if self.pending_step() != "photo":
                return
            await self._resend_question("photo")
        except Exception:  # noqa: BLE001
            logger.exception("chat guide on_camera_cancelled failed")
            await self._degrade()

    # ---- trigger detection (contract §4.10) --------------------------------

    def note_farmer_turn(self, text: str) -> None:
        """Sync hook fired for every recorded farmer turn (contract §4.7):
        captures the first general-phase question, and runs the trigger
        scan / symptom-turn backstop counter. Fail-open — never raises."""
        try:
            if self.degraded or self.doc.finished:
                return
            pending = self.pending_step()
            if pending == "general":
                if not self.doc.general_question:
                    self.doc.general_question = text[:300]
                    self.doc.title = derive_title(self.doc)
                    self._save()
                if not self._general_offered and has_trigger(text):
                    self._general_offered = True
                    asyncio.create_task(self._offer_diagnostic())
            elif pending == "crop_context":
                self._crop_context_turns += 1
                # BUG 2 fix: reveal the to_symptom button once, the moment
                # the counter reaches the threshold (checked BEFORE the
                # backstop below so both can fire on the same turn if the
                # thresholds ever collide).
                if (
                    self._crop_context_turns >= _CROP_CONTEXT_CHIP_AFTER
                    and not self._crop_context_chip_shown
                ):
                    self._crop_context_chip_shown = True
                    asyncio.create_task(self._crop_context_chip_reveal())
                if (
                    self._crop_context_turns >= _CROP_CONTEXT_TURN_CAP
                    and not self._crop_context_backstop_scheduled
                ):
                    self._crop_context_backstop_scheduled = True
                    asyncio.create_task(self._crop_context_backstop())
            elif pending == "symptom":
                self._symptom_turns += 1
                if (
                    self._symptom_turns >= _SYMPTOM_CHIP_AFTER
                    and not self._symptom_chip_shown
                ):
                    self._symptom_chip_shown = True
                    asyncio.create_task(self._symptom_chip_reveal())
                if (
                    self._symptom_turns >= _SYMPTOM_TURN_CAP
                    and not self._symptom_backstop_scheduled
                ):
                    self._symptom_backstop_scheduled = True
                    asyncio.create_task(self._symptom_backstop())
        except Exception:  # noqa: BLE001 — a hook must never break the recorder
            logger.exception("note_farmer_turn failed")

    async def _offer_diagnostic(self) -> None:
        """Server-detected trigger during the general phase (contract §4.4
        d): overlay the diag_offer question, at most once per session."""
        try:
            if self.degraded or self.doc.finished or self.pending_step() != "general":
                return
            await self._emit_question("diag_offer")
            await self._speak_raw(_SCRIPT_TRIG)
        except Exception:  # noqa: BLE001
            logger.exception("chat guide offer_diagnostic failed")
            await self._degrade()

    async def _crop_context_chip_reveal(self) -> None:
        """BUG 2 fix: farmer-turn count crossed _CROP_CONTEXT_CHIP_AFTER —
        re-emit the still-pending crop_context question once, now WITH the
        to_symptom button. Guarded (like the backstops) against a phase
        change that already happened while this task was scheduled."""
        try:
            if self.degraded or self.doc.finished or self.pending_step() != "crop_context":
                return
            # _resend_question, NOT _emit_question: the question is already in
            # the transcript (appended on first emit) — mirror on_camera_cancelled.
            await self._resend_question("crop_context")
        except Exception:  # noqa: BLE001
            logger.exception("chat guide crop_context chip reveal failed")
            await self._degrade()

    async def _symptom_chip_reveal(self) -> None:
        """BUG 2 fix: farmer-turn count crossed _SYMPTOM_CHIP_AFTER —
        re-emit the still-pending symptom question once, now WITH the
        to_photo button."""
        try:
            if self.degraded or self.doc.finished or self.pending_step() != "symptom":
                return
            # _resend_question, NOT _emit_question: no duplicate transcript row.
            await self._resend_question("symptom")
        except Exception:  # noqa: BLE001
            logger.exception("chat guide symptom chip reveal failed")
            await self._degrade()

    async def _symptom_backstop(self) -> None:
        """12-farmer-turn ceiling (contract §4.8): advance even if the model
        never calls to_photo and the farmer never taps."""
        try:
            if self.degraded or self.doc.finished or self.pending_step() != "symptom":
                return
            await self._advance_from_symptom("", via_voice=False, backstop=True)
        except Exception:  # noqa: BLE001
            logger.exception("chat guide symptom backstop failed")
            await self._degrade()

    async def _crop_context_backstop(self) -> None:
        """8-farmer-turn ceiling: advance to the plant_part step even if the
        model never calls to_symptom and the farmer never taps."""
        try:
            if self.degraded or self.doc.finished or self.pending_step() != "crop_context":
                return
            await self._advance_from_crop_context(via_voice=False, backstop=True)
        except Exception:  # noqa: BLE001
            logger.exception("chat guide crop_context backstop failed")
            await self._degrade()

    # ---- shared accept/advance path (contract §4.4) -------------------

    async def _accept(
        self, step_id: str, option_id: str, value: str, label: str, *, via_voice: bool
    ) -> str:
        """Persist + advance one accepted guide-step answer (query_type,
        crop, plant_part, photo). Returns the tool-ack ``note`` text for the
        voice path (empty string for the tap path, which speaks its
        transition directly)."""
        d = self.doc
        crop_context = ""
        if step_id == "query_type":
            d.query_type = option_id
        elif step_id == "crop":
            d.crop_id = option_id
            d.crop_name = label
            # Saved crop -> auto-load its profile into context. Unsaved crop ->
            # crop_in_profile=False persists on the doc, so pending_step()
            # routes into the crop_context phase (§1.3) where Rais asks the
            # profile questions (viloyat/tuman, ekish sanasi, faza, oxirgi
            # agrotexnik ishlar) — the strong lever the model actually follows.
            facts = await self._profile_facts_for(label)
            d.crop_in_profile = bool(facts)
            crop_context = _PROFILE_CONTEXT.format(facts=facts) if facts else ""
            if crop_context and not via_voice and self._inject_context is not None:
                # Tap path: a hidden context turn is safe (no tool call pending).
                # The voice path folds it into the tool-ack note below instead.
                await self._inject_context(crop_context)
        elif step_id == "plant_part":
            d.plant_part = option_id
        # photo: no persisted field — the message + finish() below is enough.
        self._store_append(role="farmer", kind="answer", text=label)
        await self._emit_step(step_id, option_id, value, label)

        old_phase = PHASE_FOR_STEP.get(step_id, "guide")
        # photo is definitionally the last step, but nothing on ChatDoc marks
        # it "done" the way query_type/crop_id/plant_part do — pending_step()
        # would keep re-deriving "photo" until finished=True, so accepting a
        # photo-step answer always finishes directly instead of re-deriving.
        # Photo-step answers (skip / done_photos) also fire the deterministic
        # diagnosis before finishing.
        next_step = None if step_id == "photo" else self.pending_step()
        if next_step is None:
            if step_id == "photo":          # skip (0 photos) OR done_photos (any count)
                await self._run_deterministic_finalize()
            await self._finish()
            return "Qabul qilindi. Yoʻriqli soʻrov yakunlandi."

        new_phase = PHASE_FOR_STEP.get(next_step, "guide")
        if new_phase != old_phase:
            await self._emit_state(new_phase)
        await self._emit_question(next_step)

        if next_step == "crop_context":
            body = _CROP_CONTEXT_BODY
            if via_voice:
                return _SCRIPT_CC_C.format(label=label, body=body)
            await self._speak_raw(_SCRIPT_CC_B.format(label=label, body=body))
            return ""
        if next_step == "symptom":
            body = self._symptom_body()
            if via_voice:
                return _with_context(
                    _SCRIPT_SY_C.format(label=label, body=body), crop_context
                )
            await self._speak_raw(_SCRIPT_SY_B.format(label=label, body=body))
            return ""
        if next_step == "general":
            if via_voice:
                return _SCRIPT_G_C.format(label=label)
            await self._speak_raw(_SCRIPT_G_B.format(label=label))
            return ""

        q = _PHOTO_SPOKEN_Q if next_step == "photo" else STEPS[next_step]["prompt"]
        opts = self._opts_str(next_step)
        if via_voice:
            return _with_context(
                _SCRIPT_C.format(label=label, next_step=next_step, q=q, opts=opts),
                crop_context,
            )
        await self._speak_raw(_SCRIPT_B.format(label=label, next_step=next_step, q=q, opts=opts))
        return ""

    async def _profile_facts_for(self, crop_name: str) -> str:
        """The compact saved-planting profile line for ``crop_name`` (or '').
        Committing the crop auto-pulls it so Rais knows it in-chat — pushed as a
        hidden turn on the tap path, folded into the tool-ack on the voice path.
        Best-effort; never raises.
        """
        if not crop_name:
            return ""
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            from app.voice.enrich.tenant_crops import (
                build_planting_profile,
                get_tenant_planting,
            )

            planting = await get_tenant_planting(self._s, crop_name)
            if not planting:
                return ""
            today = datetime.now(ZoneInfo("Asia/Tashkent")).date()
            return _profile_injection_text(build_planting_profile(planting, today))
        except Exception:  # noqa: BLE001
            logger.exception("profile facts lookup failed — continuing")
            return ""

    def _symptom_body(self) -> str:
        """The symptom-phase instruction (§2): the 7 Asosiy savollar plus the
        max-10 follow-up blocks."""
        return _SY_BODY

    async def _advance_from_crop_context(
        self, *, via_voice: bool, backstop: bool = False
    ) -> str:
        """Crop-context phase (§1.3) finished — via tap, the to_symptom tool,
        or the 8-turn backstop. Always advances into the plant_part step
        (the first question of the §2 dialogue)."""
        d = self.doc
        d.crop_context_done = True
        label = UZ["optToSymptom"]
        self._store_append(role="farmer", kind="answer", text=label)
        await self._emit_step("crop_context", "to_symptom", "", label)
        await self._emit_state(PHASE_FOR_STEP["plant_part"])
        await self._emit_question("plant_part")
        q = STEPS["plant_part"]["prompt"]
        opts = self._opts_str("plant_part")
        if backstop:
            await self._speak_raw(_SCRIPT_CC_CAP.format(q=q, opts=opts))
            return ""
        if via_voice:
            return _SCRIPT_C.format(label=label, next_step="plant_part", q=q, opts=opts)
        await self._speak_raw(
            _SCRIPT_B.format(label=label, next_step="plant_part", q=q, opts=opts)
        )
        return ""

    async def _advance_from_symptom(
        self, summary: str, *, via_voice: bool, backstop: bool = False
    ) -> str:
        """Symptom phase finished (contract §4.4 b) — via tap, the to_photo
        tool, or the 12-turn backstop. Always advances into the photo step."""
        d = self.doc
        d.symptom_done = True
        if summary:
            d.symptom_summary = summary[:300]
        label = UZ["optToPhoto"]
        self._store_append(role="farmer", kind="answer", text=label)
        await self._emit_step("symptom", "to_photo", d.symptom_summary, label)
        await self._emit_state("guide")
        await self._emit_question("photo")
        if backstop:
            await self._speak_raw(
                _SCRIPT_SY_CAP.format(q=_PHOTO_SPOKEN_Q, opts=self._opts_str("photo"))
            )
            return ""
        q = _PHOTO_SPOKEN_Q
        opts = self._opts_str("photo")
        if via_voice:
            return _SCRIPT_C.format(label=label, next_step="photo", q=q, opts=opts)
        await self._speak_raw(_SCRIPT_B.format(label=label, next_step="photo", q=q, opts=opts))
        return ""

    async def _advance_switch_to_diagnostic(self, *, via_voice: bool) -> str:
        """General -> diagnostic switch accepted (contract §4.4 e), by tap
        or the switch_to_diagnostic tool. Re-enters the diagnostic flow at
        the crop step (with memory chips)."""
        d = self.doc
        d.query_type = "disease_pest"
        label = UZ["optSwitchDiag"]
        self._store_append(role="farmer", kind="answer", text=label)
        await self._emit_step("diag_offer", "switch_diag", "", label)
        await self._emit_state("guide")
        await self._emit_question("crop")
        q = STEPS["crop"]["prompt"]
        opts = self._opts_str("crop")
        if via_voice:
            return _SCRIPT_SW_C.format(q=q, opts=opts)
        await self._speak_raw(_SCRIPT_SW_B.format(q=q, opts=opts))
        return ""

    async def _decline_offer(self) -> None:
        """Offer declined by tap (contract §4.4 f): stay in the general
        phase, re-send (not re-record) the general hint question."""
        self._store_append(role="farmer", kind="answer", text=UZ["optStayGeneral"])
        await self._emit_step("diag_offer", "stay_general", "", UZ["optStayGeneral"])
        await self._resend_question("general")
        await self._speak_raw(_SCRIPT_STAY)

    async def _run_deterministic_finalize(self) -> None:
        """Deterministic diagnosis trigger (done_photos / skip / 5-photo cap).
        Fail-open: a raising callback degrades, never crashes the socket."""
        if self._finalize_case is None:
            return
        d = self.doc
        summary = {
            "crop": d.crop_name,
            "symptoms": d.symptom_summary,
            "farmer_language": "uz",
        }
        if d.plant_part:
            summary["location_on_plant"] = _label_for("plant_part", d.plant_part) or d.plant_part
        await self._finalize_case(summary)

    async def _finish(self) -> None:
        self.doc.finished = True
        self._save()
        await self._emit_state("consult")
        await self._speak_raw(self._consult_kickoff_script())

    def _consult_kickoff_script(self) -> str:
        d = self.doc
        qt_label = _label_for("query_type", d.query_type) or d.query_type
        part_clause = ""
        if d.plant_part:
            part_label = _label_for("plant_part", d.plant_part) or d.plant_part
            part_clause = f"; qismi — {part_label}"
        symptom_clause = ""
        action_sentence = "muammo belgilarini batafsil soʻrab, tashxis jarayonini boshla."
        if d.symptom_done:
            symptom_text = d.symptom_summary or "suhbatda aytilgan"
            symptom_clause = f"; belgilar — {symptom_text}"
            action_sentence = (
                "yigʻilgan belgilar va rasm asosida tashxis jarayonini davom "
                "ettir; ishonch past boʻlsa yana 1-2 aniqlashtiruvchi savol "
                "ber."
            )
        photo_clause = "; rasm keldi" if self._photo_id else "; rasm yoʻq"
        return (
            "[TIZIM] Yoʻriqli soʻrov tugadi. Maʼlumotlar: ekin — "
            f"{d.crop_name}; muammo turi — {qt_label}{part_clause}"
            f"{symptom_clause}{photo_clause}. Endi erkin suhbat: "
            f"{action_sentence} Javobingni bitta qisqa savoldan boshla."
        )

    # ---- WS event emission ----------------------------------------------

    def _selections(self) -> dict:
        d = self.doc
        return {
            "query_type": d.query_type,
            "crop_id": d.crop_id,
            "crop_name": d.crop_name,
            "plant_part": d.plant_part,
            "photo_id": self._photo_id,
        }

    async def _emit_state(self, phase: str) -> None:
        await self._send_json(
            {
                "type": "chat.state",
                "chat_id": self.doc.id,
                "phase": phase,
                "selections": self._selections(),
            }
        )

    async def _resend_question(self, step_id: str) -> None:
        step = STEPS[step_id]
        if step_id == "crop":
            options = await self._crop_question_options()
        else:
            options = step["options"]
        # BUG 2 fix: hide the advance button on crop_context/symptom until
        # the farmer has answered enough in that phase (see
        # _CROP_CONTEXT_CHIP_AFTER / _SYMPTOM_CHIP_AFTER above) — the
        # underlying tap/tool route is still accepted early, only the visual
        # affordance is delayed.
        if step_id == "crop_context" and self._crop_context_turns < _CROP_CONTEXT_CHIP_AFTER:
            options = []
        elif step_id == "symptom" and self._symptom_turns < _SYMPTOM_CHIP_AFTER:
            options = []
        await self._send_json(
            {
                "type": "chat.question",
                "chat_id": self.doc.id,
                "step_id": step_id,
                "prompt": step["prompt"],
                "kind": step["kind"],
                "options": [{"id": oid, "label": label} for oid, label in options],
            }
        )

    async def _emit_question(self, step_id: str) -> None:
        await self._resend_question(step_id)
        self._store_append(role="rais", kind="question", text=STEPS[step_id]["prompt"])

    async def _emit_step(self, step_id: str, option_id: str, value: str, label: str) -> None:
        await self._send_json(
            {
                "type": "chat.step",
                "chat_id": self.doc.id,
                "step_id": step_id,
                "option_id": option_id,
                "value": value,
                "label": label,
            }
        )

    # ---- storage + degrade ------------------------------------------------

    def _save(self) -> None:
        try:
            self._store.save(self.doc)
        except Exception:  # noqa: BLE001
            logger.exception("chat guide save failed")

    def _store_append(
        self, *, role: str, kind: str, text: str, photo_url: str = "",
    ) -> None:
        try:
            self._store.append_message(self.doc, role, kind, text, photo_url=photo_url)
        except Exception:  # noqa: BLE001
            logger.exception("chat guide append_message failed")

    async def _degrade(self) -> None:
        """Any unexpected error -> degrade to a plain consult session
        (contract §8). Never re-raises."""
        self.degraded = True
        try:
            self.doc.finished = True
            self.doc.title = derive_title(self.doc)
            self._save()
        except Exception:  # noqa: BLE001
            logger.exception("chat guide degrade save failed")
        try:
            await self._emit_state("consult")
        except Exception:  # noqa: BLE001
            logger.exception("chat guide degrade emit failed")
