"""Per-turn latency tracker capturing the t0..t10 marks from the plan.

Uses a monotonic clock (``perf_counter``). One instance per dialog turn; call
``mark(name)`` at each stage, then ``snapshot()`` to emit ``latency.metrics``.
The headline metric is ``ttfa`` (time-to-first-audio = t9 - t3).
"""
from __future__ import annotations

from time import perf_counter

# Canonical mark names in pipeline order.
MARKS = (
    "t0_mic_first_chunk",
    "t1_vad_speech_start",
    "t2_stt_first_partial",
    "t3_stt_final",
    "t4_gpt_request",
    "t5_gpt_first_token",
    "t6_first_sentence",
    "t7_tts_request",
    "t8_tts_first_audio",
    "t9_first_audio_to_client",
    "t10_turn_end",
)


class LatencyTracker:
    def __init__(self) -> None:
        self._marks: dict[str, float] = {}

    def mark(self, name: str, *, overwrite: bool = False) -> None:
        """Record a timestamp for ``name`` (first write wins unless overwrite)."""
        if name not in self._marks or overwrite:
            self._marks[name] = perf_counter()

    def has(self, name: str) -> bool:
        return name in self._marks

    def _delta(self, a: str, b: str) -> float | None:
        if a in self._marks and b in self._marks:
            return round((self._marks[b] - self._marks[a]) * 1000.0, 1)  # ms
        return None

    def deltas(self) -> dict[str, float]:
        """Per-stage millisecond deltas; missing marks are omitted."""
        pairs = {
            "stt_total_ms": ("t0_mic_first_chunk", "t3_stt_final"),
            "stt_first_partial_ms": ("t0_mic_first_chunk", "t2_stt_first_partial"),
            "gpt_first_token_ms": ("t4_gpt_request", "t5_gpt_first_token"),
            "tts_first_audio_ms": ("t7_tts_request", "t8_tts_first_audio"),
            "ttfa_ms": ("t3_stt_final", "t9_first_audio_to_client"),
            "total_turn_ms": ("t0_mic_first_chunk", "t10_turn_end"),
        }
        result: dict[str, float] = {}
        for label, (a, b) in pairs.items():
            d = self._delta(a, b)
            if d is not None:
                result[label] = d
        return result

    def snapshot(self) -> dict[str, dict[str, float]]:
        """Relative marks (ms from t0) + computed deltas, for latency.metrics."""
        base = self._marks.get("t0_mic_first_chunk")
        rel = (
            {k: round((v - base) * 1000.0, 1) for k, v in self._marks.items()}
            if base is not None
            else {}
        )
        return {"marks": rel, "deltas": self.deltas()}
