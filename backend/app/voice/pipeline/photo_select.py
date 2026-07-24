"""Photo ranking before diagnosis — pick the <=3 most diagnostic photos.

When the farmer sent more than ``max_n`` photos, a cheap flash call (mirrors
``pipeline/diagnosis.py``'s genai style) picks the best ones by symptom
clarity, focus, damaged-organ visibility and non-duplicate angles, before the
expensive Pro diagnosis call. Invisible on the wire — pure cost/quality
optimisation (contract addendum P2.6).

NEVER raises (``asyncio.CancelledError`` excepted); NEVER mutates the input
list; any failure (network, timeout, MAX_TOKENS, unparsable JSON, empty/
garbage ``chosen``) falls back to the first ``max_n`` photos — exactly
today's photo-count cap behaviour.
"""
from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel

from app.config import Settings
from app.voice.pipeline.tools import PhotoAttachment
from app.voice.providers.google_auth import GoogleAuth

logger = logging.getLogger("voice.pipeline.photo_select")

RANKING_TIMEOUT_S = 20.0


class PhotoRanking(BaseModel):
    chosen: list[int]        # 0-based indices into the photo list, best first


_RANKING_PROMPT = (
    "You rank a farmer's photos for a plant-disease diagnosis. Each photo is "
    "preceded by a text label 'PHOTO <index>'. Choose the photos that together "
    "give a plant pathologist the most information: (1) symptoms clearly "
    "visible and in focus; (2) the damaged organ (leaf, stem, fruit, root) "
    "fills the frame; (3) prefer different angles or different organs over "
    "near-duplicates. Return JSON {{\"chosen\": [<indices>]}} with at most "
    "{max_n} zero-based indices, best first."
)


async def _rank(
    settings: Settings, auth: GoogleAuth, photos: list[PhotoAttachment], max_n: int,
) -> list[int]:
    """One flash ranking call; returns validated, de-duped, in-range indices
    in ORIGINAL capture order. Raises on any problem (caller fails open)."""
    from google.genai import types

    client = auth.genai_client()
    parts: list = []
    for i, p in enumerate(photos):
        parts.append(types.Part(text=f"PHOTO {i}"))
        parts.append(
            types.Part(inline_data=types.Blob(data=p.data, mime_type=p.mime))
        )
    response = await asyncio.wait_for(
        client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                system_instruction=_RANKING_PROMPT.format(max_n=max_n),
                response_mime_type="application/json",
                response_schema=PhotoRanking,
                temperature=0.0,
                max_output_tokens=256,
            ),
        ),
        RANKING_TIMEOUT_S,
    )
    parsed = getattr(response, "parsed", None)
    ranking = parsed if isinstance(parsed, PhotoRanking) else (
        PhotoRanking.model_validate_json(response.text)
    )

    seen: set[int] = set()
    valid: list[int] = []
    for i in ranking.chosen:
        if isinstance(i, int) and 0 <= i < len(photos) and i not in seen:
            seen.add(i)
            valid.append(i)
        if len(valid) >= max_n:
            break
    if not valid:
        raise ValueError("photo ranking returned no usable indices")
    return sorted(valid)


async def select_best_photos(
    settings: Settings,
    auth: GoogleAuth,
    photos: list[PhotoAttachment],
    max_n: int = 3,
) -> list[PhotoAttachment]:
    """Pick the <=max_n most diagnostic photos. NEVER raises (CancelledError
    excepted); NEVER mutates ``photos``; fail-open -> photos[:max_n]."""
    if len(photos) <= max_n:
        return photos
    try:
        indices = await _rank(settings, auth, photos, max_n)
        return [photos[i] for i in indices]
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - fail-open: ranking is optional
        logger.warning(
            "photo ranking failed — using first %d", max_n, exc_info=True
        )
        return photos[:max_n]
