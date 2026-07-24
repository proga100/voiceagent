"""Dev-only session autopilot for on-device diagnosis (DEBUG_AUTOPILOT=1).

Reproduces the plant-doctor flow deterministically with NO mic input, so a
phone connected over adb can be exercised end-to-end by scripts alone:

  t+2s   long spoken sentence (lipsync / audio-continuity window)
  t+14s  synthetic ``tool.request_photo`` (photo CTA appears in the app)
  t+20s  speaks again — historically the audio-scramble window (camera open)

Used together with the app's ``--dart-define=AUDIO_DEBUG=true`` telemetry,
which arrives back over the WebSocket as ``debug.log`` events and is printed
by the receive loop. Both are inert unless explicitly enabled.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("voice.dev_autopilot")

_GREETING = (
    "[TIZIM] Quyidagi matnni to'liq o'qib ber: Assalomu alaykum, men "
    "Alomat. Bugun pomidor kasalliklari haqida gaplashamiz. Barglarda "
    "dog'lar, poyada chirish va mevada yoriqlar eng ko'p uchraydigan "
    "muammolar hisoblanadi. Keling, birgalikda o'simligingizni "
    "tekshiramiz va aniq tashxis qo'yamiz."
)

_CAMERA_SPEECH = (
    "[TIZIM] Quyidagini o'qib ber: Kamera ochiq bo'lsa ham men gapirishda "
    "davom etaman. Bu ovoz sifatini tekshirish uchun uzun gap. Rasmni "
    "yaqindan va aniq qilib oling, shunda kasallikni to'g'ri aniqlaymiz."
)


async def run_dev_autopilot(session) -> None:
    """Drive one deterministic repro sequence against ``session``."""
    if not hasattr(session, "_speak_text"):
        return
    try:
        await asyncio.sleep(2)
        await session._speak_text(_GREETING)
        await asyncio.sleep(12)
        await session._send_json({
            "type": "tool.request_photo", "call_id": "dev-autopilot",
            "reason": "diagnostika sinovi", "target_part": "leaf",
        })
        await asyncio.sleep(6)
        await session._speak_text(_CAMERA_SPEECH)
    except Exception:  # noqa: BLE001 — a dev helper must never kill a session
        logger.exception("dev autopilot failed")
