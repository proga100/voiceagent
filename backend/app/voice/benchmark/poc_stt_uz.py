"""POC: Uzbek speech recognition via Cloud Speech-to-Text v2 (chirp_2).

Transcribes a 16 kHz mono LINEAR16 WAV with ``language_codes=["uz-UZ"]`` and the
``chirp_2`` model, printing the transcript and confidence/metadata.

Run (needs real creds + GOOGLE_PROJECT_ID):
    python -m app.voice.benchmark.poc_stt_uz path/to/uzbek_16k_mono.wav
"""
from __future__ import annotations

import asyncio
import sys
import wave

from app.config import get_settings
from app.voice.providers.google_auth import GoogleAuth


async def main(path: str) -> None:
    from google.cloud.speech_v2.types import cloud_speech

    settings = get_settings()
    auth = GoogleAuth(settings)
    client = auth.speech_client()

    with wave.open(path, "rb") as w:
        sample_rate = w.getframerate()
        audio = w.readframes(w.getnframes())

    print(f"Recognizer: {settings.stt_recognizer_path}")
    print(f"Endpoint:   {settings.stt_api_endpoint}")
    print(f"Model:      {settings.google_stt_model}  Lang: {settings.google_stt_language}")
    print(f"Audio:      {len(audio)} bytes @ {sample_rate} Hz")

    config = cloud_speech.RecognitionConfig(
        explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
            encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=sample_rate,
            audio_channel_count=1,
        ),
        language_codes=[settings.google_stt_language],
        model=settings.google_stt_model,
        features=cloud_speech.RecognitionFeatures(enable_automatic_punctuation=True),
    )
    request = cloud_speech.RecognizeRequest(
        recognizer=settings.stt_recognizer_path,
        config=config,
        content=audio,
    )
    response = await client.recognize(request=request)

    if not response.results:
        raise SystemExit("FAIL: no transcript returned for Uzbek audio.")
    for result in response.results:
        if result.alternatives:
            alt = result.alternatives[0]
            print(f"Transcript: {alt.transcript}")
            print(f"Confidence: {alt.confidence}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m app.voice.benchmark.poc_stt_uz <wav>")
    asyncio.run(main(sys.argv[1]))
