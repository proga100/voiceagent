"""Live-session case tools: request_photo + finalize_case.

Two function declarations the Gemini Live model can call while interviewing a
farmer (validated against gemini-3.1-flash-live-preview in ``poc_live_tools``):

  * **request_photo** — open the client camera for a specific plant part the
    moment a photo would sharpen the diagnosis.
  * **finalize_case** — hand the collected interview summary to the separate
    diagnosis model once enough is known and at least one photo has arrived.

The declarations are built lazily (``google.genai`` imported inside the function,
matching every other provider) so unit tests can import this module without the
SDK. Photos captured for a session are carried by :class:`PhotoAttachment`.
"""
from __future__ import annotations

from dataclasses import dataclass

# Plant parts the model may target with request_photo. Kept as a module constant
# so the tool enum and any client-side validation share one source of truth.
TARGET_PARTS = [
    "leaf", "stem", "fruit", "flower", "root",
    "branch", "bark", "whole_plant", "soil",
]


@dataclass
class PhotoAttachment:
    """One farmer photo held for the session: fed to Live and to diagnosis."""

    photo_id: str
    data: bytes
    mime: str
    target_part: str | None = None
    # §4.1 — advisory quality signal from deterministic checks + the VLM verdict.
    image_confidence: str = "ok"          # "ok" | "low"
    # photo_id of the earlier near-duplicate this photo matched, else None.
    duplicate_of: str | None = None
    # Disk path once persisted; None when persistence failed (fail-open).
    stored_path: str | None = None
    # {"blur_var", "blurry", "too_dark", "too_bright"} or None on failure/disabled.
    quality: dict | None = None
    # {"symptom_visible", "multiple_plants", "quality_ok"} or None (unverified).
    vlm_flags: dict | None = None
    # §5.4 per-image analysis result, set at finalize for SELECTED photos only.
    per_image_analysis: dict | None = None


def build_case_tools():
    """The [request_photo, finalize_case] tool list for ``LiveConnectConfig``."""
    from google.genai import types

    request_photo = types.FunctionDeclaration(
        name="request_photo",
        description=(
            "Fermerdan oʻsimlikning kasallangan qismi rasmini soʻrash. Kamera "
            "ochiladi va fermer rasm oladi. Rasm tashxisga yordam berishi bilanoq "
            "chaqir."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "reason": types.Schema(
                    type=types.Type.STRING,
                    description="Nega rasm kerakligi (qisqa izoh).",
                ),
                "target_part": types.Schema(
                    type=types.Type.STRING,
                    enum=TARGET_PARTS,
                    description="Suratga olinadigan oʻsimlik qismi.",
                ),
            },
            required=["reason", "target_part"],
        ),
    )

    finalize_case = types.FunctionDeclaration(
        name="finalize_case",
        description=(
            "Intervyu yetarli boʻlgach, yigʻilgan maʼlumotlarni tashxis uchun "
            "yuborish. Ekin, belgilar, boshlanish/tarqalish va miqyos aniq boʻlsa "
            "va kamida bitta rasm kelgan boʻlsa chaqir."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "summary": types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "crop": types.Schema(type=types.Type.STRING),
                        "growth_stage": types.Schema(type=types.Type.STRING),
                        "symptoms": types.Schema(type=types.Type.STRING),
                        "location_on_plant": types.Schema(type=types.Type.STRING),
                        "onset_and_spread": types.Schema(type=types.Type.STRING),
                        "affected_scale": types.Schema(type=types.Type.STRING),
                        "recent_conditions": types.Schema(type=types.Type.STRING),
                        "treatments_tried": types.Schema(type=types.Type.STRING),
                        "farmer_language": types.Schema(type=types.Type.STRING),
                    },
                    required=["crop", "symptoms", "farmer_language"],
                ),
            },
            required=["summary"],
        ),
    )

    return [types.Tool(function_declarations=[request_photo, finalize_case])]
