"""Conversation enrichment: give Rais today's date, the local weather, and the
crop the farmer picked — before the call opens.

Three small, independent, fail-open pieces:
  * :mod:`crops`   — proxy the Growz crop catalogue for the picker (cached).
  * :mod:`weather` — current weather from Open-Meteo (no API key).
  * :mod:`context` — compose the Uzbek ``[BUGUNGI SHAROIT]`` prompt block.

Nothing here may ever break a session: every entry point returns an empty result
on failure and the conversation simply carries one fewer line.
"""
from __future__ import annotations

from app.voice.enrich.context import build_session_enrichment
from app.voice.enrich.crops import get_crops
from app.voice.enrich.tenant_crops import (
    build_planting_profile,
    get_tenant_crops,
    get_tenant_planting,
)

__all__ = [
    "build_session_enrichment",
    "build_planting_profile",
    "get_crops",
    "get_tenant_crops",
    "get_tenant_planting",
]
