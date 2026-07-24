"""Transparent Gemini Live session resumption (backend contract).

On a server-side deadline close the receive loop re-establishes the SAME
Google Live session via ``SessionResumptionConfig(handle=...)`` — no
client-visible ``session.expired``, no lost context — falling back to the
existing ``session.expired`` path only when there is no resume handle, the
reconnect cap is exhausted, resumption is disabled, or the reconnect fails.

Everything is mocked at the module/session boundary (like
``test_gemini_live_finalize_case.py``): a fake ``auth.genai_client()`` whose
``aio.live.connect(model=, config=)`` records each real ``LiveConnectConfig``
and returns a fake CM wrapping a scripted fake session.
"""
import asyncio

from app.config import Settings
from app.voice.providers.gemini_live import GeminiLiveSession


class ConnectionClosedError(Exception):
    """Locally-defined remote-close: the NAME contains ``ConnectionClosed`` so
    ``_is_remote_close`` (matched by exception type name) treats it as a
    server-side deadline close — no real websockets dependency needed."""


# ---- Fake response / sub-objects ------------------------------------------


class SRU:
    """Stand-in for ``response.session_resumption_update``."""

    def __init__(self, *, resumable, new_handle, last_consumed_client_message_index=0):
        self.resumable = resumable
        self.new_handle = new_handle
        self.last_consumed_client_message_index = last_consumed_client_message_index


class GoAway:
    def __init__(self, time_left="5s"):
        self.time_left = time_left


class FakeResponse:
    """Only the attributes a test sets exist; the loop reads the rest via
    ``getattr(..., None)``."""

    def __init__(self, *, session_resumption_update=None, go_away=None,
                 server_content=None):
        self.session_resumption_update = session_resumption_update
        self.go_away = go_away
        self.server_content = server_content


# ---- Fake Live session (scripted receive() passes) -------------------------


def turn_yield_then_raise(responses, exc):
    async def gen():
        for r in responses:
            yield r
        raise exc
    return gen


def turn_yield_then_end(responses):
    async def gen():
        for r in responses:
            yield r
    return gen


class FakeSession:
    """A scripted Live session. Each ``receive()`` call consumes the next
    scripted turn (mirrors google-genai re-entering receive() per turn); once
    the script is exhausted it blocks forever so the test controls termination
    (by cancelling the recv task)."""

    def __init__(self, turns):
        self._turns = list(turns)
        self._i = 0

    def receive(self):
        i = self._i
        self._i += 1
        if i < len(self._turns):
            return self._turns[i]()
        return self._forever()

    async def _forever(self):
        await asyncio.Event().wait()
        yield  # pragma: no cover — unreachable

    # sends are irrelevant to these tests; no-op so nothing raises.
    async def send_realtime_input(self, *a, **k):
        pass

    async def send_client_content(self, *a, **k):
        pass

    async def send_tool_response(self, *a, **k):
        pass


# ---- Fake client / connect boundary ---------------------------------------


class FakeConnectCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


class FakeLive:
    def __init__(self, sessions):
        self._sessions = list(sessions)
        self.configs: list = []     # recorded LiveConnectConfig per connect
        self.connect_calls = 0

    def connect(self, *, model, config):
        self.configs.append(config)
        idx = self.connect_calls
        self.connect_calls += 1
        session = self._sessions[min(idx, len(self._sessions) - 1)]
        return FakeConnectCM(session)


class FakeAio:
    def __init__(self, live):
        self.live = live


class FakeClient:
    def __init__(self, live):
        self.aio = FakeAio(live)


class FakeAuth:
    def __init__(self, client):
        self._client = client

    def genai_client(self):
        return self._client


# ---- Harness ---------------------------------------------------------------


def _make_session(settings, sessions):
    """Wire a GeminiLiveSession onto scripted fake Live ``sessions`` and return
    (session, sent, fake_live)."""
    sent: list[dict] = []

    async def send_json(payload):
        sent.append(payload)

    async def send_bytes(data):
        pass

    fake_live = FakeLive(sessions)
    auth = FakeAuth(FakeClient(fake_live))
    session = GeminiLiveSession(
        settings=settings, auth=auth,
        send_json=send_json, send_bytes=send_bytes,
        system_prompt="x",
    )
    # Drive the Live boundary directly (bypass tool building in start()).
    session._live_tools = None
    session._live_model = "fake-model"
    return session, sent, fake_live


async def _until(pred, timeout=1.0):
    async def loop():
        while not pred():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(loop(), timeout)


async def _run_loop_until(session, pred, timeout=1.0):
    """Open the initial Live connection, run _receive_loop as a task, wait for
    ``pred`` (or the loop to finish), then cancel/collect the task."""
    await session._open_live()
    task = asyncio.create_task(session._receive_loop())
    try:
        done = asyncio.create_task(_until(pred, timeout))
        # Whichever finishes first: the predicate, or the loop returning.
        await asyncio.wait({task, done}, return_when=asyncio.FIRST_COMPLETED)
        if not done.done():
            done.cancel()
        # Surface predicate timeouts.
        if done.done() and not done.cancelled():
            done.result()
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---- (a) transparent reconnect --------------------------------------------


async def test_transparent_reconnect_resumes_with_handle_no_session_expired():
    s1 = FakeSession([
        turn_yield_then_raise(
            [FakeResponse(session_resumption_update=SRU(resumable=True, new_handle="H1"))],
            ConnectionClosedError("deadline expired"),
        )
    ])
    s2 = FakeSession([])  # blocks forever after reconnect
    session, sent, fake_live = _make_session(Settings(), [s1, s2])

    await _run_loop_until(session, lambda: fake_live.connect_calls >= 2)

    assert fake_live.connect_calls == 2
    # Second connect RESUMED the session on the captured handle.
    assert fake_live.configs[1].session_resumption is not None
    assert fake_live.configs[1].session_resumption.handle == "H1"
    # First connect ISSUED handles (handle=None), never resumed.
    assert fake_live.configs[0].session_resumption.handle is None
    assert not any(p["type"] == "session.expired" for p in sent)
    assert session._reconnects == 1


# ---- (b) no handle -> session.expired --------------------------------------


async def test_remote_close_without_handle_falls_back_to_session_expired():
    s1 = FakeSession([
        turn_yield_then_raise([], ConnectionClosedError("deadline expired"))
    ])
    session, sent, fake_live = _make_session(Settings(), [s1])

    await _run_loop_until(
        session, lambda: any(p["type"] == "session.expired" for p in sent)
    )

    expired = [p for p in sent if p["type"] == "session.expired"]
    assert len(expired) == 1
    assert fake_live.connect_calls == 1
    assert session._reconnects == 0


# ---- (c) cap exhausted -> session.expired ----------------------------------


async def test_reconnect_cap_zero_falls_back_to_session_expired():
    s1 = FakeSession([
        turn_yield_then_raise(
            [FakeResponse(session_resumption_update=SRU(resumable=True, new_handle="H1"))],
            ConnectionClosedError("deadline expired"),
        )
    ])
    session, sent, fake_live = _make_session(
        Settings(live_session_max_reconnects=0), [s1]
    )

    await _run_loop_until(
        session, lambda: any(p["type"] == "session.expired" for p in sent)
    )

    assert any(p["type"] == "session.expired" for p in sent)
    assert fake_live.connect_calls == 1
    assert session._reconnects == 0


# ---- (d) transparent reconnect NEVER cancels _case_task --------------------


async def test_transparent_reconnect_never_cancels_case_task():
    s1 = FakeSession([
        turn_yield_then_raise(
            [FakeResponse(session_resumption_update=SRU(resumable=True, new_handle="H1"))],
            ConnectionClosedError("deadline expired"),
        )
    ])
    s2 = FakeSession([])
    session, sent, fake_live = _make_session(Settings(), [s1, s2])

    release = asyncio.Event()
    completed = asyncio.Event()

    async def stub():
        await release.wait()
        completed.set()

    session._case_task = asyncio.create_task(stub())

    await _run_loop_until(session, lambda: fake_live.connect_calls >= 2)

    # Reconnect happened; the diagnosis task survived it untouched.
    assert session._reconnects == 1
    assert not session._case_task.cancelled()
    assert not session._case_task.done()

    release.set()
    await asyncio.wait_for(completed.wait(), 1.0)
    await session._case_task
    assert session._case_task.done() and not session._case_task.cancelled()


# ---- disabled: no session_resumption in config, straight to session.expired -


async def test_resumption_disabled_omits_config_and_expires_on_close():
    s1 = FakeSession([
        turn_yield_then_raise(
            [FakeResponse(session_resumption_update=SRU(resumable=True, new_handle="H1"))],
            ConnectionClosedError("deadline expired"),
        )
    ])
    session, sent, fake_live = _make_session(
        Settings(live_session_resumption_enabled=False), [s1]
    )

    await _run_loop_until(
        session, lambda: any(p["type"] == "session.expired" for p in sent)
    )

    # With resumption disabled the connect config carries NO session_resumption,
    assert fake_live.configs[0].session_resumption is None
    # and even a captured handle does not prevent the session.expired fallback.
    assert any(p["type"] == "session.expired" for p in sent)
    assert fake_live.connect_calls == 1
