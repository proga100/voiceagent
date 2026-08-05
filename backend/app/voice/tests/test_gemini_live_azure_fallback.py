"""Azure TTS -> Gemini voice fallback (backend contract).

When Azure Neural TTS stops working mid-session (revoked key, quota, outage)
the agent must keep TALKING rather than typing into a silent chat. The switch
is cheap: the Live socket is already connected with the AUDIO modality, so
Azure mode is really "generate Gemini audio and discard it" — clearing
``_azure_mode`` is enough to start forwarding it.

Only the Azure provider is faked here; the queue, the speak loop and the
receive loop are the real ones, so the test pins the actual state machine
(who owes ``tts.finished``, when the error event is suppressed).
"""
import asyncio

from app.config import Settings
from app.voice.providers.gemini_live import GeminiLiveSession


class FakeResponseObj:
    """Stand-in for an httpx response carrying just the status code."""

    def __init__(self, status_code):
        self.status_code = status_code


class FakeHTTPStatusError(Exception):
    """Mirrors httpx.HTTPStatusError's shape: ``.response.status_code``."""

    def __init__(self, status_code):
        super().__init__(f"Client error '{status_code}' for url '...'")
        self.response = FakeResponseObj(status_code)


class AzureTTSUnavailable(Exception):
    """Name-matched by ``_azure_failure_is_permanent`` (missing key)."""


class FakeAzure:
    """Raises ``exc`` for the first ``fail_times`` sentences, then succeeds."""

    def __init__(self, exc, fail_times=99):
        self._exc = exc
        self._fail_times = fail_times
        self.calls = 0
        self.closed = False
        self.close_calls = 0

    async def synthesize_chunk(self, text, voice, session_id="default"):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._exc
        yield b"\x01\x02"

    async def prewarm(self):
        pass

    async def aclose(self):
        self.closed = True
        self.close_calls += 1


def _make_session(settings):
    """A GeminiLiveSession in Azure mode with the speak-loop plumbing wired,
    minus the Google socket (these tests never open one)."""
    sent: list[dict] = []
    audio: list[bytes] = []

    async def send_json(payload):
        sent.append(payload)

    async def send_bytes(data):
        audio.append(data)

    session = GeminiLiveSession(
        settings=settings, auth=None,
        send_json=send_json, send_bytes=send_bytes,
        system_prompt="x",
    )
    session._azure_mode = True
    session._azure_voice = "uz-UZ-SardorNeural"
    session._azure_q = asyncio.Queue()
    return session, sent, audio


async def _drain(session, timeout=1.0):
    """Run the speak loop until it returns (fallback) or the queue empties."""
    task = asyncio.create_task(session._azure_speak_loop())
    try:
        async def idle():
            while not (task.done() or session._azure_q.empty()):
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.02)  # let the last item finish processing
        await asyncio.wait_for(idle(), timeout)
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _types(sent):
    return [e["type"] for e in sent]


# ---- (a) a revoked key gives up immediately --------------------------------


async def test_401_falls_back_on_the_first_sentence_without_error_spam():
    session, sent, _ = _make_session(Settings(azure_speech_key="k"))
    fake = FakeAzure(FakeHTTPStatusError(401))
    session._azure = fake
    session._azure_q.put_nowait((0, "Salom."))
    session._azure_q.put_nowait((0, "Qandaysiz?"))

    await _drain(session)

    assert session._azure_mode is False, "must switch to the Gemini voice"
    # "tts.fallback" is no longer sent to the client (2026-08-05) — the voice
    # swap is silent on the wire and only logged server-side.
    assert "tts.fallback" not in _types(sent)
    # The whole point: no per-sentence 401 dumped into the farmer's chat.
    assert "error" not in _types(sent)
    # The queued sentence is dropped, not retried against a dead provider.
    assert fake.calls == 1


async def test_missing_key_is_permanent_too():
    session, sent, _ = _make_session(Settings(azure_speech_key="k"))
    session._azure = FakeAzure(AzureTTSUnavailable("AZURE_SPEECH_KEY is not set"))
    session._azure_q.put_nowait((0, "Salom."))

    await _drain(session)

    assert session._azure_mode is False
    assert "tts.fallback" not in _types(sent)


# ---- (b) transient faults get a second chance ------------------------------


async def test_transient_500_retries_once_then_falls_back():
    session, sent, _ = _make_session(
        Settings(azure_speech_key="k", azure_tts_max_failures=2)
    )
    session._azure = FakeAzure(FakeHTTPStatusError(500))
    session._azure_q.put_nowait((0, "Bir."))
    session._azure_q.put_nowait((0, "Ikki."))

    await _drain(session)

    # First failure surfaces as an error and keeps Azure; the second gives up.
    assert _types(sent).count("error") == 1
    assert session._azure_mode is False
    assert "tts.fallback" not in _types(sent)


async def test_a_good_sentence_clears_the_failure_streak():
    session, sent, _ = _make_session(
        Settings(azure_speech_key="k", azure_tts_max_failures=2)
    )
    # Fail once, then succeed: the streak resets, so Azure survives.
    session._azure = FakeAzure(FakeHTTPStatusError(503), fail_times=1)
    session._azure_q.put_nowait((0, "Bir."))
    session._azure_q.put_nowait((0, "Ikki."))

    await _drain(session)

    assert session._azure_mode is True, "one blip must not kill the good voice"
    assert session._azure_failures == 0


# ---- (c) the tts.finished bookkeeping --------------------------------------


async def test_turn_already_ended_gets_exactly_one_tts_finished():
    """The end-of-turn marker is still queued, so the speak loop would have
    sent the finish — after draining, the fallback owes it instead."""
    session, sent, _ = _make_session(Settings(azure_speech_key="k"))
    session._azure = FakeAzure(FakeHTTPStatusError(401))
    session._azure_q.put_nowait((0, "Salom."))
    session._azure_q.put_nowait((0, None))  # turn_complete already seen

    await _drain(session)

    assert _types(sent).count("tts.finished") == 1


async def test_mid_turn_fallback_leaves_the_finish_to_turn_complete():
    """No marker queued => the turn is still running. Gemini keeps speaking and
    the receive loop closes it, so the fallback must NOT send its own finish
    (that would double-fire and stop the client's playback early)."""
    session, sent, _ = _make_session(Settings(azure_speech_key="k"))
    session._azure = FakeAzure(FakeHTTPStatusError(401))
    session._azure_q.put_nowait((0, "Salom."))

    await _drain(session)

    assert "tts.finished" not in _types(sent)


# ---- (d) the payoff: Gemini audio reaches the client afterwards -------------


async def test_gemini_audio_is_forwarded_once_azure_mode_is_off():
    """In Azure mode the receive loop discards ``response.data``; after the
    fallback the same frames must reach the client."""
    session, _sent, audio = _make_session(Settings(azure_speech_key="k"))
    data = b"\xaa\xbb"

    # Azure mode: the receive loop's guard drops the frame.
    if data and not session._azure_mode:
        await session._send_bytes(data)
    assert audio == [], "Azure mode must not forward Gemini's audio"

    await session._fallback_to_gemini("401")
    # Same guard, now on the other side of the switch.
    if data and not session._azure_mode:
        await session._send_bytes(data)

    assert audio == [b"\xaa\xbb"]


class FakeLiveResponse:
    """Only ``data`` matters here; the loop reads the rest via getattr(...)."""

    def __init__(self, data=None):
        self.data = data
        self.server_content = None
        self.usage_metadata = None
        self.tool_call = None
        self.tool_call_cancellation = None
        self.session_resumption_update = None
        self.go_away = None


class GatedAudioSession:
    """Emits one audio frame, waits for ``gate``, then emits a second — so the
    test can flip the mode in between and observe both sides of the guard."""

    def __init__(self, gate):
        self._gate = gate

    def receive(self):
        async def gen():
            yield FakeLiveResponse(data=b"\x01")
            await self._gate.wait()
            yield FakeLiveResponse(data=b"\x02")
            await asyncio.Event().wait()  # park; the test cancels the task
        return gen()


async def test_receive_loop_drops_audio_in_azure_mode_and_forwards_it_after():
    """End-to-end through the REAL receive loop: the same Gemini frames that
    are discarded while Azure is healthy reach the farmer once it has failed."""
    session, _sent, audio = _make_session(Settings(azure_speech_key="k"))
    gate = asyncio.Event()
    session._session = GatedAudioSession(gate)

    recv = asyncio.create_task(session._receive_loop())
    try:
        await asyncio.sleep(0.05)
        assert audio == [], "Azure mode: Gemini's audio must be discarded"

        # Azure fails on its queued sentence -> the speak loop flips the mode.
        session._azure = FakeAzure(FakeHTTPStatusError(401))
        session._azure_q.put_nowait((0, "Salom."))
        await _drain(session)
        assert session._azure_mode is False

        gate.set()
        await asyncio.sleep(0.05)
        assert audio == [b"\x02"], "after the fallback the audio must flow"
    finally:
        recv.cancel()
        try:
            await recv
        except asyncio.CancelledError:
            pass


async def test_fallback_closes_the_azure_client_and_is_idempotent():
    session, sent, _ = _make_session(Settings(azure_speech_key="k"))
    fake = FakeAzure(FakeHTTPStatusError(401))
    session._azure = fake

    await session._fallback_to_gemini("401")
    await session._fallback_to_gemini("401")  # second call must be a no-op

    assert fake.closed is True
    assert session._azure is None
    # Idempotence is observable through the provider teardown now that the
    # client event is gone: the second call closed nothing extra.
    assert fake.close_calls == 1


# ---- (e) never enter Azure mode with no key at all -------------------------


def test_azure_voice_without_a_key_stays_on_gemini():
    session, _sent, _audio = _make_session(Settings(azure_speech_key=None))
    session._azure_mode = False
    session.set_voice("azure:uz-UZ-SardorNeural")
    assert session._azure_mode is False


def test_azure_voice_with_a_key_enters_azure_mode():
    session, _sent, _audio = _make_session(Settings(azure_speech_key="k"))
    session._azure_mode = False
    session.set_voice("azure:uz-UZ-MadinaNeural")
    assert session._azure_mode is True
    assert session._azure_voice == "uz-UZ-MadinaNeural"
