"""Uzbek Latin → Cyrillic transliteration for on-screen button labels.

When the client opens the session with ``language="uz-Cyrl"`` the agent's free
speech already switches to Cyrillic (via the system-prompt directive). This
module covers the OTHER half: the guided-flow button labels / prompts that reach
the client verbatim in ``chat.question`` (option ``label``, ``prompt``) and
``chat.step`` (``label``). The frozen ``UZ`` Latin table stays the single source
of truth; labels are transliterated at emission time only, so nothing on the
wire contract or the mobile mirror changes for Latin sessions.

The mapping is deterministic and standard for Uzbek (oʻ→ў, gʻ→ғ, sh→ш, ch→ч,
y+vowel→ё/ю/я/е, word-initial e→э). It is intentionally exhaustively tested
against the real label set (see ``test_translit.py``) rather than trusted blind.
"""
from __future__ import annotations

# ʻ (U+02BB modifier letter turned comma) marks oʻ / gʻ; ʼ (U+02BC) / ’ is the
# tutuq belgisi (glottal stop) → ъ.
_MOD = "ʻ"
_TUTUQ = ("ʼ", "’")

# y + these vowels form the iotated Cyrillic letters (lowercase, uppercase).
_YO = {
    "o": ("ё", "Ё"),
    "u": ("ю", "Ю"),
    "a": ("я", "Я"),
    "e": ("е", "Е"),
}

_SINGLE = {
    "a": "а", "b": "б", "c": "с", "d": "д", "f": "ф", "g": "г", "h": "ҳ",
    "i": "и", "j": "ж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о",
    "p": "п", "q": "қ", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в",
    "x": "х", "y": "й", "z": "з",
}
_SINGLE.update({k.upper(): v.upper() for k, v in _SINGLE.items()})


def to_uz_cyrillic(text: str) -> str:
    """Transliterate an Uzbek-Latin ``text`` to Uzbek Cyrillic.

    Non-Latin characters (punctuation, emoji, digits) pass through unchanged.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        low = ch.lower()
        nxt = text[i + 1] if i + 1 < n else ""
        nn = text[i + 2] if i + 2 < n else ""

        # oʻ / gʻ (o|g followed by the turned-comma modifier)
        if low in ("o", "g") and nxt == _MOD:
            base = "ў" if low == "o" else "ғ"
            out.append(base.upper() if ch.isupper() else base)
            i += 2
            continue
        # sh / ch digraphs
        if low == "s" and nxt in ("h", "H"):
            out.append("Ш" if ch.isupper() else "ш")
            i += 2
            continue
        if low == "c" and nxt in ("h", "H"):
            out.append("Ч" if ch.isupper() else "ч")
            i += 2
            continue
        # y + vowel → iotated letter, UNLESS the vowel carries ʻ (then it is a
        # real oʻ and the y is a plain й — e.g. «Yoʻq» → «Йўқ», not «Ёқ»).
        if low == "y" and nxt.lower() in _YO and nn != _MOD:
            pair = _YO[nxt.lower()]
            out.append(pair[1] if ch.isupper() else pair[0])
            i += 2
            continue
        # e: word-initial → э, otherwise → е
        if low == "e":
            prev = text[i - 1] if i > 0 else ""
            initial = not prev.isalpha()
            if initial:
                out.append("Э" if ch.isupper() else "э")
            else:
                out.append("Е" if ch.isupper() else "е")
            i += 1
            continue
        if ch in _TUTUQ:
            out.append("ъ")
            i += 1
            continue
        out.append(_SINGLE.get(ch, ch))
        i += 1
    return "".join(out)
