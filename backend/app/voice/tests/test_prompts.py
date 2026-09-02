from app.config import Settings
from app.voice.pipeline.prompts import (
    AGRICULTURE_SYSTEM_PROMPT_UZ,
    TOOL_INSTRUCTIONS_UZ,
    load_system_prompt,
)


def _settings(tmp_path, *, text=None, enable=True) -> Settings:
    """Settings pointing at a temp prompt file; text=None => file absent."""
    path = tmp_path / "voice_agent.md"
    if text is not None:
        path.write_text(text, encoding="utf-8")
    return Settings(voice_agent_prompt_path=str(path), enable_case_tools=enable)


def test_file_content_overrides_constant(tmp_path):
    s = _settings(tmp_path, text="Boshqa prompt", enable=False)
    assert load_system_prompt(s) == "Boshqa prompt"


def test_falls_back_to_constant_when_file_missing(tmp_path):
    s = _settings(tmp_path, text=None, enable=False)
    assert load_system_prompt(s) == AGRICULTURE_SYSTEM_PROMPT_UZ


def test_empty_file_falls_back_to_constant(tmp_path):
    s = _settings(tmp_path, text="   \n", enable=False)
    assert load_system_prompt(s) == AGRICULTURE_SYSTEM_PROMPT_UZ


def test_tool_policy_appended_when_enabled(tmp_path):
    s = _settings(tmp_path, text="Baza prompt", enable=True)
    assert load_system_prompt(s) == "Baza prompt" + "\n\n" + TOOL_INSTRUCTIONS_UZ


def test_marker_suppresses_append(tmp_path):
    body = "Baza prompt <!-- tools-included --> qolgan qism"
    s = _settings(tmp_path, text=body, enable=True)
    assert load_system_prompt(s) == body


def test_disabled_never_appends_policy(tmp_path):
    # Acceptance: tools OFF => byte-identical to the legacy constant path.
    s = _settings(tmp_path, text=None, enable=False)
    out = load_system_prompt(s)
    assert out == AGRICULTURE_SYSTEM_PROMPT_UZ
    assert TOOL_INSTRUCTIONS_UZ not in out


def test_identity_is_generic_ai_agronomist():
    # The agent must present itself as a generic AI agronomist — no given name
    # ("Rais") and no company ("Growz") in the constant or the shipped prompt file.
    assert "agronom" in AGRICULTURE_SYSTEM_PROMPT_UZ.lower()
    assert "Rais" not in AGRICULTURE_SYSTEM_PROMPT_UZ
    assert "Growz" not in AGRICULTURE_SYSTEM_PROMPT_UZ
    from pathlib import Path
    shipped = Path(__file__).resolve().parents[3] / "prompts" / "voice_agent.md"
    text = shipped.read_text(encoding="utf-8")
    assert "agronom" in text.lower()
    assert "Rais" not in text
    assert "Growz" not in text
