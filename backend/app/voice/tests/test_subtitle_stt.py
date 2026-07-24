"""Subtitle STT (Google Chirp 3): provider, factory, filters, turn hook."""
import asyncio

from app.config import Settings
from app.voice.providers.gemini_live import GeminiLiveSession
from app.voice.providers.subtitle_stt import (
    GoogleChirpSTT,
    build_subtitle_stt,
    plausible,
    to_latin_uz,
)


# ---- factory -----------------------------------------------------------------

def test_factory_builds_google_when_creds_present():
    s = Settings(google_project_id="proj-1")
    assert isinstance(build_subtitle_stt(s, object()), GoogleChirpSTT)


def test_factory_fail_open_without_creds():
    assert build_subtitle_stt(Settings(google_project_id=""), object()) is None
    assert build_subtitle_stt(Settings(google_project_id="p"), None) is None


# ---- GoogleChirpSTT provider (fake speech client) ----------------------------

class _FakeAlt:
    def __init__(self, t): self.transcript = t


class _FakeResult:
    def __init__(self, t): self.alternatives = [_FakeAlt(t)]


class _FakeResp:
    def __init__(self, texts): self.results = [_FakeResult(t) for t in texts]


class _FakeSpeechClient:
    def __init__(self, texts, capture=None):
        self._texts = texts
        self._capture = capture

    async def recognize(self, *, request):
        if self._capture is not None:
            self._capture["recognizer"] = request.recognizer
            self._capture["model"] = request.config.model
            self._capture["lang"] = list(request.config.language_codes)
        return _FakeResp(self._texts)


class _FakeAuth:
    def __init__(self, client): self._c = client
    def speech_client(self): return self._c


def test_chirp_transcribes_and_joins_results():
    cap = {}
    s = Settings(google_project_id="p", google_stt_region="eu",
                 google_stt_model="chirp_3")
    stt = GoogleChirpSTT(s, _FakeAuth(_FakeSpeechClient(
        ["Mening ismim Rustam.", "Olmalarim kasal."], cap)))
    out = asyncio.run(stt.transcribe_pcm(b"\x00" * 100, 16000))
    assert out == "Mening ismim Rustam. Olmalarim kasal."
    assert cap["model"] == "chirp_3"
    assert cap["lang"] == ["uz-UZ"]
    assert cap["recognizer"] == "projects/p/locations/eu/recognizers/_"


def test_chirp_empty_results():
    s = Settings(google_project_id="p")
    stt = GoogleChirpSTT(s, _FakeAuth(_FakeSpeechClient([])))
    assert asyncio.run(stt.transcribe_pcm(b"\x00" * 100, 16000)) == ""


# ---- output filters ----------------------------------------------------------

def test_to_latin_uz_transliterates():
    assert to_latin_uz("Ассалому алайкум") == "Assalomu alaykum"
    assert (
        to_latin_uz("Мен Тошкент вилоятидан, ўрик ва ғўза ҳақида")
        == "Men Toshkent viloyatidan, oʻrik va gʻoʻza haqida"
    )
    assert to_latin_uz("Қишлоқ") == "Qishloq"


def test_to_latin_uz_latin_passthrough():
    s = "Mening ismim Rustam, oʻriklar gullayapti."
    assert to_latin_uz(s) is s                    # untouched, same object


def test_plausible_guard():
    assert plausible("Mening ismim Rustam", "meni ismim rustam")
    assert not plausible(
        "Qalampir TVning qabuliga xush kelibsiz",
        "salom holingiz qanday yaxshimisiz",
    )
    assert plausible("har qanday matn", "tam")     # short rough -> no veto
    assert plausible("har qanday matn", "")


# ---- turn hook in GeminiLiveSession ------------------------------------------

class _FakeSTT:
    def __init__(self, text="Rustam", raise_exc=False):
        self.text = text
        self.raise_exc = raise_exc
        self.calls: list[tuple[bytes, int]] = []

    async def transcribe_pcm(self, pcm: bytes, rate: int) -> str:
        self.calls.append((pcm, rate))
        if self.raise_exc:
            raise RuntimeError("scribe down")
        return self.text

    async def aclose(self):
        pass


def _session(stt=None) -> tuple[GeminiLiveSession, list]:
    sent: list[dict] = []

    async def send_json(payload):
        sent.append(payload)

    async def send_bytes(_):
        pass

    s = GeminiLiveSession(
        settings=Settings(), auth=None, send_json=send_json,
        send_bytes=send_bytes, system_prompt="t",
    )
    s._stt = stt
    return s, sent


async def _drain(s):
    if s._stt_tasks:
        await asyncio.gather(*s._stt_tasks, return_exceptions=True)


def test_scribe_replaces_line_and_emits_event():
    async def run():
        stt = _FakeSTT("Mening ismim Rustam")
        s, sent = _session(stt)
        s._mem_in.append("tam")
        s._turn_audio += b"\x00" * 20000
        s._flush_transcript_turn()
        assert s._turn_audio == bytearray()          # buffer cleared
        await _drain(s)
        assert s._transcript[0] == "FERMER: Mening ismim Rustam"
        assert {"type": "stt.corrected", "text": "Mening ismim Rustam",
                "orig": "tam"} in sent
    asyncio.run(run())


def test_short_audio_skipped():
    async def run():
        stt = _FakeSTT()
        s, sent = _session(stt)
        s._mem_in.append("uh")
        s._turn_audio += b"\x00" * 100               # < _MIN_SCRIBE_BYTES
        s._flush_transcript_turn()
        await _drain(s)
        assert stt.calls == [] and sent == []
    asyncio.run(run())


def test_unchanged_text_no_event():
    async def run():
        stt = _FakeSTT("salom")
        s, sent = _session(stt)
        s._mem_in.append("salom")
        s._turn_audio += b"\x00" * 20000
        s._flush_transcript_turn()
        await _drain(s)
        assert sent == []
    asyncio.run(run())


def test_implausible_result_rejected():
    async def run():
        stt = _FakeSTT("Qalampir TVning qabuliga xush kelibsiz")
        s, sent = _session(stt)
        s._mem_in.append("salom holingiz qanday yaxshimisiz")
        s._turn_audio += b"\x00" * 20000
        s._flush_transcript_turn()
        await _drain(s)
        assert sent == []
        assert s._transcript == ["FERMER: salom holingiz qanday yaxshimisiz"]
    asyncio.run(run())


def test_cyrillic_result_transliterated():
    async def run():
        stt = _FakeSTT("Мен Рустам")
        s, sent = _session(stt)
        s._mem_in.append("men rustam")
        s._turn_audio += b"\x00" * 20000
        s._flush_transcript_turn()
        await _drain(s)
        assert sent and sent[0]["text"] == "Men Rustam"
    asyncio.run(run())


def test_scribe_failure_is_silent():
    async def run():
        s, sent = _session(_FakeSTT(raise_exc=True))
        s._mem_in.append("tam")
        s._turn_audio += b"\x00" * 20000
        s._flush_transcript_turn()
        await _drain(s)
        assert sent == []
        assert s._transcript == ["FERMER: tam"]     # rough line kept
    asyncio.run(run())


def test_no_provider_zero_overhead():
    async def run():
        s, sent = _session(None)
        s._mem_in.append("salom")
        s._turn_audio += b"\x00" * 20000
        s._flush_transcript_turn()
        assert s._stt_tasks == set() and sent == []
        assert s._turn_audio == bytearray()          # stale bytes still cleared
    asyncio.run(run())
