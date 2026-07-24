"""Upload-time photo verification — the gate BEFORE a photo enters the flow.

The Live conversation model is easily led: we used to inject every photo with
our own label ("[Rasm yuborildi: leaf]"), so it happily "saw" a leaf in a
keyboard. This module runs one fast, structured vision call (``gemini_model``,
flash-tier) the moment a photo arrives and decides:

  * ``is_plant``     — does the image actually show a living plant / tree part
                       or fruit at all?
  * ``seen_part``    — which part is visible (leaf/stem/fruit/... or "none");
  * ``matches_target`` — does it show the part the conversation asked for?

Only verified-plant photos are stored for the final diagnosis; the Live model
is told the *verified* result (or the mismatch) instead of our assumption.
Verification is best-effort: on any error or timeout the photo is accepted
unverified — availability beats strictness for a field tool.
"""
from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel

from app.config import Settings
from app.voice.pipeline.tools import TARGET_PARTS
from app.voice.providers.google_auth import GoogleAuth

_VERIFY_TIMEOUT_S = 8.0

#: Uzbek names for the spoken feedback, keyed by TARGET_PARTS values.
PART_UZ = {
    "leaf": "barg",
    "stem": "poya",
    "fruit": "meva",
    "flower": "gul",
    "root": "ildiz",
    "branch": "shox",
    "bark": "poʻstloq",
    "whole_plant": "butun oʻsimlik",
    "soil": "tuproq",
}


class PhotoVerdict(BaseModel):
    """Structured output of the verification call."""

    is_plant: bool
    #: One of TARGET_PARTS when a plant part is visible, else "none".
    seen_part: Literal[
        "leaf", "stem", "fruit", "flower", "root", "branch", "bark",
        "whole_plant", "soil", "none",
    ]
    #: True when the visible part is (or plausibly includes) the requested one.
    matches_target: bool
    #: §4 additions — neutral defaults so the existing ``unverified`` fallback
    #: needs no change and old parses (missing keys) stay valid.
    #: True only when damage/disease/pest symptoms are actually visible.
    symptom_visible: bool = True
    #: True when the frame shows many separate plants (a whole field/row).
    multiple_plants: bool = False
    #: False when the image is too blurry/dark/bright/far to judge symptoms.
    quality_ok: bool = True
    #: True when the call itself failed/timed out — treat the photo as
    #: accepted-but-unverified. Never produced by the model.
    unverified: bool = False


_VERIFY_SYSTEM_PROMPT = (
    "You verify photos for a plant-disease voice assistant used by farmers. "
    "Look at the image and answer strictly about what is VISIBLE:\n"
    "1) is_plant: true only if the image clearly shows a living plant, tree, "
    "crop, or fruit — a leaf, stem, fruit, flower, root, branch, bark, a whole "
    "plant, or soil immediately around a plant. Objects like keyboards, "
    "people, animals, furniture, walls, screens or documents are NOT plants.\n"
    "2) seen_part: the single most prominent plant part from the allowed list, "
    "or 'none' when is_plant is false.\n"
    "3) matches_target: true when seen_part is the requested part, or when the "
    "requested part is clearly visible in the photo even if something else "
    "dominates. The requested part is given in the user message.\n"
    "4) symptom_visible: true only if damage/disease/pest symptoms are "
    "actually visible on the plant.\n"
    "5) multiple_plants: true when the frame shows many separate plants (a "
    "whole field/row) rather than one plant or one part.\n"
    "6) quality_ok: false when the image is too blurry, too dark, too bright "
    "or too far away to judge symptoms.\n"
    "Be strict: when in doubt about is_plant, answer false."
)


async def verify_photo(
    settings: Settings,
    auth: GoogleAuth,
    data: bytes,
    mime: str,
    target_part: str | None,
) -> PhotoVerdict:
    """Classify one uploaded photo. Never raises: errors → ``unverified``."""
    try:
        return await asyncio.wait_for(
            _verify(settings, auth, data, mime, target_part), _VERIFY_TIMEOUT_S
        )
    except Exception:  # noqa: BLE001 — verification must never block a photo
        return PhotoVerdict(
            is_plant=True, seen_part=target_part if target_part in TARGET_PARTS
            else "whole_plant", matches_target=True, unverified=True,
        )


async def _verify(
    settings: Settings,
    auth: GoogleAuth,
    data: bytes,
    mime: str,
    target_part: str | None,
) -> PhotoVerdict:
    from google.genai import types

    client = auth.genai_client()
    asked = target_part if target_part in TARGET_PARTS else "whole_plant"
    response = await client.aio.models.generate_content(
        model=settings.gemini_model,
        contents=[types.Content(role="user", parts=[
            types.Part(text=f"Requested part: {asked}"),
            types.Part(inline_data=types.Blob(data=data, mime_type=mime)),
        ])],
        config=types.GenerateContentConfig(
            system_instruction=_VERIFY_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=PhotoVerdict,
            temperature=0.0,
            max_output_tokens=2048,
        ),
    )
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, PhotoVerdict):
        return parsed
    return PhotoVerdict.model_validate_json(response.text)
