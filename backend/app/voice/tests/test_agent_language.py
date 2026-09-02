"""Tests for the AGENT_LANGUAGE config flag (uz-UZ default / en-US demo mode).

Covers: Settings.is_english, effective_live_language, effective_stt_language,
effective_live_voice (never "azure:"-prefixed in English mode), and
load_system_prompt() in English mode (English prompt, English tool policy with
farmer_language="en", and the on-disk Uzbek voice_agent_prompt_path ignored).
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.voice.pipeline.prompts import (
    AGRICULTURE_SYSTEM_PROMPT_UZ,
    load_system_prompt,
)


# ---------------------------------------------------------------------------
# 1. Default (uz-UZ) — all effective_* values must match the Uzbek/legacy path
# ---------------------------------------------------------------------------

def test_default_is_uzuz():
    """Settings() default: agent_language=='uz-UZ', is_english False,
    effective_* properties all mirror the existing Uzbek/legacy fields."""
    s = Settings()

    assert s.agent_language == "uz-UZ"
    assert s.is_english is False

    # effective language properties must equal the legacy fields unchanged
    assert s.effective_live_language == s.gemini_live_language
    assert s.effective_stt_language == s.google_stt_language

    # effective voice must equal the plain gemini_live_voice field
    assert s.effective_live_voice == s.gemini_live_voice


# ---------------------------------------------------------------------------
# 2. English mode flips both language codes to "en-US"
# ---------------------------------------------------------------------------

def test_english_flips_language_and_stt():
    """Settings(agent_language='en-US') returns 'en-US' for both
    effective_live_language and effective_stt_language."""
    s = Settings(agent_language="en-US")

    assert s.is_english is True
    assert s.effective_live_language == "en-US"
    assert s.effective_stt_language == "en-US"


# ---------------------------------------------------------------------------
# 3. English voice must NOT be an "azure:"-prefixed string
# ---------------------------------------------------------------------------

def test_english_voice_is_not_azure():
    """effective_live_voice in English mode is a bare Gemini voice name,
    never an 'azure:'-prefixed token."""
    s = Settings(agent_language="en-US")

    voice = s.effective_live_voice
    assert isinstance(voice, str) and voice, "effective_live_voice must be a non-empty string"
    assert not voice.startswith("azure:"), (
        f"effective_live_voice must not start with 'azure:' in English mode; got {voice!r}"
    )


# ---------------------------------------------------------------------------
# 4. English mode: load_system_prompt returns an English prompt with English
#    tool policy containing farmer_language="en"
# ---------------------------------------------------------------------------

def test_english_prompt_is_english_with_tools(tmp_path):
    """In English mode with enable_case_tools=True:
    - the returned prompt is not the Uzbek constant
    - it contains an English marker phrase
    - the appended tool policy contains farmer_language='en' or farmer_language="en"
    """
    # Point voice_agent_prompt_path at a non-existent path (no file on disk)
    s = Settings(
        agent_language="en-US",
        enable_case_tools=True,
        voice_agent_prompt_path=str(tmp_path / "no_such_file.md"),
    )

    # Guard: is_english must be True — raises AttributeError until the feature
    # is implemented, which is the correct RED failure reason for this test.
    assert s.is_english is True, "Settings(agent_language='en-US').is_english must be True"

    result = load_system_prompt(s)

    # Must NOT be the Uzbek constant
    assert result != AGRICULTURE_SYSTEM_PROMPT_UZ, (
        "English mode must not return the Uzbek constant AGRICULTURE_SYSTEM_PROMPT_UZ"
    )

    # Must contain an English marker — the English prompt should address farmers in English
    # Any of these substrings would be reasonable English markers:
    english_markers = ["farmer", "agronomist", "plant", "disease", "English", "en-US", "photo"]
    assert any(marker.lower() in result.lower() for marker in english_markers), (
        f"English prompt must contain at least one English marker phrase; got: {result[:200]!r}"
    )

    # Tool policy in English mode must include farmer_language="en" or farmer_language='en'
    assert ('farmer_language="en"' in result or "farmer_language='en'" in result), (
        "English tool policy must contain farmer_language=\"en\" or farmer_language='en'; "
        f"got: {result!r}"
    )


# ---------------------------------------------------------------------------
# 5. English mode ignores any on-disk Uzbek voice_agent_prompt_path file
# ---------------------------------------------------------------------------

def test_english_prompt_ignores_uzbek_file(tmp_path):
    """Even when voice_agent_prompt_path points at a real file containing
    Uzbek text, English mode ignores it and returns the English prompt."""
    uzbek_file = tmp_path / "uzbek_agent.md"
    uzbek_text = (
        "Sen Rais — Oʻzbekiston fermerlari uchun ovozli agronomsan. "
        "Faqat oʻzbek tilida gapir."
    )
    uzbek_file.write_text(uzbek_text, encoding="utf-8")

    s = Settings(
        agent_language="en-US",
        enable_case_tools=False,
        voice_agent_prompt_path=str(uzbek_file),
    )

    # Guard: is_english must be True — this raises AttributeError until the
    # feature is implemented, which is the correct RED failure reason.
    assert s.is_english is True, "Settings(agent_language='en-US').is_english must be True"

    result = load_system_prompt(s)

    # The Uzbek file content must NOT appear in the result
    assert uzbek_text not in result, (
        "English mode must ignore the on-disk Uzbek prompt file; "
        f"got result starting with: {result[:200]!r}"
    )

    # Must NOT be the Uzbek constant either
    assert result != AGRICULTURE_SYSTEM_PROMPT_UZ, (
        "English mode must not return the Uzbek constant AGRICULTURE_SYSTEM_PROMPT_UZ"
    )
