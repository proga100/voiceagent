"""Voice activity detection for mid-speech barge-in.

MVP ships an **energy (RMS) VAD** over PCM16 frames — dependency-free, fast, and
unit-testable. It exists to detect that the user has started speaking *while the
agent is talking* so the session can barge-in. Endpointing (end-of-utterance) is
handled by SpeechKit's built-in EOU classifier, so this VAD is barge-in only.

A drop-in Silero-ONNX implementation can replace ``EnergyVAD`` behind the same
``is_speech(frame) -> bool`` / ``update(frame) -> bool`` interface (the latter
returns True only once speech is *confirmed* over several frames, to suppress
TTS echo false-positives). Echo is mitigated upstream by the browser's
``echoCancellation`` plus the confirmation debounce here.
"""
from __future__ import annotations

import numpy as np


def rms_energy(frame: bytes) -> float:
    """Root-mean-square amplitude of a little-endian PCM16 frame, 0..1."""
    if not frame:
        return 0.0
    samples = np.frombuffer(frame, dtype="<i2")
    if samples.size == 0:
        return 0.0
    x = samples.astype(np.float64)
    return float(np.sqrt(np.mean(x * x)) / 32768.0)


class EnergyVAD:
    def __init__(
        self,
        *,
        speech_threshold: float = 0.04,
        confirm_frames: int = 3,
    ) -> None:
        # speech_threshold: RMS above this counts as voiced. ~0.04 is a
        # conservative default that ignores room noise / faint TTS echo.
        # confirm_frames: consecutive voiced frames required to confirm speech.
        self._threshold = speech_threshold
        self._confirm = confirm_frames
        self._voiced_run = 0

    def is_speech(self, frame: bytes) -> bool:
        """Instantaneous voiced/unvoiced decision for one frame."""
        return rms_energy(frame) >= self._threshold

    def update(self, frame: bytes) -> bool:
        """Feed a frame; return True the moment speech is *confirmed*.

        Returns True once ``confirm_frames`` consecutive voiced frames are seen,
        then resets so the caller gets a single edge-triggered signal per onset.
        """
        if self.is_speech(frame):
            self._voiced_run += 1
            if self._voiced_run >= self._confirm:
                self._voiced_run = 0
                return True
        else:
            self._voiced_run = 0
        return False

    def reset(self) -> None:
        self._voiced_run = 0


class NoiseGate:
    """Input noise gate: passes audio to STT only when loud enough.

    Quiet background noise (below ``threshold``) is replaced with digital silence
    so the recognizer never transcribes it. A ``hangover`` keeps the gate open
    briefly after speech dips, so word gaps and trailing syllables aren't cut.
    Sending silence (rather than nothing) keeps the STT stream continuous so its
    end-of-utterance detector still fires.
    """

    def __init__(self, threshold: float = 0.02, hangover_frames: int = 6) -> None:
        self._threshold = threshold
        self._hangover = hangover_frames
        self._open = 0

    def gate(self, frame: bytes) -> bytes:
        """Return the frame if voiced/within hangover, else a silent frame."""
        if rms_energy(frame) >= self._threshold:
            self._open = self._hangover
            return frame
        if self._open > 0:
            self._open -= 1
            return frame
        return b"\x00" * len(frame)

    @property
    def is_open(self) -> bool:
        return self._open > 0
