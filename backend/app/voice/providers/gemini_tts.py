"""Staged-pipeline TTS provider for Google.

The staged STT->LLM->TTS pipeline needs a per-sentence synthesizer. Google has
no Uzbek Cloud TTS voice, so this provider routes by ``settings.tts_provider``:

* ``gemini_live`` — synthesize each sentence through a one-shot Gemini Live turn
  (``language_code=uz-UZ``). This is the supported Google path to Uzbek speech.
* ``external``    — POST the sentence to an external Uzbek TTS endpoint
  (``EXTERNAL_TTS_URL``), e.g. an Azure ``uz-UZ`` voice or a self-hosted
  Chatterbox/FeruzaSpeech service. Expects raw PCM bytes back.
* ``none``        — raise a clear ``UzbekTTSUnavailable`` error. We never fall
  back to a non-Uzbek voice silently.

Implements the same ``TTSProvider`` Protocol as the Yandex TTS it replaces.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from app.config import Settings
from app.voice.providers.gemini_live import synthesize_uzbek
from app.voice.providers.google_auth import GoogleAuth


class UzbekTTSUnavailable(RuntimeError):
    """Raised when no Uzbek voice is configured. Never silently substituted."""


class GeminiTTSProvider:
    def __init__(self, settings: Settings, auth: GoogleAuth) -> None:
        self._s = settings
        self._auth = auth
        self._cancelled: set[str] = set()

    async def synthesize_chunk(
        self, text: str, voice: str, session_id: str = "default"
    ) -> AsyncIterator[bytes]:
        if not text.strip() or session_id in self._cancelled:
            return

        mode = self._s.tts_provider
        if mode == "gemini_live":
            async for frame in synthesize_uzbek(
                self._s,
                self._auth,
                text,
                cancelled=lambda: session_id in self._cancelled,
            ):
                yield frame
        elif mode == "external":
            async for frame in self._external(text, session_id):
                yield frame
        else:  # "none"
            raise UzbekTTSUnavailable(
                "Uzbek TTS fallback required: Google Cloud TTS has no Uzbek voice. "
                "Set TTS_PROVIDER=gemini_live (Gemini Live audio) or TTS_PROVIDER="
                "external with EXTERNAL_TTS_URL pointing at an Uzbek voice service."
            )

    async def _external(self, text: str, session_id: str) -> AsyncIterator[bytes]:
        import httpx

        if not self._s.external_tts_url:
            raise UzbekTTSUnavailable(
                "TTS_PROVIDER=external requires EXTERNAL_TTS_URL"
            )
        payload = {"text": text, "voice": self._s.external_tts_voice}
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            async with client.stream(
                "POST", self._s.external_tts_url, json=payload
            ) as resp:
                resp.raise_for_status()
                async for frame in resp.aiter_bytes():
                    if session_id in self._cancelled:
                        break
                    if frame:
                        yield frame

    async def stream_audio(
        self, text_stream: AsyncIterator[str], session_id: str = "default"
    ) -> AsyncIterator[bytes]:
        async for sentence in text_stream:
            if session_id in self._cancelled:
                break
            async for frame in self.synthesize_chunk(
                sentence, self._s.tts_voice, session_id
            ):
                yield frame

    async def cancel(self, session_id: str = "default") -> None:
        self._cancelled.add(session_id)
        await asyncio.sleep(0)

    def reset(self, session_id: str = "default") -> None:
        self._cancelled.discard(session_id)
