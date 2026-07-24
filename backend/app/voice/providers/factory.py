"""Provider factory — single place to construct Google providers from settings.

The pipeline asks the factory for providers and never imports concrete provider
classes directly, so the speech-out strategy (Gemini Live vs external TTS) can be
swapped here without touching pipeline code.
"""
from __future__ import annotations

from app.config import Settings
from app.voice.providers.gemini_tts import GeminiTTSProvider
from app.voice.providers.google_auth import GoogleAuth
from app.voice.providers.google_gemini import GeminiProvider
from app.voice.providers.google_stt import GoogleSTTProvider
from app.voice.providers.google_translate import GoogleTranslateProvider


def build_auth(settings: Settings) -> GoogleAuth:
    return GoogleAuth(settings)


def build_stt(settings: Settings, auth: GoogleAuth) -> GoogleSTTProvider:
    return GoogleSTTProvider(settings, auth)


def build_gpt(settings: Settings, auth: GoogleAuth) -> GeminiProvider:
    return GeminiProvider(settings, auth)


def build_tts(settings: Settings, auth: GoogleAuth) -> GeminiTTSProvider:
    return GeminiTTSProvider(settings, auth)


def build_translate(
    settings: Settings, auth: GoogleAuth
) -> GoogleTranslateProvider | None:
    # Gemini handles Uzbek directly; the bridge is only built when enabled.
    if not settings.voice_use_russian_bridge:
        return None
    return GoogleTranslateProvider(settings, auth)
