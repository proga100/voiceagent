import math
import struct

from app.voice.vad.silero_vad import EnergyVAD, rms_energy


def _tone(samples: int, amplitude: int) -> bytes:
    """PCM16 sine-ish buffer at a given amplitude."""
    return b"".join(
        struct.pack("<h", int(amplitude * math.sin(i * 0.3))) for i in range(samples)
    )


def _silence(samples: int) -> bytes:
    return b"\x00\x00" * samples


def test_rms_silence_is_zero():
    assert rms_energy(_silence(160)) == 0.0


def test_rms_loud_is_high():
    assert rms_energy(_tone(160, 20000)) > 0.3


def test_empty_frame_is_zero():
    assert rms_energy(b"") == 0.0


def test_is_speech_threshold():
    vad = EnergyVAD(speech_threshold=0.04)
    assert vad.is_speech(_tone(160, 20000)) is True
    assert vad.is_speech(_silence(160)) is False


def test_update_requires_confirm_frames():
    vad = EnergyVAD(speech_threshold=0.04, confirm_frames=3)
    loud = _tone(160, 20000)
    assert vad.update(loud) is False  # 1
    assert vad.update(loud) is False  # 2
    assert vad.update(loud) is True   # 3 -> confirmed (edge)
    assert vad.update(loud) is False  # reset after edge


def test_silence_breaks_the_run():
    vad = EnergyVAD(speech_threshold=0.04, confirm_frames=3)
    loud = _tone(160, 20000)
    vad.update(loud)
    vad.update(loud)
    vad.update(_silence(160))  # breaks run
    assert vad.update(loud) is False  # only 1 voiced again
