"""Subtitle STT — Google Chirp 3 (Cloud Speech v2).

Dual-channel design: Gemini Live stays Alomat's ears; Chirp 3 re-transcribes
each finished push-to-talk turn purely for the subtitle bubble, the stored
transcript and memory extraction (it never feeds back into the conversation).
Chirp 3 was the most accurate of the tested engines on Uzbek farmer speech.

Two output filters fix failure modes seen in the field: :func:`to_latin_uz`
(Cyrillic output → Latin, the app is Latin-script) and :func:`plausible`
(reject a transcript that shares no words with what Gemini heard — a total
disagreement means the STT hallucinated; keep the rough line instead).
"""
from __future__ import annotations

import logging
import re

from app.config import Settings

logger = logging.getLogger("voice.subtitle_stt")


# ---- shared output filters --------------------------------------------------

# Uzbek Cyrillic -> Latin (2021 official alphabet flavor: oʻ/gʻ with ʻ okina).
_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "s", "ч": "ch", "ш": "sh", "щ": "sh",
    "ъ": "ʼ", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ў": "oʻ", "қ": "q", "ғ": "gʻ", "ҳ": "h",
}


def to_latin_uz(text: str) -> str:
    """Transliterate Uzbek Cyrillic to Latin; Latin input passes through."""
    if not re.search(r"[а-яёўқғҳА-ЯЁЎҚҒҲ]", text):
        return text
    out: list[str] = []
    for ch in text:
        lower = ch.lower()
        mapped = _CYR.get(lower)
        if mapped is None:
            out.append(ch)
            continue
        if ch != lower and mapped:  # keep capitalization (Ё -> Yo)
            mapped = mapped[0].upper() + mapped[1:]
        out.append(mapped)
    return "".join(out)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) >= 3}


def plausible(corrected: str, rough: str) -> bool:
    """Hallucination guard: both texts transcribe the SAME audio, so total
    word disagreement means one of them invented content — and Alomat's reply
    followed the rough hearing, so we keep that. Short/empty rough lines carry
    no signal and never veto."""
    rough_words = _words(rough)
    if len(rough_words) < 2:
        return True
    return bool(rough_words & _words(corrected))


# ---- provider ---------------------------------------------------------------

class GoogleChirpSTT:
    """Cloud Speech v2 chirp_3 one-shot (mirrors benchmark/poc_stt_uz.py).

    chirp_3 is served only from the multi-region endpoints (settings default
    region ``eu`` -> ``eu-speech.googleapis.com``). Needs a service-account
    JSON via GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_PROJECT_ID.
    """

    def __init__(self, settings: Settings, auth) -> None:
        self._s = settings
        self._auth = auth
        self._client = None

    async def transcribe_pcm(self, pcm: bytes, sample_rate: int) -> str:
        from google.cloud.speech_v2.types import cloud_speech

        if self._client is None:
            self._client = self._auth.speech_client()
        config = cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=sample_rate,
                audio_channel_count=1,
            ),
            language_codes=[self._s.effective_stt_language],
            model=self._s.google_stt_model,
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=True
            ),
        )
        response = await self._client.recognize(
            request=cloud_speech.RecognizeRequest(
                recognizer=self._s.stt_recognizer_path,
                config=config,
                content=pcm,
            )
        )
        parts = [
            r.alternatives[0].transcript
            for r in response.results
            if r.alternatives
        ]
        return " ".join(p.strip() for p in parts if p).strip()

    async def aclose(self) -> None:
        self._client = None


def build_subtitle_stt(settings: Settings, auth):
    """The subtitle scribe for a session: Google Chirp 3 when its credentials
    are configured, else None (fail open to Gemini's built-in transcription)."""
    if settings.google_project_id and auth is not None:
        return GoogleChirpSTT(settings, auth)
    logger.info("Google STT credentials absent — built-in transcription only")
    return None
