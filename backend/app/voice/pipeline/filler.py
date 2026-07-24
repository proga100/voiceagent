"""Instant acknowledgment fillers.

Yandex STT can't endpoint faster than 500 ms of silence, and GPT+TTS add ~1 s on
top. To make the agent feel instantly responsive, we play a short pre-synthesized
filler ("Ha, hozir.", "Mm, koʻraylik.") the moment a final transcript arrives,
while the real answer is still being generated.

Fillers are synthesized ONCE per session (pre-warmed in the background at start)
and cached as raw audio frames, so playing one costs nothing at turn time. A small
rotation avoids sounding robotic.
"""
from __future__ import annotations

import asyncio
import logging
import random

logger = logging.getLogger("voice.filler")

# Neutral "thinking"/acknowledgment phrases — safe regardless of the answer.
DEFAULT_FILLERS_UZ = ("Ha, hozir.", "Mm, koʻraylik.", "Shunaqa, hozir.")

_FILLER_SESSION = "__filler_synth__"  # dedicated id (never barge-in cancelled)


class FillerBank:
    def __init__(self, tts, voice: str, phrases=DEFAULT_FILLERS_UZ) -> None:
        self._tts = tts
        self._voice = voice
        self._phrases = list(phrases)
        self._cache: list[bytes] = []  # one concatenated PCM blob per phrase
        self._ready = False

    def ready(self) -> bool:
        return self._ready and bool(self._cache)

    async def prewarm(self) -> None:
        """Synthesize every filler once and cache its audio. Best-effort."""
        try:
            for text in self._phrases:
                blob = bytearray()
                async for frame in self._tts.synthesize_chunk(
                    text, self._voice, _FILLER_SESSION
                ):
                    blob += frame
                if blob:
                    self._cache.append(bytes(blob))
            self._ready = bool(self._cache)
            logger.info("filler bank ready: %d phrases", len(self._cache))
        except Exception:  # noqa: BLE001 - fillers are optional, never fatal
            logger.exception("filler prewarm failed; continuing without fillers")
            self._ready = False

    def next_audio(self) -> bytes | None:
        """A random filler's PCM blob, or None if not ready."""
        if not self.ready():
            return None
        return random.choice(self._cache)
