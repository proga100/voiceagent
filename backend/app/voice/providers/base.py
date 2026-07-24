"""Provider Protocols — the seam between the pipeline and the cloud provider.

Every provider call (Google Cloud Speech, Gemini, Gemini Live) is wrapped behind
one of these so the pipeline never imports SDK/HTTP details directly, and so
providers can be swapped or mocked in tests. Mirrors the ai-agent-db
``Protocol``-based agent-interface convention.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Shared value types
# ---------------------------------------------------------------------------

TranscriptKind = Literal["partial", "final", "error"]


@dataclass(slots=True)
class Transcript:
    kind: TranscriptKind
    text: str = ""
    error: str | None = None


@dataclass(slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    text: str


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------


@runtime_checkable
class STTProvider(Protocol):
    async def start_stream(self, session_id: str) -> None: ...
    async def send_audio_chunk(self, chunk: bytes) -> None: ...
    def receive_transcripts(self) -> AsyncIterator[Transcript]: ...
    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# LLM (Gemini)
# ---------------------------------------------------------------------------


@runtime_checkable
class GPTProvider(Protocol):
    def stream_response(
        self,
        messages: list[ChatMessage],
        system_prompt: str,
        options: dict | None = None,
    ) -> AsyncIterator[str]: ...

    async def generate_response(
        self,
        messages: list[ChatMessage],
        system_prompt: str,
        options: dict | None = None,
    ) -> str: ...


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------


@runtime_checkable
class TTSProvider(Protocol):
    def synthesize_chunk(self, text: str, voice: str) -> AsyncIterator[bytes]: ...
    def stream_audio(self, text_stream: AsyncIterator[str]) -> AsyncIterator[bytes]: ...
    async def cancel(self, session_id: str) -> None: ...


# ---------------------------------------------------------------------------
# Realtime (Gemini Live API — native audio-in/audio-out, Uzbek supported)
# ---------------------------------------------------------------------------


@runtime_checkable
class RealtimeProvider(Protocol):
    async def connect(self, session_id: str) -> None: ...
    async def send_audio_chunk(self, chunk: bytes) -> None: ...
    def receive_events(self) -> AsyncIterator[dict]: ...
    async def interrupt(self) -> None: ...
    async def close(self) -> None: ...
