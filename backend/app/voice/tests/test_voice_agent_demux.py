import base64
import json

from app.voice.pipeline import voice_agent


class _FakeSession:
    """Records every call the demux loop makes (no network, no SDK)."""

    def __init__(self) -> None:
        self.calls: list = []

    def set_input_sample_rate(self, sr):
        self.calls.append(("sr", sr))

    def set_voice(self, v):
        self.calls.append(("voice", v))

    async def start(self):
        self.calls.append(("start",))

    async def on_audio_chunk(self, data):
        self.calls.append(("audio", data))

    async def on_user_interrupt(self):
        self.calls.append(("interrupt",))

    async def on_user_text(self, text):
        self.calls.append(("user_text", text))

    async def on_photo(self, photo_id, data, mime, target_part):
        self.calls.append(("photo", photo_id, data, mime, target_part))

    async def on_photo_quality(self, status, reason, target_part):
        self.calls.append(("quality", status, reason, target_part))

    async def on_camera_cancelled(self):
        self.calls.append(("cancel",))

    async def close(self):
        self.calls.append(("close",))


class _FakeWebSocket:
    """Yields queued starlette-shaped frames, then disconnects."""

    def __init__(self, messages) -> None:
        self._messages = list(messages)

    async def receive(self):
        if self._messages:
            return self._messages.pop(0)
        return {"type": "websocket.disconnect"}


def _text_frame(event: dict) -> dict:
    return {"type": "websocket.receive", "text": json.dumps(event)}


async def _run(monkeypatch, events) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket(
        [_text_frame({"type": "session.start"})]
        + [_text_frame(e) for e in events]
        + [_text_frame({"type": "session.end"})]
    )
    await voice_agent.run_voice_agent(ws, settings=None, session_id="t")
    return session


async def test_photo_upload_decodes_and_dispatches(monkeypatch):
    raw = b"\x89PNG\r\nphoto-bytes"
    session = await _run(monkeypatch, [{
        "type": "photo.upload",
        "data": base64.b64encode(raw).decode(),
        "mime": "image/png",
        "photo_id": "p1",
        "target_part": "leaf",
    }])
    photo_calls = [c for c in session.calls if c[0] == "photo"]
    assert photo_calls == [("photo", "p1", raw, "image/png", "leaf")]


async def test_photo_quality_and_camera_cancelled_dispatch(monkeypatch):
    session = await _run(monkeypatch, [
        {"type": "photo.quality", "status": "bad", "reason": "blur", "target_part": "leaf"},
        {"type": "camera.cancelled"},
    ])
    assert ("quality", "bad", "blur", "leaf") in session.calls
    assert ("cancel",) in session.calls


async def test_session_always_closed(monkeypatch):
    session = await _run(monkeypatch, [])
    assert ("start",) in session.calls
    assert session.calls[-1] == ("close",)


# ---- Per-farmer memory wiring ----------------------------------------------

from app.config import Settings  # noqa: E402

DEV = "11111111-2222-3333-4444-555555555555"


class _FakeMemorySession(_FakeSession):
    """A session that also supports the memory hooks."""

    def set_memory(self, block):
        self.calls.append(("set_memory", block))

    async def speak_system(self, text):
        self.calls.append(("speak_system", text))

    def transcript_text(self):
        return ""


async def _run_memory(monkeypatch, tmp_path, start_event, *, enabled=True,
                      session=None):
    session = session or _FakeMemorySession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    # enrich_enabled=False keeps these tests purely about memory (enrichment is
    # exercised separately and would otherwise add its own set_memory call).
    settings = Settings(
        memory_enabled=enabled, memory_dir=str(tmp_path / "memory"),
        enrich_enabled=False,
    )
    ws = _FakeWebSocket(
        [_text_frame(start_event), _text_frame({"type": "session.end"})]
    )
    await voice_agent.run_voice_agent(ws, settings=settings, session_id="t")
    return session


async def test_user_id_triggers_memory_kickoff(monkeypatch, tmp_path):
    session = await _run_memory(
        monkeypatch, tmp_path, {"type": "session.start", "user_id": DEV}
    )
    kinds = [c[0] for c in session.calls]
    assert "set_memory" in kinds
    assert "speak_system" in kinds
    # New farmer → onboarding block + intro kickoff, injected BEFORE start.
    block = next(c[1] for c in session.calls if c[0] == "set_memory")
    assert "[YANGI FERMER]" in block
    assert kinds.index("set_memory") < kinds.index("start")
    assert kinds.index("start") < kinds.index("speak_system")


async def test_no_user_id_stays_memoryless(monkeypatch, tmp_path):
    session = await _run_memory(monkeypatch, tmp_path, {"type": "session.start"})
    kinds = [c[0] for c in session.calls]
    assert "set_memory" not in kinds and "speak_system" not in kinds


async def test_invalid_user_id_ignored(monkeypatch, tmp_path):
    session = await _run_memory(
        monkeypatch, tmp_path,
        {"type": "session.start", "user_id": "../../etc/passwd"},
    )
    assert "set_memory" not in [c[0] for c in session.calls]


async def test_memory_disabled_stays_memoryless(monkeypatch, tmp_path):
    session = await _run_memory(
        monkeypatch, tmp_path, {"type": "session.start", "user_id": DEV},
        enabled=False,
    )
    assert "set_memory" not in [c[0] for c in session.calls]


async def test_session_without_memory_hooks_unaffected(monkeypatch, tmp_path):
    # A session lacking set_memory (e.g. staged path) must run untouched.
    session = await _run_memory(
        monkeypatch, tmp_path, {"type": "session.start", "user_id": DEV},
        session=_FakeSession(),
    )
    assert ("start",) in session.calls
    assert session.calls[-1] == ("close",)


async def test_text_input_dispatches_trimmed_and_capped(monkeypatch):
    session = await _run(monkeypatch, [
        {"type": "text.input", "value": "  Salom, pomidorim kasal  "},
    ])
    assert ("user_text", "Salom, pomidorim kasal") in session.calls


# ---- Conversation enrichment wiring ----------------------------------------


async def _run_enrich(monkeypatch, start_event, *, enabled=True):
    """Run one session.start with enrichment on, weather OFF (no network)."""
    session = _FakeMemorySession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    settings = Settings(
        memory_enabled=False, enrich_enabled=enabled,
        enrich_weather_enabled=False,  # date + crop only → no HTTP
    )
    ws = _FakeWebSocket(
        [_text_frame(start_event), _text_frame({"type": "session.end"})]
    )
    await voice_agent.run_voice_agent(ws, settings=settings, session_id="t")
    return session


async def test_enrichment_injects_crop_and_date_before_start(monkeypatch):
    session = await _run_enrich(
        monkeypatch,
        {"type": "session.start", "crop_name": "Pomidor", "crop_id": "u-1"},
    )
    kinds = [c[0] for c in session.calls]
    block = next(c[1] for c in session.calls if c[0] == "set_memory")
    assert "[BUGUNGI SHAROIT]" in block
    assert "Pomidor" in block
    # Enrichment must be injected BEFORE the Live connection opens.
    assert kinds.index("set_memory") < kinds.index("start")


async def test_enrichment_runs_without_user_id_or_crop(monkeypatch):
    # Unlike memory, enrichment fires even with no user_id and no crop (date-only).
    session = await _run_enrich(monkeypatch, {"type": "session.start"})
    block = next(c[1] for c in session.calls if c[0] == "set_memory")
    assert "[BUGUNGI SHAROIT]" in block


async def test_enrichment_disabled_injects_nothing(monkeypatch):
    session = await _run_enrich(
        monkeypatch, {"type": "session.start", "crop_name": "Bodring"},
        enabled=False,
    )
    assert "set_memory" not in [c[0] for c in session.calls]


async def test_enrichment_failure_never_blocks_start(monkeypatch):
    # If enrichment raises, the session must still start + close cleanly.
    async def _boom(*a, **k):
        raise RuntimeError("weather down")

    monkeypatch.setattr(voice_agent, "build_session_enrichment", _boom)
    session = _FakeMemorySession()
    monkeypatch.setattr(voice_agent, "_build_session", lambda ws, s, sid: session)
    ws = _FakeWebSocket([
        _text_frame({"type": "session.start", "crop_name": "Olma"}),
        _text_frame({"type": "session.end"}),
    ])
    await voice_agent.run_voice_agent(
        ws, settings=Settings(memory_enabled=False, enrich_enabled=True),
        session_id="t",
    )
    assert ("start",) in session.calls
    assert session.calls[-1] == ("close",)


async def test_text_input_empty_ignored(monkeypatch):
    session = await _run(monkeypatch, [
        {"type": "text.input", "value": "   "},
        {"type": "text.input"},
    ])
    assert not [c for c in session.calls if c[0] == "user_text"]


async def test_text_input_long_message_truncated(monkeypatch):
    session = await _run(monkeypatch, [
        {"type": "text.input", "value": "x" * 900},
    ])
    sent = [c for c in session.calls if c[0] == "user_text"]
    assert sent and len(sent[0][1]) == 500


def test_is_remote_close_classifier():
    from app.voice.providers.gemini_live import _is_remote_close

    class ConnectionClosedError(Exception):
        pass

    assert _is_remote_close(ConnectionClosedError("received 1011 (internal error)"))
    assert _is_remote_close(RuntimeError("Deadline expired before operation could complete"))
    assert _is_remote_close(RuntimeError("server sent GoAway"))
    assert not _is_remote_close(ValueError("some unrelated failure"))
    assert not _is_remote_close(KeyError("voice"))
