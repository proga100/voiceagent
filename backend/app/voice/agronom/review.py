"""AI senior-agronom second opinion (contract addendum Phase 3, P3.5).

Mirrors ``pipeline/diagnosis.py``'s genai pattern exactly
(``client.aio.models.generate_content``, ``response_schema``, MAX_TOKENS
finish-reason check, ``response.parsed`` -> ``model_validate_json``
fallback). This is a clearly-labelled STUB second opinion — every review it
produces is written with ``is_mock: true``.

FAIL-OPEN throughout: any error (genai failure, timeout, MAX_TOKENS,
unparsable JSON, empty summary, store race) leaves the review at
``status="pending"`` — never raises into the caller, never touches the
diagnosis or the live call (contract P3.11).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal

from pydantic import BaseModel, Field

from app.config import Settings
from app.voice.chat.models import UZ, ChatDoc, now_iso, sanitize_expert_payload
from app.voice.chat.store import ChatStore, lock_for
from app.voice.enrich.treatments import find_preparations_by_id, get_crop_diseases
from app.voice.providers.google_auth import GoogleAuth

logger = logging.getLogger("voice.agronom")

REVIEW_TIMEOUT_S = 60.0


class ExpertReview(BaseModel):
    verdict: Literal["confirmed", "adjusted"]
    expert_summary: str          # 1-3 short Uzbek sentences, farmer-facing
    expert_notes: list[str] = Field(default_factory=list)   # 0-6 short practical Uzbek notes
    keep_preparations: bool      # True = the AI list stands
    # When keep_preparations is False: the id of the better-matching entry
    # from the Growz candidate list, or "" (then the AI list stands anyway).
    adjusted_growz_disease_id: str = ""


AGRONOM_REVIEW_SYSTEM_PROMPT = (
    "You are a SENIOR agronomist giving a second opinion on a junior AI "
    "assistant's crop diagnosis for a smallholder farmer. Input is a JSON "
    "with the crop, the interview facts, the AI diagnosis (disease, "
    "confidence, differentials, treatment, prevention) and the recommended "
    "preparations with doses.\n"
    "Review it like an expert: is the diagnosis plausible given the facts? "
    "Are the preparations and doses appropriate for this crop and problem?\n"
    "- If the diagnosis and preparations are sound: verdict='confirmed'.\n"
    "- If anything should change (different likely disease, wrong or "
    "missing preparation, dose concern, missing precaution): "
    "verdict='adjusted' and explain in the notes.\n"
    "Set keep_preparations=false ONLY when a DIFFERENT disease from the "
    "GROWZ DISEASE CANDIDATES list fits better — then put its id in "
    "adjusted_growz_disease_id. Otherwise keep_preparations=true and "
    "adjusted_growz_disease_id=\"\".\n"
    "Write expert_summary in UZBEK (Latin script), 1-3 short, simple, "
    "farmer-friendly sentences. expert_notes: 0-6 short practical Uzbek "
    "notes (dose timing, safety, application tips, what to re-check). "
    "Never mention being an AI or that you are reviewing an AI.\n"
    "The input may include 'session_photos': file references (not images) "
    "with per-image pre-reads from a specialist triage — use them as extra "
    "context; you do not receive the actual photos."
)


def _candidate_block(growz_diseases: list[dict] | None) -> str:
    """Local copy of diagnosis.py's ``_candidate_block`` (NOT imported —
    same listing shape, wording adapted to ``adjusted_growz_disease_id``)."""
    if not growz_diseases:
        return ""
    listing = "\n".join(
        f"{d.get('id')}: {d.get('name')}" for d in growz_diseases[:150] if d.get("id")
    )
    if not listing:
        return ""
    return (
        "\n\nGROWZ DISEASE CANDIDATES for this crop (format `id: name`). If a "
        "DIFFERENT disease/pest fits better than the AI diagnosis, set "
        "'adjusted_growz_disease_id' to the id of the SINGLE best-matching "
        "entry — match on the disease/pest MEANING, ignoring the crop-name "
        "prefix (e.g. 'Fitoftoroz' == 'Pomidor fitoftorozi'). Otherwise leave "
        "it \"\".\n" + listing
    )


async def review_diagnosis(
    settings: Settings,
    auth: GoogleAuth,
    review_input: dict,
    growz_candidates: list[dict],
) -> ExpertReview:
    """Ask the senior-agronom model for a second opinion. Raises on failure —
    the caller (``_run_mock_review``) is responsible for catching."""
    from google.genai import types

    client = auth.genai_client()
    parts = [types.Part(text=json.dumps(review_input, ensure_ascii=False))]
    response = await client.aio.models.generate_content(
        model=settings.diagnosis_model or settings.gemini_model,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            system_instruction=(
                AGRONOM_REVIEW_SYSTEM_PROMPT + _candidate_block(growz_candidates)
            ),
            response_mime_type="application/json",
            response_schema=ExpertReview,
            temperature=settings.diagnosis_temperature,
            max_output_tokens=settings.diagnosis_max_tokens,
        ),
    )
    candidate = (getattr(response, "candidates", None) or [None])[0]
    finish = getattr(candidate, "finish_reason", None)
    if finish is not None and "MAX_TOKENS" in str(finish):
        raise RuntimeError(
            f"agronom review truncated at {settings.diagnosis_max_tokens} output "
            f"tokens (finish_reason={finish}); raise DIAGNOSIS_MAX_TOKENS"
        )
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, ExpertReview):
        return parsed
    return ExpertReview.model_validate_json(response.text)


def _review_input(doc: ChatDoc) -> dict:
    """Tolerates thin/old ``last_diagnosis`` (pre-Phase-3 chats)."""
    diag = doc.last_diagnosis or {}
    return {
        "crop_name": doc.crop_name,
        "plant_part": doc.plant_part,
        "query_type": doc.query_type,
        "symptom_summary": doc.symptom_summary,
        "general_question": doc.general_question,
        "interview_summary": diag.get("summary") or {},
        "ai_diagnosis": diag.get("result") or {
            "likely_disease": diag.get("disease", ""),
            "confidence": diag.get("confidence", ""),
        },
        "ai_preparations": diag.get("preparations_full")
        or [{"name": n} for n in (diag.get("preparations") or [])],
        # §5.5 — file references + per-image pre-reads (never raw bytes);
        # tolerant of pre-change chats via ``.get()``.
        "session_photos": diag.get("photos") or [],
    }


def maybe_start_mock_review(settings: Settings, store: ChatStore, doc: ChatDoc) -> None:
    """Start the AI second opinion iff flags on, a request is pending and a
    diagnosis exists to review. Never raises; never blocks the caller."""
    try:
        if not (settings.agronom_enabled and settings.agronom_mock_enabled):
            return
        r = doc.agronom_review
        if r is None or r.status != "pending":
            return
        if not (doc.last_diagnosis or {}).get("disease"):
            return   # nothing to review yet — the teardown call retries
        asyncio.create_task(_run_mock_review(settings, store, doc.user_id, doc.id))
    except Exception:  # noqa: BLE001
        logger.exception("agronom mock kickoff failed")


async def _run_mock_review(
    settings: Settings, store: ChatStore, user_id: str, chat_id: str
) -> None:
    """Frozen flow (contract P3.5) — FAIL-OPEN: any error leaves status
    ``"pending"``."""
    try:
        from app.voice.providers.factory import build_auth

        doc = store.read(user_id, chat_id)
        if doc is None or doc.agronom_review is None:
            return
        if doc.agronom_review.status != "pending":
            return
        candidates = await get_crop_diseases(          # fail-open [] (P2.2)
            settings, doc.crop_name, doc.query_type or "disease_pest"
        )
        review = await asyncio.wait_for(
            review_diagnosis(
                settings, build_auth(settings), _review_input(doc), candidates
            ),
            REVIEW_TIMEOUT_S,
        )
        adjusted: list[dict] = []
        if not review.keep_preparations and review.adjusted_growz_disease_id:
            adjusted = await find_preparations_by_id(   # fail-open [] (P2.2)
                settings, review.adjusted_growz_disease_id
            )
        summary, notes, adjusted = sanitize_expert_payload(
            review.expert_summary, review.expert_notes, adjusted
        )
        if not summary:
            return                          # useless review -> stay pending
        async with lock_for(chat_id):
            doc = store.read(user_id, chat_id)          # FRESH under the lock
            if doc is None or doc.agronom_review is None:
                return
            if doc.agronom_review.status == "done":
                return   # human beat us — human wins
            r = doc.agronom_review
            r.status = "done"
            r.reviewed_at = now_iso()
            r.is_mock = True
            r.verdict = review.verdict
            r.expert_summary = summary
            r.expert_notes = notes
            r.adjusted_preparations = adjusted
            store.append_message(
                doc, "agronom", "agronom_review", f"{UZ['agronomPrefix']} {summary}"
            )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — the review is optional decoration
        logger.warning("mock agronom review failed — stays pending", exc_info=True)
