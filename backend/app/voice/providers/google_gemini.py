"""Gemini text provider — streaming via the ``google-genai`` SDK.

Replaces YandexGPT in the staged pipeline. Uses
``client.aio.models.generate_content_stream`` which yields **incremental**
chunks (unlike YandexGPT's cumulative payloads), so we forward ``chunk.text``
straight through to the sentence chunker.

Gemini's chat roles are ``user`` / ``model`` (no ``assistant``); the system
prompt is passed out-of-band as ``system_instruction``. Uzbek is handled
natively, so no Russian bridge is needed by default.
"""
from __future__ import annotations

from typing import AsyncIterator

from app.config import Settings
from app.voice.providers.base import ChatMessage
from app.voice.providers.google_auth import GoogleAuth


class GeminiProvider:
    def __init__(self, settings: Settings, auth: GoogleAuth) -> None:
        self._s = settings
        self._auth = auth
        self._client = None

    def _client_lazy(self):
        if self._client is None:
            self._client = self._auth.genai_client()
        return self._client

    def _contents(self, messages: list[ChatMessage]) -> list:
        from google.genai import types

        contents = []
        for m in messages:
            if m.role == "system":
                continue  # carried via system_instruction
            role = "model" if m.role == "assistant" else "user"
            contents.append(
                types.Content(role=role, parts=[types.Part(text=m.text)])
            )
        return contents

    def _gen_config(self, system_prompt: str, options: dict | None):
        from google.genai import types

        opts = options or {}
        return types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=opts.get("temperature", self._s.gemini_temperature),
            max_output_tokens=opts.get("max_tokens", self._s.gemini_max_tokens),
            # Disable thinking: for short voice replies a thinking budget would eat
            # the max_output_tokens (truncating the answer) and add latency.
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

    async def stream_response(
        self,
        messages: list[ChatMessage],
        system_prompt: str,
        options: dict | None = None,
    ) -> AsyncIterator[str]:
        """Yield incremental token deltas as Gemini generates."""
        client = self._client_lazy()
        stream = await client.aio.models.generate_content_stream(
            model=self._s.gemini_model,
            contents=self._contents(messages),
            config=self._gen_config(system_prompt, options),
        )
        async for chunk in stream:
            text = getattr(chunk, "text", None)
            if text:
                yield text

    async def generate_response(
        self,
        messages: list[ChatMessage],
        system_prompt: str,
        options: dict | None = None,
    ) -> str:
        """Non-streamed convenience for tests / benchmarks."""
        client = self._client_lazy()
        resp = await client.aio.models.generate_content(
            model=self._s.gemini_model,
            contents=self._contents(messages),
            config=self._gen_config(system_prompt, options),
        )
        return resp.text or ""
