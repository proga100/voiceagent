"""POC: prove the Gemini Live API speaks Uzbek (the only Google path to it).

Feeds an Uzbek sentence to the Live API with ``language_code=uz-UZ`` and writes
the returned native audio to a WAV file. If the Live API / audio modality is
unavailable on the account/region/SDK, it FAILS LOUDLY — it never falls back to
another language.

Run (needs real creds in .env):
    python -m app.voice.benchmark.poc_live_audio
"""
from __future__ import annotations

import asyncio
import wave

from app.config import get_settings
from app.voice.providers.gemini_live import synthesize_uzbek
from app.voice.providers.google_auth import GoogleAuth

SAMPLE_TEXT = "Assalomu alaykum. Men o'zbek tilida gaplashadigan AI yordamchiman."
OUT_PATH = "poc_live_uzbek.wav"


async def main() -> None:
    settings = get_settings()
    auth = GoogleAuth(settings)

    print(f"Model:    {settings.gemini_live_model}")
    print(f"Language: {settings.gemini_live_language}")
    print(f"Voice:    {settings.gemini_live_voice}")
    print(f"Text:     {SAMPLE_TEXT}")

    pcm = bytearray()
    async for frame in synthesize_uzbek(settings, auth, SAMPLE_TEXT):
        pcm += frame

    if not pcm:
        raise SystemExit(
            "FAIL: Gemini Live returned no audio for Uzbek. Uzbek TTS fallback "
            "required — verify Live API access/region or set TTS_PROVIDER=external."
        )

    with wave.open(OUT_PATH, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit PCM
        w.setframerate(settings.audio_output_sample_rate_hz)
        w.writeframes(bytes(pcm))

    print(f"OK: wrote {len(pcm)} bytes of Uzbek audio -> {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
