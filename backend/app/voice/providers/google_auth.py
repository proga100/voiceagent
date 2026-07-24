"""Google auth/clients — one place to construct the genai + Speech clients.

Two credential surfaces:
  * **Gemini** (text + Live API) via ``google-genai``: an API key
    (``GEMINI_API_KEY``) for the Developer API, or ADC/Vertex when
    ``GOOGLE_GENAI_USE_VERTEXAI=true``.
  * **Cloud Speech-to-Text v2** via ``google-cloud-speech``: Application Default
    Credentials (service-account JSON pointed to by
    ``GOOGLE_APPLICATION_CREDENTIALS``).

This mirrors the Yandex ``*_auth`` seam so providers stay auth-agnostic.
"""
from __future__ import annotations

import os

from app.config import Settings


class AuthError(RuntimeError):
    pass


class GoogleAuth:
    def __init__(self, settings: Settings) -> None:
        self._s = settings
        # Make ADC discoverable to every google client in this process.
        if settings.google_application_credentials:
            os.environ.setdefault(
                "GOOGLE_APPLICATION_CREDENTIALS",
                settings.google_application_credentials,
            )

    def genai_client(self):
        """A ``google.genai.Client`` for Gemini text and the Live API."""
        from google import genai  # imported lazily so unit tests need no SDK

        s = self._s
        if s.google_genai_use_vertexai:
            if not s.google_project_id:
                raise AuthError(
                    "GOOGLE_GENAI_USE_VERTEXAI=true requires GOOGLE_PROJECT_ID"
                )
            return genai.Client(
                vertexai=True,
                project=s.google_project_id,
                location=s.google_stt_region,
            )
        if not s.gemini_api_key:
            raise AuthError(
                "GEMINI_API_KEY is required (or set GOOGLE_GENAI_USE_VERTEXAI=true "
                "with ADC + GOOGLE_PROJECT_ID)"
            )
        return genai.Client(api_key=s.gemini_api_key)

    def speech_client(self):
        """An async Cloud Speech-to-Text v2 client bound to the STT region."""
        from google.api_core.client_options import ClientOptions
        from google.cloud.speech_v2 import SpeechAsyncClient

        if not self._s.google_project_id:
            raise AuthError("GOOGLE_PROJECT_ID is required for Speech-to-Text")
        return SpeechAsyncClient(
            client_options=ClientOptions(api_endpoint=self._s.stt_api_endpoint)
        )
