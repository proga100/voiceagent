from app.voice.pipeline.chunker import SentenceChunker


def _feed(text: str, **kw) -> list[str]:
    """Feed a string token-by-token, returning all emitted sentences + flush."""
    c = SentenceChunker(**kw)
    out: list[str] = []
    for ch in text:
        out += c.push(ch)
    out += c.flush()
    return out


def test_single_sentence_emitted_on_terminator():
    assert _feed("Salom dunyo.") == ["Salom dunyo."]


def test_multiple_sentences():
    out = _feed("Birinchi gap. Ikkinchi gap! Uchinchi?")
    assert out == ["Birinchi gap.", "Ikkinchi gap!", "Uchinchi?"]


def test_first_sentence_available_before_stream_ends():
    c = SentenceChunker()
    emitted = c.push("Tushunarli. ")
    assert emitted == ["Tushunarli."]
    # remainder still buffered, nothing flushed yet
    assert c.push("Davom etamiz") == []


def test_flush_returns_trailing_partial():
    c = SentenceChunker()
    assert c.push("Yarim gap") == []
    assert c.flush() == ["Yarim gap"]


def test_decimal_point_does_not_split():
    out = _feed("Konsentratsiya 0.3 foiz bo'lsin.")
    assert out == ["Konsentratsiya 0.3 foiz bo'lsin."]


def test_long_run_splits_on_word_boundary():
    long = "soz " * 60  # ~240 chars, no terminator
    out = _feed(long, max_chars=120)
    assert len(out) >= 2
    assert "".join(out).replace(" ", "") == long.replace(" ", "")


def test_newline_is_terminator():
    assert _feed("Birinchi\nIkkinchi.") == ["Birinchi", "Ikkinchi."]


def test_empty_and_whitespace_yield_nothing():
    assert _feed("   \n  ") == []


def test_first_clause_emitted_early_on_comma():
    # First chunk breaks on a comma once long enough, so TTS starts sooner.
    c = SentenceChunker(first_clause_min_chars=10)
    out = []
    for ch in "Sariq dogʻlar paydo boʻlganda, ularni oching.":
        out += c.push(ch)
    out += c.flush()
    assert out[0] == "Sariq dogʻlar paydo boʻlganda,"
    assert out[1] == "ularni oching."


def test_short_first_clause_not_split():
    # Comma before min length: do not fragment.
    c = SentenceChunker(first_clause_min_chars=24)
    out = []
    for ch in "Ha, tushunarli gap keldi.":
        out += c.push(ch)
    out += c.flush()
    assert out == ["Ha, tushunarli gap keldi."]


def test_clause_split_only_for_first_chunk():
    c = SentenceChunker(first_clause_min_chars=10)
    out = []
    for ch in "Birinchi uzun gap tugadi. Ikkinchi gap, bu yerda vergul bor.":
        out += c.push(ch)
    out += c.flush()
    # Only the very first chunk may break on a comma; later sentences stay whole.
    assert out[0] == "Birinchi uzun gap tugadi."
    assert "Ikkinchi gap, bu yerda vergul bor." in out
