"""photo.upload no longer carries target_part — the server resolves it.

The client used to echo back the part it guessed, which could disagree with the
part the model had just asked for: one session collects several different shots
("general view, then close-up, then the root") while the interview's plant_part
stays a single value. The server knows both, so it decides.

Order, most specific first:
    1. what request_photo last asked for
    2. the interview's plant_part
    3. whole_plant

It matters because the answer feeds verify_photo(), which tells the model
"you asked for a leaf but this is a root" — comparing against the wrong part
would make that warning wrong rather than absent.
"""
from app.config import Settings
from app.voice.providers.gemini_live import GeminiLiveSession


def _session() -> GeminiLiveSession:
    async def send_json(_):
        pass

    async def send_bytes(_):
        pass

    return GeminiLiveSession(
        settings=Settings(), auth=None,
        send_json=send_json, send_bytes=send_bytes,
        system_prompt="x",
    )


def _resolve(s: GeminiLiveSession, sent: str | None) -> str:
    """The chain exactly as on_photo applies it."""
    return (
        sent
        or s.last_requested_part
        or s.interview_plant_part
        or "whole_plant"
    )


def test_defaults_are_empty_on_a_fresh_session():
    s = _session()
    assert s.last_requested_part is None
    assert s.interview_plant_part is None


def test_falls_back_to_whole_plant_when_nothing_is_known():
    s = _session()
    assert _resolve(s, None) == "whole_plant"


def test_uses_the_interview_answer_when_the_model_asked_for_nothing():
    s = _session()
    s.interview_plant_part = "leaf"
    assert _resolve(s, None) == "leaf"


def test_the_models_request_wins_over_the_interview_answer():
    """The farmer picked 'flower' in the interview, then the model asked for the
    root. This photo is the root — comparing it against 'flower' would produce a
    bogus mismatch warning."""
    s = _session()
    s.interview_plant_part = "flower"
    s.last_requested_part = "root"
    assert _resolve(s, "") == "root"


def test_an_explicit_value_still_wins():
    """Older clients keep sending target_part; their value must not be ignored."""
    s = _session()
    s.interview_plant_part = "flower"
    s.last_requested_part = "root"
    assert _resolve(s, "fruit") == "fruit"


def test_a_later_request_replaces_the_earlier_one():
    """Several shots in one session: each request_photo re-points the fallback."""
    s = _session()
    s.last_requested_part = "whole_plant"
    assert _resolve(s, None) == "whole_plant"
    s.last_requested_part = "root"
    assert _resolve(s, None) == "root"
