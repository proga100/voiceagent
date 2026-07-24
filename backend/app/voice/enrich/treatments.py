"""Growz Agroapteka preparations — fuzzy-matched by diagnosed disease/weed name.

Mirrors ``enrich/crops.py`` exactly: a cached module-level httpx client, a lazy
``_http()``, ``x-api-key`` auth, ``raise_for_status()``, and a fail-open
``except Exception -> []`` on any error. The catalogue fuzzy-match reuses the
okina-normalizing rule from ``chat/guide.py._find_crop_match`` — COPIED here
(not imported) because ``enrich`` must not depend on ``chat`` (wrong layering
direction).

Preparations are a SEPARATE, lower-trust signal from the Gemini diagnosis
verdict (different source, independently fail-open — contract addendum
P2.1/P2.2): this module NEVER raises. Any error (no key, network, bad shape,
no/ambiguous catalogue match, zero treatments) returns ``[]``.
"""
from __future__ import annotations

import logging
import re

import httpx

from app.config import Settings

logger = logging.getLogger("voice.enrich.treatments")

# Process-wide cache (per catalogue endpoint: "diseases" | "weeds") + a
# keep-alive client shared with the treatments fetch.
_cache: dict[str, list[dict]] = {}
_client: httpx.AsyncClient | None = None

PREP_CAP = 4            # max preparations returned / emitted
TREATMENTS_LIMIT = 25   # limit= param on /api/ai/treatments
CATALOGUE_LIMIT = 5000  # limit= param on /api/ai/diseases|weeds
DESC_MAX = 300          # description truncation (chars)

_OKINA_RE = re.compile(r"[ʻ’']")           # same class as guide.py:223
_PARENS_RE = re.compile(r"\(.*?\)")        # "Fitoftoroz (Kechki blight)" -> core


def _norm(s: str) -> str:
    return _OKINA_RE.sub("'", (s or "").strip().lower())


def _core(s: str) -> str:
    """Diagnosed names are verbose ("Fitoftoroz (Kechki blight)"); drop the
    parenthetical gloss so the core term ("fitoftoroz") can contain-match the
    crop-prefixed Growz name ("Pomidor fitoftorozi")."""
    return _norm(_PARENS_RE.sub("", s or ""))


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            limits=httpx.Limits(max_keepalive_connections=2, keepalive_expiry=30),
        )
    return _client


async def _get_catalogue(settings: Settings, kind: str) -> list[dict]:
    """Cached ``[{id, name, biology_name}]`` for the disease or weed catalogue.

    Frozen rule (P2.2): ``kind == "weed"`` -> ``/api/ai/weeds``; any other
    value -> ``/api/ai/diseases``. Returns ``[]`` on any failure or missing key.
    """
    endpoint = "weeds" if kind == "weed" else "diseases"
    if endpoint in _cache:
        return _cache[endpoint]
    if not settings.growz_api_key:
        return []
    try:
        resp = await _http().get(
            f"{settings.growz_api_url}/api/ai/{endpoint}",
            params={"limit": CATALOGUE_LIMIT},
            headers={"x-api-key": settings.growz_api_key},
        )
        resp.raise_for_status()
        rows = resp.json().get("data", [])
    except Exception:  # noqa: BLE001 - fail-open: preparations are optional
        logger.warning(
            "growz %s catalogue fetch failed — returning empty list",
            endpoint, exc_info=True,
        )
        return []

    catalogue = [
        {
            "id": row.get("id", ""),
            "name": row.get("name", ""),
            "biology_name": row.get("biology_name", ""),
        }
        for row in rows
        if row.get("id") and row.get("name")
    ]
    _cache[endpoint] = catalogue
    logger.info("growz %s catalogue cached: %d", endpoint, len(catalogue))
    return catalogue


def _match_disease(
    disease_name: str, catalogue: list[dict], crop_name: str = ""
) -> dict | None:
    """Exact ``name``/``biology_name`` match first (Gemini sometimes answers
    with the Latin binomial); else the single candidate that contains (or is
    contained by) the diagnosed name (the §1.6 single-candidate rule).

    When the bare diagnosed name is ambiguous (Growz stores CROP-PREFIXED
    names like "Pomidor fitoftorozi" / "Kartoshka fitoftorozi", so "fitoftoroz"
    alone hits several), the known ``crop_name`` disambiguates: keep the
    candidates whose name also contains the crop and accept if that leaves
    exactly one. Still ``None`` when genuinely ambiguous — better empty than
    the wrong preparations."""
    norm_full = _norm(disease_name)
    if not norm_full or not catalogue:
        return None
    # Exact match on the FULL diagnosed name (Gemini sometimes answers with the
    # catalogue name verbatim or the Latin binomial).
    for row in catalogue:
        if (
            _norm(row.get("name", "")) == norm_full
            or _norm(row.get("biology_name", "")) == norm_full
        ):
            return row
    # Containment on the CORE term (parenthetical gloss stripped).
    norm_target = _core(disease_name) or norm_full
    candidates = [
        row for row in catalogue
        if norm_target in _norm(row.get("name", ""))
        or _norm(row.get("name", "")) in norm_target
    ]
    if len(candidates) == 1:
        return candidates[0]
    # Ambiguous core term (Growz stores crop-prefixed names, so "fitoftoroz"
    # hits several) -> the known crop narrows it; accept only when exactly one
    # remains (still None when genuinely ambiguous — safe over wrong).
    norm_crop = _norm(crop_name)
    if len(candidates) > 1 and norm_crop:
        narrowed = [
            row for row in candidates if norm_crop in _norm(row.get("name", ""))
        ]
        if len(narrowed) == 1:
            return narrowed[0]
    return None


def _coerce_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_preparation(row: dict) -> dict | None:
    """Map one Growz treatment row to the frozen P2.1 Preparation dict, or
    ``None`` when the row has no usable drug name (dropped before de-dup)."""
    drug = row.get("drug") or {}
    name = (drug.get("name") or "").strip()
    if not name:
        return None
    dose_min = row.get("dose_min")
    if dose_min is None:
        dose_min = drug.get("dose_min")
    dose_max = row.get("dose_max")
    if dose_max is None:
        dose_max = drug.get("dose_max")
    unit = (drug.get("unit") or {}).get("name") or ""
    description = (drug.get("description") or "").strip()[:DESC_MAX]
    return {
        "name": name,
        "dose_min": _coerce_float(dose_min),
        "dose_max": _coerce_float(dose_max),
        "unit": unit,
        "type": (row.get("type") or "").lower(),
        "description": description,
    }


def _dedup_cap(rows: list[dict]) -> list[dict]:
    """Treatment rows -> frozen Preparation dicts: drop nameless, de-dupe by
    normalized drug name, cap at PREP_CAP (Growz row order preserved)."""
    preparations: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        prep = _build_preparation(row)
        if prep is None:
            continue
        key = _norm(prep["name"])
        if key in seen:
            continue
        seen.add(key)
        preparations.append(prep)
        if len(preparations) >= PREP_CAP:
            break
    return preparations


async def find_preparations_by_id(settings: Settings, disease_id: str) -> list[dict]:
    """Preparations for an EXACT Growz disease id — the ROBUST path. The
    diagnosis model picks the id from the crop's candidate list
    (:func:`get_crop_diseases`), so there is no name guessing. NEVER raises;
    ``[]`` on any failure or missing id/key."""
    disease_id = (disease_id or "").strip()
    if not disease_id or not settings.growz_api_key:
        return []
    try:
        resp = await _http().get(
            f"{settings.growz_api_url}/api/ai/treatments",
            params={"disease_id": disease_id, "limit": TREATMENTS_LIMIT},
            headers={"x-api-key": settings.growz_api_key},
        )
        resp.raise_for_status()
        rows = resp.json().get("data", [])
    except Exception:  # noqa: BLE001 - fail-open: preparations are optional
        logger.warning(
            "growz treatments fetch failed for id %r — []", disease_id, exc_info=True
        )
        return []
    return _dedup_cap(rows)


async def get_crop_diseases(
    settings: Settings, crop_name: str, kind: str = "disease_pest",
) -> list[dict]:
    """The crop's Growz diseases as ``[{id, name}]`` candidates for the
    diagnosis model. Growz has no crop filter but names are crop-prefixed, so we
    filter the cached catalogue by the crop name (e.g. Pomidor -> ~43). ``[]`` on
    any failure or empty crop."""
    norm_crop = _norm(crop_name)
    if not norm_crop:
        return []
    catalogue = await _get_catalogue(settings, kind)
    return [
        {"id": r["id"], "name": r["name"]}
        for r in catalogue
        if norm_crop in _norm(r.get("name", ""))
    ]


async def find_preparations(
    settings: Settings, disease_name: str, kind: str = "disease_pest",
    crop_name: str = "",
) -> list[dict]:
    """Fuzzy-name FALLBACK path (used when the diagnosis model returned no Growz
    disease id). ``crop_name`` disambiguates crop-prefixed names. NEVER raises —
    ``[]`` on any failure or no match."""
    try:
        catalogue = await _get_catalogue(settings, kind)
        match = _match_disease(disease_name, catalogue, crop_name)
    except Exception:  # noqa: BLE001 - fail-open
        logger.warning("disease catalogue match failed — []", exc_info=True)
        return []
    if match is None:
        logger.info("no catalogue match for diagnosed name %r", disease_name)
        return []
    return await find_preparations_by_id(settings, match["id"])


async def aclose() -> None:
    """Close the shared client + drop the caches (test/shutdown hygiene)."""
    global _client, _cache
    if _client is not None:
        await _client.aclose()
        _client = None
    _cache = {}
