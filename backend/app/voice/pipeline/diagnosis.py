"""Plant-disease diagnosis — a separate, higher-quality REST call.

The Live session runs a fast half-cascade model for the *conversation*; the
actual diagnosis is offloaded here to a stronger model (``diagnosis_model``,
default gemini-3.1-pro-preview) that receives the interview summary JSON plus the
farmer's photos and returns a **structured** verdict. Structured output (a
``response_schema``) keeps the reply machine-usable while ``spoken_summary`` gives
the Live agent one short, farmer-friendly sentence to read aloud.

This is the non-Live REST path, so photos ride ``inline_data`` blobs (the Live
socket's base64 bug does not apply here). The call must never leak an exception
into the Live session — the caller wraps it and voices an apology on failure.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel

from app.config import Settings
from app.voice.pipeline.tools import PhotoAttachment
from app.voice.providers.google_auth import GoogleAuth


class Differential(BaseModel):
    name: str
    why: str


class DiagnosisResult(BaseModel):
    # Whether a valid plant photo was available and used. When false the
    # diagnosis still exists — produced from the interview text alone — and
    # spoken_summary notes the photo was not used.
    is_plant: bool
    likely_disease: str
    confidence: Literal["high", "medium", "low"]
    differentials: list[Differential]
    immediate_treatment: list[str]
    prevention: list[str]
    spoken_summary: str
    language: str
    # Phase 2 (disease-id matching): the id of the single best-matching disease
    # from the Growz candidate list passed to :func:`diagnose`, or "" when none
    # fits. Drives the exact preparations lookup (find_preparations_by_id) —
    # far more reliable than fuzzy-matching the free-text likely_disease.
    growz_disease_id: str = ""


DIAGNOSIS_SYSTEM_PROMPT = (
    "You are an expert plant pathologist diagnosing crop diseases for smallholder "
    "farmers. Your input is an interview summary (JSON) plus zero or more photos.\n"
    "STEP 1 — CHECK THE PHOTOS. If at least one attached photo clearly shows a "
    "living plant, tree, crop or fruit part: set is_plant=true and use the photos "
    "in your diagnosis. If NO photos are attached, or none of them shows a plant "
    "(e.g. a keyboard, a person, furniture): set is_plant=false and simply do not "
    "use images — do NOT refuse to diagnose.\n"
    "STEP 2 — ALWAYS DIAGNOSE. Identify the most likely disease from everything "
    "you have (interview summary alone when is_plant=false), list a few plausible "
    "differentials with a one-line reason each, and give concrete immediate "
    "treatment and prevention steps. When is_plant=false, lower the confidence "
    "accordingly and make spoken_summary briefly state that the diagnosis is "
    "based only on the farmer's description — the photo was missing or did not "
    "show a plant so it was not used — and that a proper photo of the affected "
    "part would make the diagnosis more accurate.\n"
    "If summary.farmer_profile is present, it holds the farmer's real Growz "
    "planting record (crop, planted_date, days_since_planting, growth_period_days, "
    "region, field, current_agrotech_task). USE it: weigh the growth stage, "
    "region/season and recent agrotech (e.g. a just-done fertilization) when "
    "choosing the diagnosis and treatment — but never read it back verbatim.\n"
    "Write every farmer-facing field in the language given by "
    "summary.farmer_language, using simple, non-technical words a farmer "
    "understands. 'spoken_summary' must be at most three short sentences in that "
    "same language — it is read aloud over the phone. Set 'language' to the "
    "farmer_language code.\n"
    "If a `per_image_analysis` JSON block is present, it holds a specialist "
    "pre-read of each attached photo by index (symptoms_seen, organ, "
    "hypotheses, confidence). Use it as evidence to weigh against your own "
    "reading of the photos — it is advisory, not ground truth; still look at "
    "the images yourself."
)


def _candidate_block(growz_diseases: list[dict] | None) -> str:
    """Append the crop's Growz disease candidates so the model returns a real
    ``growz_disease_id`` (matched on meaning, crop prefix ignored) — the exact
    link to preparations. Empty when no candidates are supplied."""
    if not growz_diseases:
        return ""
    listing = "\n".join(
        f"{d.get('id')}: {d.get('name')}" for d in growz_diseases[:150] if d.get("id")
    )
    if not listing:
        return ""
    return (
        "\n\nGROWZ DISEASE CANDIDATES for this crop (format `id: name`). After "
        "diagnosing, set 'growz_disease_id' to the id of the SINGLE best-matching "
        "entry — match on the disease/pest MEANING, ignoring the crop-name prefix "
        "(e.g. your 'Fitoftoroz' == 'Pomidor fitoftorozi'). If none matches your "
        "diagnosis well, set 'growz_disease_id' to \"\".\n" + listing
    )


async def diagnose(
    settings: Settings,
    auth: GoogleAuth,
    summary: dict,
    photos: list[PhotoAttachment],
    growz_diseases: list[dict] | None = None,
    per_image_analyses: list[dict | None] | None = None,
) -> DiagnosisResult:
    """Diagnose from the interview ``summary`` and ``photos`` (structured JSON).

    When ``growz_diseases`` (``[{id, name}]`` for the crop) is given, the model
    also returns ``growz_disease_id`` — the exact Growz disease link used for
    preparations. When ``per_image_analyses`` (same length/order as
    ``photos``, entries may be ``None``) carries any non-None entry, one
    extra JSON text part is inserted with each analysis keyed by its
    0-based photo index — advisory pre-reads for the model to weigh."""
    from google.genai import types

    client = auth.genai_client()
    parts = [types.Part(text=json.dumps(summary, ensure_ascii=False))]
    if per_image_analyses and any(a is not None for a in per_image_analyses):
        analysis_block = {
            "per_image_analysis": [
                {"photo_index": i, **a}
                for i, a in enumerate(per_image_analyses)
                if a is not None
            ]
        }
        parts.append(types.Part(text=json.dumps(analysis_block, ensure_ascii=False)))
    parts.extend(
        types.Part(inline_data=types.Blob(data=p.data, mime_type=p.mime))
        for p in photos
    )
    response = await client.aio.models.generate_content(
        model=settings.diagnosis_model or settings.gemini_model,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            system_instruction=DIAGNOSIS_SYSTEM_PROMPT + _candidate_block(growz_diseases),
            response_mime_type="application/json",
            response_schema=DiagnosisResult,
            temperature=settings.diagnosis_temperature,
            max_output_tokens=settings.diagnosis_max_tokens,
        ),
    )
    # Pro-tier models spend "thinking" tokens from the same output budget; a
    # too-small budget truncates the JSON mid-string. Fail loudly so the
    # session sends a clean error event instead of a half-parsed diagnosis.
    candidate = (getattr(response, "candidates", None) or [None])[0]
    finish = getattr(candidate, "finish_reason", None)
    if finish is not None and "MAX_TOKENS" in str(finish):
        raise RuntimeError(
            f"diagnosis truncated at {settings.diagnosis_max_tokens} output "
            f"tokens (finish_reason={finish}); raise DIAGNOSIS_MAX_TOKENS"
        )
    # response.parsed is the SDK-validated object when response_schema is set;
    # fall back to strict JSON parsing of the text.
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, DiagnosisResult):
        return parsed
    return DiagnosisResult.model_validate_json(response.text)
