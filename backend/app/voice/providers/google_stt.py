"""Google Cloud Speech-to-Text v2 — streaming recognition.

Service ``SpeechAsyncClient.streaming_recognize`` against the regional endpoint
``<region>-speech.googleapis.com``. The first request carries the
``StreamingRecognitionConfig`` (language ``uz-UZ``, model ``chirp_2``, LINEAR16
16 kHz mono, interim results); subsequent requests carry audio bytes.

Uzbek (``uz-UZ``) is supported by the ``chirp`` / ``chirp_2`` models in
``asia-southeast1`` and ``europe-west4`` only — the region in ``Settings`` must
serve the chosen model. Implements the same ``STTProvider`` Protocol as the
Yandex STT it replaces, so the pipeline is unchanged.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from app.config import Settings
from app.voice.providers.base import Transcript
from app.voice.providers.google_auth import GoogleAuth

_END = None  # sentinel pushed onto the chunk queue to end the request stream


class GoogleSTTProvider:
    def __init__(self, settings: Settings, auth: GoogleAuth) -> None:
        self._s = settings
        self._auth = auth
        self._chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._transcripts: asyncio.Queue[Transcript] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._sample_rate = settings.audio_input_sample_rate_hz
        self._client = None

    def _config_request(self):
        from google.cloud.speech_v2.types import cloud_speech

        recognition_config = cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=self._sample_rate,
                audio_channel_count=1,
            ),
            language_codes=[self._s.google_stt_language],
            model=self._s.google_stt_model,
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=True,
            ),
        )
        streaming_config = cloud_speech.StreamingRecognitionConfig(
            config=recognition_config,
            streaming_features=cloud_speech.StreamingRecognitionFeatures(
                interim_results=True,
            ),
        )
        return cloud_speech.StreamingRecognizeRequest(
            recognizer=self._s.stt_recognizer_path,
            streaming_config=streaming_config,
        )

    async def _request_iterator(self):
        from google.cloud.speech_v2.types import cloud_speech

        # First request configures the session; then audio chunks until sentinel.
        yield self._config_request()
        while True:
            chunk = await self._chunks.get()
            if chunk is _END:
                return
            yield cloud_speech.StreamingRecognizeRequest(audio=chunk)

    async def start_stream(self, session_id: str, sample_rate: int | None = None) -> None:
        if sample_rate:
            self._sample_rate = sample_rate
        self._client = self._auth.speech_client()
        call = await self._client.streaming_recognize(requests=self._request_iterator())
        self._task = asyncio.create_task(self._consume(call))

    async def _consume(self, call: AsyncIterator) -> None:
        try:
            async for response in call:
                for result in response.results:
                    if not result.alternatives:
                        continue
                    text = result.alternatives[0].transcript
                    if not text:
                        continue
                    kind = "final" if result.is_final else "partial"
                    await self._transcripts.put(Transcript(kind, text))
        except Exception as exc:  # noqa: BLE001 - surface as an error transcript
            await self._transcripts.put(
                Transcript("error", error=f"{type(exc).__name__}: {exc}")
            )
        finally:
            await self._transcripts.put(Transcript("error", error="__closed__"))

    async def send_audio_chunk(self, chunk: bytes) -> None:
        await self._chunks.put(chunk)

    async def receive_transcripts(self) -> AsyncIterator[Transcript]:
        while True:
            t = await self._transcripts.get()
            if t.kind == "error" and t.error == "__closed__":
                return
            yield t

    async def close(self) -> None:
        await self._chunks.put(_END)
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
