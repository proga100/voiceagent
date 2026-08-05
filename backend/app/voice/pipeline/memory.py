"""Per-farmer memory — Alomat remembers every user across sessions.

The app sends a stable ``user_id`` (a device UUID) in ``chat.start``. This
module owns everything that turns that id into the "she knows me" feeling:

  * ``MemoryStore``   — JSON file per profile (``data/memory/profiles/``) plus a
                        device index (``data/memory/devices/<device_id>`` → key).
                        Keys: ``phone:998XXXXXXXXX`` once the number is known,
                        else ``dev:<device_id>``. Learning a confirmed phone
                        re-keys/merges the profile so memory survives reinstall.
  * ``build_memory_context`` — the per-session system-prompt block + the spoken
                        kickoff: onboarding for a brand-new farmer, or a
                        greeting tiered by how long ago the last session was
                        (computed in code — the LLM never does time math).
  * ``finalize_session_memory`` — after the socket closes: bump deterministic
                        fields first (session count / last_seen / diagnosis),
                        then one flash structured-output call extracts new facts
                        from the transcript. Fail-open: any error or timeout
                        leaves a valid profile and never breaks teardown.

Storage is deliberately primitive (files + tmp/rename + per-key asyncio locks):
prod runs a single uvicorn worker and one farmer talks on one socket at a time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.config import Settings
from app.voice.providers.google_auth import GoogleAuth

logger = logging.getLogger("voice.memory")

_MEMORY_TIMEOUT_S = 20.0

# Growth caps — memory must stay a compact prompt block, not a junk drawer.
_MAX_CROPS = 15
_MAX_CROPS_PER_SESSION = 5
_MAX_CROP_CHARS = 40
_MAX_NOTES = 10
_MAX_NOTES_PER_SESSION = 2
_MAX_NOTE_CHARS = 120
_MAX_NAME_CHARS = 60
_MAX_REGION_CHARS = 60
# Below this the call was too trivial to be worth an extraction LLM call.
_MIN_TRANSCRIPT_CHARS = 80
_MIN_FARMER_TURNS = 2

_DEVICE_ID_RE = re.compile(r"[A-Za-z0-9-]{8,64}")

# One lock per profile key; single-process store (prod = one uvicorn worker).
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(key: str) -> asyncio.Lock:
    return _locks.setdefault(key, asyncio.Lock())


class OpenIssue(BaseModel):
    problem: str = ""
    diagnosis: str = ""
    status: Literal["ochiq", "davolanmoqda", "hal_bolgan"] = "ochiq"
    date: str = ""  # ISO date


class FarmerProfile(BaseModel):
    name: str = ""
    phone: str = ""   # normalized 998XXXXXXXXX
    region: str = ""
    crops: list[str] = []
    notes: list[str] = []
    open_issue: OpenIssue | None = None
    sessions_count: int = 0
    last_seen: str = ""  # ISO UTC datetime


class ProfileUpdate(BaseModel):
    """Structured output of the post-session extraction call (flat on purpose)."""

    name: str | None = None
    #: Digits only, and ONLY if the farmer confirmed the repeated-back number.
    phone: str | None = None
    region: str | None = None
    crops_add: list[str] = []
    open_issue_problem: str | None = None
    open_issue_resolved: bool = False
    notes_add: list[str] = []


def valid_device_id(device_id: object) -> bool:
    """The id becomes a filename — reject anything but a plain UUID-ish token."""
    return isinstance(device_id, str) and _DEVICE_ID_RE.fullmatch(device_id) is not None


def normalize_phone(raw: str | None) -> str | None:
    """Uzbek numbers only: 9 local digits → prefix 998; 998+9 digits → keep."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 9:
        return "998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        return digits
    return None


class MemoryStore:
    """File-backed profile store. All paths live under ``settings.memory_dir``."""

    def __init__(self, settings: Settings) -> None:
        root = Path(settings.memory_dir)
        self._profiles = root / "profiles"
        self._devices = root / "devices"
        for p in (self._profiles, self._devices):
            p.mkdir(parents=True, exist_ok=True)

    def load_for_device(self, device_id: str) -> tuple[str, FarmerProfile | None]:
        """Resolve a device to ``(profile_key, profile|None)``. None = new farmer."""
        idx = self._devices / device_id
        if idx.exists():
            key = idx.read_text(encoding="utf-8").strip()
            if key:
                profile = self.read(key)
                if profile is not None:
                    return key, profile
        key = f"dev:{device_id}"
        return key, self.read(key)

    def read(self, key: str) -> FarmerProfile | None:
        """Read one profile; corrupt JSON is preserved as ``.bad`` and treated
        as a new farmer (the session must keep working)."""
        path = self._profiles / f"{key}.json"
        if not path.exists():
            return None
        try:
            return FarmerProfile.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception:  # noqa: BLE001 — any parse problem → start fresh
            logger.warning("corrupt profile %s — sidelined as .bad", path.name)
            try:
                path.replace(path.with_suffix(".json.bad"))
            except OSError:
                pass
            return None

    def save(self, key: str, profile: FarmerProfile) -> None:
        path = self._profiles / f"{key}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            profile.model_dump_json(indent=2), encoding="utf-8"
        )
        tmp.replace(path)  # atomic on POSIX

    def write_index(self, device_id: str, key: str) -> None:
        idx = self._devices / device_id
        tmp = self._devices / (device_id + ".tmp")
        tmp.write_text(key, encoding="utf-8")
        tmp.replace(idx)

    def link_phone(
        self, device_id: str, old_key: str, profile: FarmerProfile
    ) -> tuple[str, FarmerProfile]:
        """Re-key ``profile`` under its (validated) phone number.

        If a profile already exists for that phone (app reinstall — the whole
        point of the phone key), merge INTO it with existing-wins on identity
        scalars, so a misheard-but-confirmed number can never overwrite a
        stranger's name. Returns ``(new_key, profile)``.
        """
        new_key = f"phone:{profile.phone}"
        if new_key == old_key:
            self.write_index(device_id, new_key)
            return new_key, profile
        existing = self.read(new_key)
        if existing is not None:
            profile = _merge_profiles(existing, profile)
        self.save(new_key, profile)
        self.write_index(device_id, new_key)
        if old_key.startswith("dev:"):
            (self._profiles / f"{old_key}.json").unlink(missing_ok=True)
        return new_key, profile


def _merge_profiles(existing: FarmerProfile, incoming: FarmerProfile) -> FarmerProfile:
    """Reinstall merge: identity scalars keep the EXISTING value when set."""
    issue = existing.open_issue
    if incoming.open_issue is not None and (
        issue is None or (incoming.open_issue.date or "") >= (issue.date or "")
    ):
        issue = incoming.open_issue
    crops = list(existing.crops)
    crops += [c for c in incoming.crops if c not in crops]
    return FarmerProfile(
        name=existing.name or incoming.name,
        phone=existing.phone or incoming.phone,
        region=existing.region or incoming.region,
        crops=crops[:_MAX_CROPS],
        notes=(existing.notes + incoming.notes)[-_MAX_NOTES:],
        open_issue=issue,
        sessions_count=existing.sessions_count + incoming.sessions_count,
        last_seen=max(existing.last_seen, incoming.last_seen),
    )


# ---- Prompt context -------------------------------------------------------

_ONBOARDING_BLOCK = (
    "[YANGI FERMER] Bu foydalanuvchi bilan BIRINCHI suhbat. Suhbat davomida "
    "tabiiy ravishda, har safar faqat bitta savol bilan bilib ol: fermerning "
    "ISMINI; QAYERDAN ekanini (viloyat/tuman); NIMA yetishtirishini (ekinlar, "
    "daraxtlar, issiqxona). Bu soʻroq emas — muammosini hal qilish asosiy "
    "maqsad, tanishuv suhbatga singib ketsin. Ismini bilgach, unga ism bilan "
    "murojaat qil. Suhbat oxiriga yaqin telefon raqamini soʻra — "
    "«maslahatlarimni eslab qolishim uchun» — va raqamni OʻZING QAYTARIB AYTIB "
    "«toʻgʻrimi?» deb tasdiqlat. Fermer raqam berishni istamasa, boshqa "
    "soʻrama."
)

_ONBOARDING_KICKOFF = (
    "Suhbatni sen boshla: qisqa salomlash, oʻzingni tanishtir — «Men Rais — "
    "Growz AI kompaniyasining sunʼiy intellekt agronomiman» — va fermerdan "
    "ismini soʻra. Koʻpi bilan 2 qisqa jumla."
)


def _parse_last_seen(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def build_memory_context(
    profile: FarmerProfile | None, now: datetime | None = None
) -> tuple[str, str]:
    """Return ``(system_prompt_block, spoken_kickoff)`` for this session."""
    if profile is None:
        return _ONBOARDING_BLOCK, _ONBOARDING_KICKOFF
    now = now or datetime.now(timezone.utc)

    facts = ["[FERMER PROFILI] Bu fermer bilan avval gaplashgansan."]
    if profile.name:
        facts.append(f"Ismi: {profile.name}.")
    if profile.region:
        facts.append(f"Hududi: {profile.region}.")
    if profile.crops:
        facts.append("Yetishtiradi: " + ", ".join(profile.crops[:8]) + ".")
    issue = profile.open_issue
    if issue is not None and (issue.problem or issue.diagnosis):
        status_uz = {
            "ochiq": "ochiq", "davolanmoqda": "davolanmoqda",
            "hal_bolgan": "hal boʻlgan",
        }[issue.status]
        facts.append(
            f"Oxirgi muammo: {issue.problem or issue.diagnosis}"
            + (f" (tashxis: {issue.diagnosis})" if issue.diagnosis else "")
            + f" — holati: {status_uz}"
            + (f", {issue.date}" if issue.date else "") + "."
        )
    for note in profile.notes[-5:]:
        facts.append(f"Eslatma: {note}")
    facts.append(
        "Bu maʼlumotlardan tabiiy foydalan — fermer sen uni eslab qolganingni "
        "his qilsin. Bilmagan faktlaringni mos payt kelganda bitta savol bilan "
        "soʻrab ol."
    )
    if profile.sessions_count <= 3 and not profile.phone:
        facts.append(
            "Telefon raqami hali yoʻq: suhbat oxiriga yaqin BIR MARTA soʻra "
            "(«maslahatlarimni eslab qolishim uchun») va raqamni qaytarib "
            "aytib tasdiqlat. Rad etsa, qaytarma."
        )
    block = " ".join(facts)

    name_part = f" {profile.name}" if profile.name else ""
    follow = ""
    if issue is not None and issue.status != "hal_bolgan" and (
        issue.problem or issue.diagnosis
    ):
        follow = (
            f" Oxirgi muammosi ({issue.problem or issue.diagnosis}) hozir "
            "qanday ekanini soʻra."
        )
    elif profile.crops:
        follow = f" {profile.crops[0].capitalize()} qalayligini soʻra."

    last = _parse_last_seen(profile.last_seen)
    gap = (now - last) if last is not None else None
    if gap is not None and gap < timedelta(minutes=30):
        kickoff = (
            f"Fermer qayta ulandi — bir necha daqiqa oldin gaplashgan "
            f"edinglar. Juda qisqa javob ber: «Eshitaman{name_part}» kabi, va "
            "kut. Oʻzingni QAYTA tanishtirma, hol-ahvol soʻrama."
        )
    elif gap is not None and gap < timedelta(days=7):
        kickoff = (
            f"Fermerni ismi bilan iliq salomlash{name_part and f' («Assalomu alaykum,{name_part} aka!» kabi)' or ''}."
            + follow + " 1-2 qisqa jumla. Oʻzingni qayta tanishtirma."
        )
    else:
        kickoff = (
            f"Fermerni iliq salomlash{name_part and f', ismi bilan ({profile.name})' or ''} — "
            "anchadan beri gaplashmagansizlar, shuni iliq ohangda bildir."
            + follow + " 1-2 qisqa jumla. Oʻzingni qayta tanishtirma."
        )
    return block, kickoff


# ---- Post-session extraction ----------------------------------------------

_EXTRACT_SYSTEM_PROMPT = (
    "You maintain a farmer's profile for an Uzbek voice AI agronomist. Input: "
    "a call transcript with FERMER: (the farmer) and RAIS: (the assistant) "
    "lines. Extract ONLY facts the farmer himself stated in FERMER lines — "
    "RAIS lines are context only. All text values in Uzbek.\n"
    "- name: the farmer's own name if he said it, else null.\n"
    "- phone: ONLY if RAIS repeated the number back and the farmer "
    "explicitly confirmed it; digits only (e.g. 998901234567), else null.\n"
    "- region: the farmer's province/district if mentioned, else null.\n"
    "- crops_add: NEW crops/trees/plants the farmer grows (short names, "
    "max 5). Not the diseases — the plants.\n"
    "- open_issue_problem: the plant problem discussed in THIS call, one "
    "short sentence, else null.\n"
    "- open_issue_resolved: true only if the farmer said an earlier problem "
    "is now fixed.\n"
    "- notes_add: up to 2 short facts worth remembering next call (irrigation "
    "habits, greenhouse, field size...), else [].\n"
    "Never guess or invent. Prefer null/[] when unsure."
)


def _transcript_worth_extracting(transcript: str) -> bool:
    if len(transcript) < _MIN_TRANSCRIPT_CHARS:
        return False
    return transcript.count("FERMER:") >= _MIN_FARMER_TURNS


def _clip_list(items: list[str], per_session: int, max_chars: int) -> list[str]:
    out = []
    for item in items[:per_session]:
        item = str(item).strip()[:max_chars]
        if item:
            out.append(item)
    return out


def _apply_update(
    profile: FarmerProfile, upd: ProfileUpdate, today: str
) -> None:
    """Merge one session's extracted facts into ``profile`` (same farmer —
    new statements win over old values; caps enforced here, not trusted from
    the model)."""
    if upd.name and upd.name.strip():
        profile.name = upd.name.strip()[:_MAX_NAME_CHARS]
    if upd.region and upd.region.strip():
        profile.region = upd.region.strip()[:_MAX_REGION_CHARS]
    for crop in _clip_list(upd.crops_add, _MAX_CROPS_PER_SESSION, _MAX_CROP_CHARS):
        low = crop.lower()
        if low not in (c.lower() for c in profile.crops):
            profile.crops.append(low)
    profile.crops = profile.crops[:_MAX_CROPS]
    profile.notes = (
        profile.notes
        + _clip_list(upd.notes_add, _MAX_NOTES_PER_SESSION, _MAX_NOTE_CHARS)
    )[-_MAX_NOTES:]
    if upd.open_issue_problem and upd.open_issue_problem.strip():
        problem = upd.open_issue_problem.strip()[:_MAX_NOTE_CHARS]
        if profile.open_issue is None:
            profile.open_issue = OpenIssue(problem=problem, date=today)
        else:
            profile.open_issue.problem = problem
            profile.open_issue.date = today
    if upd.open_issue_resolved and profile.open_issue is not None:
        profile.open_issue.status = "hal_bolgan"


async def _extract(
    settings: Settings, auth: GoogleAuth, transcript: str
) -> ProfileUpdate | None:
    from google.genai import types

    client = auth.genai_client()
    response = await client.aio.models.generate_content(
        model=settings.gemini_model,
        contents=[types.Content(role="user", parts=[types.Part(text=transcript)])],
        config=types.GenerateContentConfig(
            system_instruction=_EXTRACT_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=ProfileUpdate,
            temperature=0.0,
            max_output_tokens=2048,
        ),
    )
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, ProfileUpdate):
        return parsed
    return ProfileUpdate.model_validate_json(response.text)


def _save_transcript(
    settings: Settings, key: str, transcript: str, now: datetime
) -> None:
    """Append the raw conversation to a per-farmer log for quality review.

    One file per farmer per day (``transcripts/<key>/<YYYY-MM-DD>.txt``),
    sessions separated by a timestamp header. Written BEFORE extraction so the
    conversation survives any LLM failure. Best-effort: never raises.
    """
    if not transcript.strip():
        return
    try:
        day_dir = Path(settings.memory_dir) / "transcripts" / key
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{now.date().isoformat()}.txt"
        header = f"=== session {now.isoformat(timespec='seconds')} ===\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(header + transcript.rstrip() + "\n\n")
    except Exception:  # noqa: BLE001 — review logs must never break teardown
        logger.exception("transcript save failed")


async def finalize_session_memory(
    settings: Settings,
    auth: GoogleAuth,
    store: MemoryStore,
    device_id: str,
    key: str,
    profile: FarmerProfile | None,
    transcript: str,
    diagnosis: dict | None,
) -> None:
    """Persist everything this session taught us. Never raises.

    Deterministic facts (session count, last_seen, diagnosis→open_issue) are
    applied and survive even when the extraction call fails or times out.
    """
    try:
        profile = profile or FarmerProfile()
        now = datetime.now(timezone.utc)
        today = date.today().isoformat()
        _save_transcript(settings, key, transcript, now)
        profile.sessions_count += 1
        profile.last_seen = now.isoformat(timespec="seconds")
        if diagnosis and diagnosis.get("disease"):
            profile.open_issue = OpenIssue(
                problem=(
                    profile.open_issue.problem
                    if profile.open_issue is not None and profile.open_issue.problem
                    else diagnosis["disease"]
                ),
                diagnosis=diagnosis["disease"],
                status="davolanmoqda",
                date=diagnosis.get("date") or today,
            )

        upd: ProfileUpdate | None = None
        if _transcript_worth_extracting(transcript):
            try:
                upd = await asyncio.wait_for(
                    _extract(settings, auth, transcript), _MEMORY_TIMEOUT_S
                )
            except Exception:  # noqa: BLE001 — extraction is best-effort
                logger.exception("memory extraction failed — keeping deterministic update")
        if upd is not None:
            _apply_update(profile, upd, today)

        async with _lock_for(key):
            phone = normalize_phone(upd.phone) if upd is not None else None
            if phone:
                profile.phone = phone
                key, profile = store.link_phone(device_id, key, profile)
            else:
                store.save(key, profile)
                store.write_index(device_id, key)
        logger.info(
            "memory saved: key=%s sessions=%d name=%r",
            key, profile.sessions_count, profile.name,
        )
    except Exception:  # noqa: BLE001 — memory must never break teardown
        logger.exception("memory update failed")
