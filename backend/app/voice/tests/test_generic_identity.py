"""RED tests: the agent must present itself as a generic AI agronomist.

The target identity is "an AI agronomist" — no given name ("Rais") and no
company attribution ("Growz") in any user-facing prompt constant or greeting.

Scope note: we assert only on the IDENTITY constants (prompts + onboarding
kickoff). We deliberately do NOT assert on:
  - the transcript role label "RAIS:" / "rais" (chat-log infra, stored data)
  - GROWZ_API_URL / x-api-key / crop-catalogue code (real API infrastructure)
These tests will fail RED until the fix is applied to prompts.py and memory.py.
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.voice.pipeline.prompts import (
    AGRICULTURE_SYSTEM_PROMPT_EN,
    AGRICULTURE_SYSTEM_PROMPT_UZ,
    CYRILLIC_REPLY_DIRECTIVE,
    ENGLISH_GREETING_KICKOFF,
    load_system_prompt,
)
from app.voice.pipeline import memory


# ---------------------------------------------------------------------------
# 1. English system prompt — generic identity required
# ---------------------------------------------------------------------------

def test_en_prompt_contains_ai_agronomist():
    """AGRICULTURE_SYSTEM_PROMPT_EN must describe the agent as an AI agronomist."""
    assert "ai agronomist" in AGRICULTURE_SYSTEM_PROMPT_EN.lower(), (
        "AGRICULTURE_SYSTEM_PROMPT_EN must contain 'AI agronomist' (case-insensitive); "
        f"got: {AGRICULTURE_SYSTEM_PROMPT_EN!r}"
    )


def test_en_prompt_no_rais():
    """AGRICULTURE_SYSTEM_PROMPT_EN must not contain the brand name 'Rais'."""
    assert "Rais" not in AGRICULTURE_SYSTEM_PROMPT_EN, (
        "AGRICULTURE_SYSTEM_PROMPT_EN must not contain 'Rais'; "
        f"got: {AGRICULTURE_SYSTEM_PROMPT_EN!r}"
    )


def test_en_prompt_no_growz():
    """AGRICULTURE_SYSTEM_PROMPT_EN must not contain the company name 'Growz'."""
    assert "Growz" not in AGRICULTURE_SYSTEM_PROMPT_EN, (
        "AGRICULTURE_SYSTEM_PROMPT_EN must not contain 'Growz'; "
        f"got: {AGRICULTURE_SYSTEM_PROMPT_EN!r}"
    )


# ---------------------------------------------------------------------------
# 2. English greeting kickoff — generic identity required
# ---------------------------------------------------------------------------

def test_en_greeting_no_rais():
    """ENGLISH_GREETING_KICKOFF must not introduce the agent as 'Rais'."""
    assert "Rais" not in ENGLISH_GREETING_KICKOFF, (
        "ENGLISH_GREETING_KICKOFF must not contain 'Rais'; "
        f"got: {ENGLISH_GREETING_KICKOFF!r}"
    )


def test_en_greeting_no_growz():
    """ENGLISH_GREETING_KICKOFF must not mention 'Growz'."""
    assert "Growz" not in ENGLISH_GREETING_KICKOFF, (
        "ENGLISH_GREETING_KICKOFF must not contain 'Growz'; "
        f"got: {ENGLISH_GREETING_KICKOFF!r}"
    )


def test_en_greeting_still_has_agronomist():
    """ENGLISH_GREETING_KICKOFF must still reference 'agronomist' (generic role)."""
    assert "agronomist" in ENGLISH_GREETING_KICKOFF.lower(), (
        "ENGLISH_GREETING_KICKOFF must still contain 'agronomist'; "
        f"got: {ENGLISH_GREETING_KICKOFF!r}"
    )


# ---------------------------------------------------------------------------
# 3. Uzbek system prompt — generic identity required
# ---------------------------------------------------------------------------

def test_uz_prompt_no_rais():
    """AGRICULTURE_SYSTEM_PROMPT_UZ must not contain the name 'Rais'."""
    assert "Rais" not in AGRICULTURE_SYSTEM_PROMPT_UZ, (
        "AGRICULTURE_SYSTEM_PROMPT_UZ must not contain 'Rais'; "
        f"got: {AGRICULTURE_SYSTEM_PROMPT_UZ!r}"
    )


def test_uz_prompt_no_growz():
    """AGRICULTURE_SYSTEM_PROMPT_UZ must not contain 'Growz'."""
    assert "Growz" not in AGRICULTURE_SYSTEM_PROMPT_UZ, (
        "AGRICULTURE_SYSTEM_PROMPT_UZ must not contain 'Growz'; "
        f"got: {AGRICULTURE_SYSTEM_PROMPT_UZ!r}"
    )


def test_uz_prompt_still_has_agronom():
    """AGRICULTURE_SYSTEM_PROMPT_UZ must still contain the Uzbek word 'agronom'."""
    assert "agronom" in AGRICULTURE_SYSTEM_PROMPT_UZ.lower(), (
        "AGRICULTURE_SYSTEM_PROMPT_UZ must still contain 'agronom' (Uzbek for agronomist); "
        f"got: {AGRICULTURE_SYSTEM_PROMPT_UZ!r}"
    )


# ---------------------------------------------------------------------------
# 4. memory._ONBOARDING_KICKOFF — generic identity required
# ---------------------------------------------------------------------------

def test_onboarding_kickoff_no_rais():
    """memory._ONBOARDING_KICKOFF must not introduce the agent as 'Rais'."""
    assert "Rais" not in memory._ONBOARDING_KICKOFF, (
        "memory._ONBOARDING_KICKOFF must not contain 'Rais'; "
        f"got: {memory._ONBOARDING_KICKOFF!r}"
    )


def test_onboarding_kickoff_no_growz():
    """memory._ONBOARDING_KICKOFF must not mention 'Growz'."""
    assert "Growz" not in memory._ONBOARDING_KICKOFF, (
        "memory._ONBOARDING_KICKOFF must not contain 'Growz'; "
        f"got: {memory._ONBOARDING_KICKOFF!r}"
    )


# ---------------------------------------------------------------------------
# 5. load_system_prompt(en-US) full output — no brand name, no company
# ---------------------------------------------------------------------------

def test_load_system_prompt_en_no_rais(tmp_path):
    """load_system_prompt with agent_language='en-US' must produce no 'Rais'."""
    s = Settings(
        agent_language="en-US",
        enable_case_tools=False,
        voice_agent_prompt_path=str(tmp_path / "absent.md"),
    )
    result = load_system_prompt(s)
    assert "Rais" not in result, (
        "load_system_prompt(en-US) full output must not contain 'Rais'; "
        f"got: {result!r}"
    )


def test_load_system_prompt_en_no_growz(tmp_path):
    """load_system_prompt with agent_language='en-US' must produce no 'Growz'."""
    s = Settings(
        agent_language="en-US",
        enable_case_tools=False,
        voice_agent_prompt_path=str(tmp_path / "absent.md"),
    )
    result = load_system_prompt(s)
    assert "Growz" not in result, (
        "load_system_prompt(en-US) full output must not contain 'Growz'; "
        f"got: {result!r}"
    )


def test_cyrillic_directive_no_rais():
    """The uz-Cyrl reply directive's example intro must not name the brand
    (Cyrillic "Раис") or the company."""
    assert "Раис" not in CYRILLIC_REPLY_DIRECTIVE
    assert "Growz" not in CYRILLIC_REPLY_DIRECTIVE
    assert "агроном" in CYRILLIC_REPLY_DIRECTIVE
