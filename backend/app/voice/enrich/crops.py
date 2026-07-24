"""Growz crop catalogue — the list the farmer picks from before a call.

Read-only proxy over Growz ``GET /api/ai/crops`` (``x-api-key`` auth). The full
list (~134 crops, Uzbek names + real UUIDs) is a slow-moving reference table, so
it is fetched once and cached process-wide. Crucially the crop UUID returned here
is the SAME id the Growz mobile app uses, so a future ``/tenant/chats/start``
integration can pass the picked crop straight through.

Fail-open: any error (no key, network, bad shape) returns ``[]`` — the picker
shows nothing rather than crashing, and the voice agent never depends on it (the
chosen crop's *name* rides in ``session.start``, not resolved from this list).
"""
from __future__ import annotations

import logging

import httpx

from app.config import Settings

logger = logging.getLogger("voice.enrich.crops")

# Process-wide cache + keep-alive client (the catalogue rarely changes).
_cache: list[dict] | None = None
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            limits=httpx.Limits(max_keepalive_connections=2, keepalive_expiry=30),
        )
    return _client


async def get_crops(settings: Settings) -> list[dict]:
    """All Growz crops as ``[{id, name, biology_name, category}]`` (Uzbek names).

    Cached after the first success. Returns ``[]`` on any failure or missing key.
    """
    global _cache
    if _cache is not None:
        return _cache
    if not settings.growz_api_key:
        return []
    try:
        resp = await _http().get(
            f"{settings.growz_api_url}/api/ai/crops",
            params={"limit": 1000},
            headers={"x-api-key": settings.growz_api_key},
        )
        resp.raise_for_status()
        rows = resp.json().get("data", [])
    except Exception:  # noqa: BLE001 - fail-open: crops are optional
        logger.warning("growz crops fetch failed — returning empty list", exc_info=True)
        return []

    crops = [
        {
            "id": c.get("id", ""),
            "name": c.get("name", ""),
            "biology_name": c.get("biology_name", ""),
            "category": (c.get("crop_category") or {}).get("name", ""),
        }
        for c in rows
        if c.get("id") and c.get("name")
    ]
    crops.sort(key=lambda c: (c["category"], c["name"]))
    _cache = crops
    logger.info("growz crops cached: %d", len(crops))
    return crops


async def aclose() -> None:
    """Close the shared client + drop the cache (test/shutdown hygiene)."""
    global _client, _cache
    if _client is not None:
        await _client.aclose()
        _client = None
    _cache = None
