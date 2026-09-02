"""RED test: when AGENT_LANGUAGE=en-US the agent must speak a greeting FIRST.

The bug (verified in voice_agent.py ~line 361, ~line 434): the `is_english`
guard skips the memory block entirely, leaving `mem_store is None` and
`mem_kickoff = ""`.  After `session.start()` the code reaches:

    elif mem_store is not None and mem_kickoff:
        await session.speak_system(mem_kickoff)

Both conditions are False in English mode, so `speak_system` is never called
and the agent stays silent — waiting for the user to speak first.

The requirement: in English mode, `session.speak_system` must be called ONCE
with a non-empty English greeting string AFTER `session.start()` and BEFORE
any user audio or text is received.

This test is deliberately left failing (RED): do NOT implement the fix here.
"""
from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.voice.pipeline import voice_agent


# ---------------------------------------------------------------------------
# Minimal fake session — mirrors _FakeMemorySession from test_voice_agent_demux
# ---------------------------------------------------------------------------

class _FakeEnglishSession:
    """Records every hook the orchestrator makes, including speak_system."""

    def __init__(self) -> None:
        self.calls: list = []

    def set_input_sample_rate(self, sr):
        self.calls.append(("sr", sr))

    def set_voice(self, v):
        self.calls.append(("voice", v))

    def set_memory(self, block):
        self.calls.append(("set_memory", block))

    async def start(self):
        self.calls.append(("start",))

    async def speak_system(self, text):
        self.calls.append(("speak_system", text))

    async def on_audio_chunk(self, data):
        self.calls.append(("audio", data))

    async def on_user_interrupt(self):
        self.calls.append(("interrupt",))

    async def on_user_text(self, text):
        self.calls.append(("user_text", text))

    async def on_photo(self, photo_id, data, mime, target_part):
        self.calls.append(("photo", photo_id, data, mime, target_part))
        return True

    async def on_camera_cancelled(self):
        self.calls.append(("cancel",))

    def transcript_text(self):
        return ""

    async def close(self):
        self.calls.append(("close",))


class _FakeWebSocket:
    """Yields queued starlette-shaped frames, then disconnects."""

    def __init__(self, messages) -> None:
        self._messages = list(messages)
        self.sent_json: list[dict] = []

    async def receive(self):
        if self._messages:
            return self._messages.pop(0)
        return {"type": "websocket.disconnect"}

    async def send_json(self, payload: dict) -> None:
        self.sent_json.append(payload)

    async def send_bytes(self, data: bytes) -> None:
        pass


def _text_frame(event: dict) -> dict:
    return {"type": "websocket.receive", "text": json.dumps(event)}


async def _run_english(monkeypatch, *, session=None) -> _FakeEnglishSession:
    """Run one chat.start through the orchestrator in English (en-US) mode.

    Memory and enrichment are both disabled (irrelevant to this test);
    no chat_id so the guide flow is also not active.  This is the bare
    English-mode chatless session — exactly the scenario that must speak first.
    """
    if session is None:
        session = _FakeEnglishSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)

    settings = Settings(
        agent_language="en-US",
        memory_enabled=False,
        enrich_enabled=False,
        use_gemini_live_audio=True,
        enable_case_tools=False,
    )

    ws = _FakeWebSocket([_text_frame({"type": "chat.start"})])
    await voice_agent.run_voice_agent(ws, settings=settings, session_id="t")
    return session


# ---------------------------------------------------------------------------
# RED test — this MUST fail until the fix is implemented
# ---------------------------------------------------------------------------

async def test_english_mode_speaks_greeting_before_user_input(monkeypatch):
    """In English mode (agent_language='en-US'), the orchestrator must call
    session.speak_system with a non-empty English greeting after session.start()
    and before any user audio or text is received.

    Failure expected: speak_system called 0 times because the is_english guard
    at ~line 361 of voice_agent.py skips the memory block, leaving mem_store=None
    and the elif branch at ~line 434 never executes.
    """
    session = await _run_english(monkeypatch)

    # Collect all speak_system calls that happened AFTER start()
    kinds = [c[0] for c in session.calls]
    assert "start" in kinds, "session.start() was never called — harness broken"

    start_index = kinds.index("start")
    speak_calls_after_start = [
        c for i, c in enumerate(session.calls)
        if c[0] == "speak_system" and i > start_index
    ]

    # The primary assertion: speak_system must have been called at least once
    # with a non-empty string — the agent must speak a greeting first.
    assert len(speak_calls_after_start) >= 1, (
        f"Expected session.speak_system() to be called at least once after start() "
        f"in English mode, but speak_system was never called.\n"
        f"All session calls: {session.calls!r}"
    )

    greeting_text = speak_calls_after_start[0][1]

    # The greeting must not be empty.
    assert greeting_text.strip(), (
        f"speak_system was called but with an empty string: {greeting_text!r}"
    )

    # The greeting must be in English (not Uzbek).  We check for the absence of
    # common Uzbek words and presence of at least one ASCII word — English
    # greetings will always contain recognisable ASCII words.
    uzbek_markers = ["Salom", "Assalomu", "fermer", "qanday", "yordam"]
    for marker in uzbek_markers:
        assert marker not in greeting_text, (
            f"Greeting in English mode must not contain Uzbek text {marker!r}; "
            f"got: {greeting_text!r}"
        )

    # Must contain at least one English word (case-insensitive).
    english_markers = ["Hello", "Hi", "Good", "Welcome", "I'm", "I am", "help",
                       "plant", "How", "What", "agronomist"]
    assert any(m.lower() in greeting_text.lower() for m in english_markers), (
        f"Greeting must contain recognisable English words; got: {greeting_text!r}"
    )
