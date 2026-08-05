"""Typed input while the Live socket is down must fail LOUDLY, not silently.

Field case (2026-08-05): the Gemini Live session hit its duration deadline and
went into a transparent reconnect; the farmer typed a message in that window.
``_speak_text``'s guard no-opped, so the model never heard it, the client got
no reply AND no error — it looked like the agent simply ignored the farmer.
Worse, the line still landed in the transcript as something the model never
heard.

Contract pinned here:
  * session down (None) or reconnecting → the client gets
    ``{"type": "error", "code": "not_ready"}`` and NOTHING is recorded;
  * healthy session → the message reaches Gemini, is recorded, and no
    ``not_ready`` error is emitted.
"""
from app.config import Settings
from app.voice.providers.gemini_live import GeminiLiveSession


class FakeLiveSession:
    """Records what a healthy Live session would receive."""

    def __init__(self):
        self.sent = []

    async def send_client_content(self, *, turns, turn_complete):
        self.sent.append((turns, turn_complete))


def _session():
    sent = []

    async def send_json(payload):
        sent.append(payload)

    async def send_bytes(_):
        pass

    s = GeminiLiveSession(
        settings=Settings(), auth=None,
        send_json=send_json, send_bytes=send_bytes,
        system_prompt="x",
    )
    return s, sent


def _not_ready_events(sent):
    return [e for e in sent if e.get("type") == "error" and e.get("code") == "not_ready"]


async def test_typed_input_with_no_live_session_notifies_the_client():
    s, sent = _session()
    assert s._session is None  # fresh session: Live socket not opened yet

    await s.on_user_text("salom")

    assert len(_not_ready_events(sent)) == 1
    # The model never heard it — it must not be recorded as if it did.
    assert not any("salom" in line for line in s._transcript)


async def test_typed_input_during_transparent_reconnect_notifies_the_client():
    """The exact field case: deadline reconnect in flight, farmer types."""
    s, sent = _session()
    s._session = FakeLiveSession()
    s._reconnecting = True

    await s.on_user_text("Pomidor bargida dogʻlar bor")

    assert len(_not_ready_events(sent)) == 1
    assert s._session.sent == []  # nothing pushed into the half-open socket
    assert not any("Pomidor" in line for line in s._transcript)


async def test_typed_input_on_a_healthy_session_is_delivered_and_recorded():
    s, sent = _session()
    fake = FakeLiveSession()
    s._session = fake

    await s.on_user_text("salom")

    assert len(fake.sent) == 1, "the message must reach Gemini"
    assert fake.sent[0][1] is True  # turn_complete: a spoken reply is expected
    assert any("FERMER: salom" in line for line in s._transcript)
    assert _not_ready_events(sent) == []


async def test_recovery_after_reconnect_completes():
    """Once _reconnecting clears, the very next typed message goes through —
    the not_ready state must not be sticky."""
    s, sent = _session()
    fake = FakeLiveSession()
    s._session = fake

    s._reconnecting = True
    await s.on_user_text("birinchi")     # dropped, with an error event
    s._reconnecting = False
    await s.on_user_text("ikkinchi")     # delivered

    assert len(_not_ready_events(sent)) == 1
    assert len(fake.sent) == 1
    assert any("FERMER: ikkinchi" in line for line in s._transcript)
    assert not any("birinchi" in line for line in s._transcript)
