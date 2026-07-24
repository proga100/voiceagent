"""The farmer's registered Growz crops — for the «Qaysi ekin?» quick-pick chips.

These are the crops shown next to «Ekinlar» on the crop step: the farmer's REAL
Growz profile, not our conversation memory. The real source is
``GET /api/tenant/plantings`` (per-farmer tenant token) — the farmer's actual
plantings (field + crop + age + area + planting date, the records the "Ўсимлик
qo'shish" screen creates; ``/current`` filters to what's growing now). It is
currently behind the Growz tenant OTP-verify 500 blocker, so
``settings.tenant_crops_source`` selects where the list comes from:

  * ``"mock"`` — the packaged :data:`tenant_crops_mock.json` (dev default).
  * ``"api"``  — ``GET /api/tenant/plantings`` with the farmer's tenant token.
    Stub returning ``[]`` until the OTP verify is fixed and a token is threaded in.
  * ``"off"``  — no chips; the crop step shows only «Ekinlar».

Only the crop NAME is used downstream (the guide re-resolves it against the live
``/api/ai/crops`` catalogue to get the chip id), so the parser tolerates both a
flat ``cropName`` and a nested ``crop: {id, name}`` planting shape.

Fail-open law: any error returns ``[]`` — a missing profile list must NEVER break
the crop step (the farmer still gets «Ekinlar»).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_MOCK = Path(__file__).with_name("tenant_crops_mock.json")
_MAX_CROPS = 4  # the guide caps chips at 4 anyway; keep the payload small
# Uzbek-Latin language id in Growz translations — the script Rais speaks and the
# one /api/ai/crops names use, so chip names resolve against the catalogue.
_UZ_LATIN_LANG = "40d15260-93c9-4f92-a56a-c25a28e4b732"
_OKINA_RE = re.compile(r"[ʻ’']")


def _norm(s: str) -> str:
    """Okina-normalized lowercase, for matching a crop name to a planting."""
    return _OKINA_RE.sub("'", (s or "").strip().lower())


def _pick_field(translations: list, key: str) -> str:
    """The Uzbek-Latin value of ``key`` from a Growz ``translations`` array
    (else the first translation carrying that key)."""
    if not isinstance(translations, list):
        return ""
    named = [
        t for t in translations
        if isinstance(t, dict) and (t.get(key) or "").strip()
    ]
    for t in named:
        if t.get("language") == _UZ_LATIN_LANG:
            return t[key].strip()
    return named[0][key].strip() if named else ""


def _pick_name(translations: list) -> str:
    """Crop/district name — works for crop and district translations."""
    return _pick_field(translations, "name")


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[:n].rsplit(" ", 1)[0].rstrip(" ,.;") + "…"


def _crop_name_of(record: dict) -> str:
    """The crop name of one planting record (real nested shape or flat mock)."""
    crop = record.get("crop") if isinstance(record.get("crop"), dict) else {}
    translations = crop.get("translations")
    name = _pick_name(translations) if isinstance(translations, list) else ""
    return (
        name or record.get("cropName") or crop.get("name") or record.get("name") or ""
    ).strip()


def _extract(records: list[dict]) -> list[dict]:
    """Normalize raw planting records -> ``[{"id", "name"}]`` (name required).

    Handles the real ``GET /api/tenant/plantings`` shape (``crop.translations[]``
    for the name, ``crop.id`` for the id) and the simpler flat ``cropName``/
    ``crop.name`` mock; dedupes by name and caps at :data:`_MAX_CROPS`.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for r in records or []:
        if not isinstance(r, dict):
            continue
        crop = r.get("crop") if isinstance(r.get("crop"), dict) else {}
        name = _crop_name_of(r)
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"id": r.get("cropId") or crop.get("id") or "", "name": name})
        if len(out) >= _MAX_CROPS:
            break
    return out


def _as_records(raw) -> list[dict]:
    """Normalize any Growz plantings payload to a list of planting records:
    a ``{data: [...]}`` list response, a bare single-planting DETAIL object, or
    an already-unwrapped list."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, list):
            return data
        if raw.get("crop") or raw.get("cropName"):  # bare detail object
            return [raw]
    return []


def _load_records(settings) -> list[dict]:
    """Raw planting records from the configured source (``mock`` today; ``api``
    once the tenant token is available). ``[]`` on anything else."""
    source = getattr(settings, "tenant_crops_source", "off") or "off"
    if source == "mock":
        path = getattr(settings, "tenant_crops_mock_path", "") or ""
        p = Path(path) if path else _DEFAULT_MOCK
        return _as_records(json.loads(p.read_text(encoding="utf-8")))
    # TODO: source == "api" -> GET /api/tenant/plantings with the farmer's tenant
    # token, once the OTP-verify 500 is fixed (see growz-tenant-otp-blocker).
    return []


async def get_tenant_crops(settings, user_id: str | None = None) -> list[dict]:
    """The farmer's Growz crops as ``[{"id", "name"}]`` (max 4), or ``[]``.

    ``user_id`` is accepted for the future ``"api"`` path (token lookup); the
    mock ignores it.
    """
    try:
        return _extract(_load_records(settings))
    except Exception:  # noqa: BLE001 — chip source is best-effort, never fatal
        logger.warning("tenant crops failed — returning []", exc_info=True)
        return []


async def get_tenant_planting(
    settings, crop_name: str, user_id: str | None = None
) -> dict | None:
    """The full raw planting record whose crop matches ``crop_name``, or None.

    Used to auto-pull the farmer's profile (planting date, region, field, current
    task…) into the diagnosis/enrichment — «Profilda bo'lsa, Sistem avtomatik
    oladi». Fail-open: any error returns None (the call proceeds without it).
    """
    if not crop_name:
        return None
    try:
        target = _norm(crop_name)
        records = _load_records(settings)
        for r in records:
            if isinstance(r, dict) and _norm(_crop_name_of(r)) == target:
                return r
        # containment fallback (e.g. "Anor" vs "Anor (o'rtapishar)")
        for r in records:
            if not isinstance(r, dict):
                continue
            n = _norm(_crop_name_of(r))
            if n and (n in target or target in n):
                return r
        return None
    except Exception:  # noqa: BLE001 — best-effort, never fatal
        logger.warning("tenant planting lookup failed — None", exc_info=True)
        return None


def _days_since(iso_date: str, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(iso_date[:10])).days
    except Exception:  # noqa: BLE001
        return None


def build_planting_profile(planting: dict, today: date) -> dict:
    """A compact profile from a raw planting record, for the diagnosis + the
    session enrichment. Includes only what's really in ``/api/tenant/plantings``:
    crop, planting date, days-since-planting, growth period, region, field,
    current agrotech task, GPS. Missing fields are simply omitted.
    """
    if not isinstance(planting, dict):
        return {}
    crop = planting.get("crop") or {}
    field = planting.get("field") or {}
    farmer = planting.get("farmer") or {}
    profile: dict = {}

    name = _crop_name_of(planting)
    if name:
        profile["crop"] = name
    planted = planting.get("plantedDate") or ""
    if planted:
        profile["planted_date"] = planted[:10]
        days = _days_since(planted, today)
        if days is not None:
            profile["days_since_planting"] = days
    gf, gt = crop.get("growthPeriodFrom"), crop.get("growthPeriodTo")
    if gf and gt:
        profile["growth_period_days"] = f"{gf}-{gt}"
    # Region name only exists in the LIST shape (district.translations[].name);
    # the DETAIL shape carries region/district as id-only, so it's omitted (never
    # inject a raw UUID).
    region = _pick_name((field.get("district") or {}).get("translations") or [])
    if not region:
        region = _pick_name((field.get("region") or {}).get("translations") or [])
    if region:
        profile["region"] = region
    if field.get("name"):
        fld = {"name": field["name"]}
        area = planting.get("area") or field.get("area")
        if area:
            fld["area"] = area
        unit = (planting.get("plantingUnit") or {}).get("key")
        if unit:
            fld["unit"] = unit
        profile["field"] = fld
    # GPS only in the LIST shape (farmer.location); omitted from DETAIL.
    coords = (farmer.get("location") or {}).get("coordinates") or []
    if isinstance(coords, list) and len(coords) == 2:
        # Growz stores [lat, lon] (41.x, 69.x for Tashkent).
        profile["location"] = {"lat": coords[0], "lon": coords[1]}
    # Current agrotech task: the LIST shape has a short activityType.name; the
    # DETAIL shape only has the long activityTask.description — take a trimmed
    # version so the diagnosis still knows what was just done.
    task = planting.get("currentTask") or {}
    label = _pick_name((task.get("activityType") or {}).get("translations") or [])
    if not label:
        label = _truncate(
            _pick_field((task.get("activityTask") or {}).get("translations") or [],
                        "description"),
            200,
        )
    if label:
        profile["current_agrotech_task"] = label
    return profile
