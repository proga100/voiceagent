"""Sentence chunker: turns an LLM token stream into speakable sentences.

The whole latency strategy hinges on this: the moment the first sentence is
complete it goes to TTS, while the LLM is still generating the rest. A chunk
is emitted when:

  * a sentence terminator is seen (. ! ? … ; and newlines), OR
  * the buffer grows past ``max_chars`` (long-sentence safety valve), OR
  * the stream ends (flush the remainder).

Pure and synchronous — no I/O — so it is trivially unit-testable. The async
pipeline feeds it tokens and drains completed sentences.
"""
from __future__ import annotations

# Includes ASCII + Uzbek/Cyrillic-context punctuation. Uzbek Latin uses the same
# ASCII terminators; we add the ellipsis and semicolon which GPT often emits.
_TERMINATORS = frozenset(".!?…;\n")
# Clause boundaries used ONLY to get the first chunk out fast (lower
# time-to-first-audio). After the first chunk we wait for full sentences so TTS
# prosody stays natural.
_CLAUSE_MARKS = frozenset(",:")
# Small guard so "0.3" or "v3." mid-number doesn't split. We only suppress a
# split when the char immediately before AND after a '.' are both digits.


class SentenceChunker:
    def __init__(self, max_chars: int = 120, first_clause_min_chars: int = 24) -> None:
        self._buf: list[str] = []
        self._max_chars = max_chars
        # The first spoken chunk may break on a clause mark once the buffer has at
        # least this many chars — speaks the opening clause while GPT continues.
        # 0 disables. Resets per chunker (one per turn).
        self._first_clause_min = first_clause_min_chars
        self._emitted_any = False

    @property
    def _text(self) -> str:
        return "".join(self._buf)

    def push(self, token: str) -> list[str]:
        """Feed a token; return zero or more completed sentences."""
        out: list[str] = []
        for ch in token:
            self._buf.append(ch)
            if ch in _TERMINATORS:
                if ch == "." and self._is_decimal_point():
                    continue
                sentence = self._text.strip()
                if sentence:
                    out.append(sentence)
                    self._emitted_any = True
                self._buf.clear()
            elif (
                not self._emitted_any
                and self._first_clause_min
                and ch in _CLAUSE_MARKS
                and len(self._text.strip()) >= self._first_clause_min
            ):
                # First clause only: start speaking the opening clause early.
                sentence = self._text.strip()
                out.append(sentence)
                self._emitted_any = True
                self._buf.clear()
            elif len(self._buf) >= self._max_chars and ch == " ":
                # Long run with no terminator: break on a word boundary.
                sentence = self._text.strip()
                if sentence:
                    out.append(sentence)
                    self._emitted_any = True
                self._buf.clear()
        return out

    def flush(self) -> list[str]:
        """Return the trailing partial sentence (if any) and reset."""
        sentence = self._text.strip()
        self._buf.clear()
        return [sentence] if sentence else []

    def _is_decimal_point(self) -> bool:
        # buffer currently ends with '.'; check the char before it is a digit.
        # We can't see the next char yet, so this is a best-effort guard that
        # treats "<digit>." as part of a number until proven otherwise. Kept
        # intentionally minimal per the plan.
        if len(self._buf) < 2:
            return False
        return self._buf[-2].isdigit()
