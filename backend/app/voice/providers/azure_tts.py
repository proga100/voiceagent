"""Azure Neural TTS — real native Uzbek voices (no accent).

Unlike Gemini Live (which approximates Uzbek and carries an accent), Azure has
two genuine ``uz-UZ`` neural voices: ``uz-UZ-MadinaNeural`` (female) and
``uz-UZ-SardorNeural`` (male). This provider hits the Azure Speech REST endpoint
and streams back raw PCM at 24 kHz to match the client's playback rate.

It's used in the Google agent's "Azure voice" mode: Gemini Live does STT + the
LLM reply (TEXT), and Azure synthesizes that text here — so the brain is Google,
the accent-free voice is Azure.
"""
from __future__ import annotations

from typing import AsyncIterator
from xml.sax.saxutils import escape

import httpx

from app.config import Settings


class AzureTTSUnavailable(RuntimeError):
    pass


class AzureTTSProvider:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        # One keep-alive client reused across sentences: the TLS handshake to
        # Azure happens once, not per sentence, cutting per-sentence latency.
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=30),
            )
        return self._client

    async def prewarm(self) -> None:
        """Best-effort: open the TLS connection now so the first synth is faster."""
        if not self._s.azure_speech_key:
            return
        try:
            host = f"https://{self._s.azure_speech_region}.tts.speech.microsoft.com/"
            await self._http().get(host, timeout=httpx.Timeout(5.0))
        except Exception:  # noqa: BLE001 - warmup is optional; ignore failures
            pass

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def _endpoint(self) -> str:
        return (
            f"https://{self._s.azure_speech_region}.tts.speech.microsoft.com"
            "/cognitiveservices/v1"
        )

    def _ssml(self, text: str, voice: str) -> str:
        return (
            "<speak version='1.0' xml:lang='uz-UZ'>"
            f"<voice name='{voice}'>{escape(text)}</voice></speak>"
        )

    async def synthesize_chunk(
        self, text: str, voice: str, session_id: str = "default"
    ) -> AsyncIterator[bytes]:
        """Stream raw 24 kHz PCM for one sentence/turn via Azure Neural TTS."""
        if not text.strip():
            return
        if not self._s.azure_speech_key:
            raise AzureTTSUnavailable("AZURE_SPEECH_KEY is not set")
        headers = {
            "Ocp-Apim-Subscription-Key": self._s.azure_speech_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self._s.azure_tts_format,
            "User-Agent": "voiceagent-google",
        }
        body = self._ssml(text, voice).encode("utf-8")
        # Azure streams raw 16-bit PCM, but HTTP chunks can split mid-sample (odd
        # byte counts). Carry the dangling byte forward so every yielded frame is
        # 2-byte aligned — otherwise the client's Int16 parse misaligns => noise.
        carry = b""
        async with self._http().stream(
            "POST", self._endpoint, headers=headers, content=body
        ) as resp:
            resp.raise_for_status()
            async for frame in resp.aiter_bytes():
                if not frame:
                    continue
                buf = carry + frame
                if len(buf) % 2:
                    carry = buf[-1:]
                    buf = buf[:-1]
                else:
                    carry = b""
                if buf:
                    yield buf
