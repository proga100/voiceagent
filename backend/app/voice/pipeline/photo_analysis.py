"""Per-selected-photo specialist triage — §5.4 of diagnostic_flow.md.

For each photo picked by ``photo_select.select_best_photos`` for the final
diagnosis, one flash-tier call (mirrors ``photo_select.py``'s genai style)
produces a structured pre-read: symptoms seen, organ, disease/pest
hypotheses, nutrient/agrotech-stress suspicion and a confidence label. The
Pro diagnosis call later receives these as advisory evidence (never ground
truth — it still looks at the images itself).

``analyze_selected_photos`` NEVER raises (``asyncio.CancelledError``
excepted): any per-photo failure/timeout degrades that slot to ``None``;
others still succeed. ``[]`` in -> ``[]`` out.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel

from app.config import Settings
from app.voice.pipeline.tools import PhotoAttachment
from app.voice.providers.google_auth import GoogleAuth

logger = logging.getLogger("voice.pipeline.photo_analysis")

PER_IMAGE_TIMEOUT_S = 20.0


class PerImageAnalysis(BaseModel):
    symptoms_seen: list[str]
    organ: Literal[
        "leaf", "stem", "fruit", "flower", "root",
        "branch", "bark", "whole_plant", "soil", "unknown",
    ]
    likely_disease_hypotheses: list[str]
    likely_pest_hypotheses: list[str]
    nutrient_deficiency_suspected: bool
    agrotech_stress_suspected: bool
    confidence: Literal["high", "medium", "low"]


_PER_IMAGE_SYSTEM_PROMPT = (
    "You are a plant pathology specialist doing a quick per-photo triage for "
    "a single farmer photo. Look ONLY at the attached image and answer: "
    "which symptoms are visible (short phrases), which plant organ dominates "
    "the frame, plausible disease and pest hypotheses given what you see "
    "(name(s), can be empty lists), whether a nutrient deficiency is "
    "suspected, whether agrotechnical stress (e.g. over/under-watering, "
    "fertilizer burn, sun scorch) is suspected, and your overall confidence "
    "in this read. This is a fast pre-read for a senior diagnosis model — be "
    "concise and only report what is actually visible."
)


async def _analyze_one(
    settings: Settings, auth: GoogleAuth, photo: PhotoAttachment,
) -> PerImageAnalysis:
    """One flash triage call for a single photo. Raises on any failure — the
    caller (``analyze_selected_photos``) is responsible for catching."""
    from google.genai import types

    client = auth.genai_client()
    response = await asyncio.wait_for(
        client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=[types.Content(role="user", parts=[
                types.Part(inline_data=types.Blob(data=photo.data, mime_type=photo.mime)),
            ])],
            config=types.GenerateContentConfig(
                system_instruction=_PER_IMAGE_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=PerImageAnalysis,
                temperature=0.0,
                max_output_tokens=1024,
            ),
        ),
        PER_IMAGE_TIMEOUT_S,
    )
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, PerImageAnalysis):
        return parsed
    return PerImageAnalysis.model_validate_json(response.text)


async def analyze_selected_photos(
    settings: Settings, auth: GoogleAuth, photos: list[PhotoAttachment],
) -> list[PerImageAnalysis | None]:
    """Analyze each of ``photos`` in parallel; same length/order as input.
    NEVER raises (``asyncio.CancelledError`` excepted) — a per-photo
    error/timeout degrades that slot to ``None``, others still succeed."""
    if not photos:
        return []
    results = await asyncio.gather(
        *(_analyze_one(settings, auth, p) for p in photos),
        return_exceptions=True,
    )
    out: list[PerImageAnalysis | None] = []
    for photo, result in zip(photos, results):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException):
            logger.warning(
                "per-image analysis failed for %s", photo.photo_id, exc_info=result
            )
            out.append(None)
        else:
            out.append(result)
    return out
