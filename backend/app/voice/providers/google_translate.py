"""Optional translation bridge via Gemini.

Unlike YandexGPT, Gemini handles Uzbek directly, so the Russian bridge defaults
OFF (``VOICE_USE_RUSSIAN_BRIDGE=false``). This provider is only constructed when
the bridge is explicitly enabled; it reuses the Gemini client for a cheap
sentence-level translation instead of pulling in the Cloud Translation API.
"""
from __future__ import annotations

from app.config import Settings
from app.voice.providers.google_auth import GoogleAuth


class GoogleTranslateProvider:
    def __init__(self, settings: Settings, auth: GoogleAuth) -> None:
        self._s = settings
        self._auth = auth
        self._client = None

    async def one(self, text: str, source: str, target: str) -> str:
        if not text.strip():
            return text
        from google.genai import types

        if self._client is None:
            self._client = self._auth.genai_client()
        prompt = (
            f"Translate the following text from {source} to {target}. "
            f"Return ONLY the translation, no extra words.\n\n{text}"
        )
        resp = await self._client.aio.models.generate_content(
            model=self._s.gemini_model,
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
            config=types.GenerateContentConfig(temperature=0.0),
        )
        return (resp.text or text).strip()
