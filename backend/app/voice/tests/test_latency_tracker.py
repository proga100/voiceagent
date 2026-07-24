import time

from app.voice.pipeline.latency_tracker import LatencyTracker


def test_marks_are_first_write_wins():
    t = LatencyTracker()
    t.mark("t0_mic_first_chunk")
    first = t.snapshot()["marks"]["t0_mic_first_chunk"]
    t.mark("t0_mic_first_chunk")  # ignored
    assert t.snapshot()["marks"]["t0_mic_first_chunk"] == first


def test_overwrite_flag_updates_mark():
    t = LatencyTracker()
    t.mark("t10_turn_end")
    time.sleep(0.001)
    t.mark("t10_turn_end", overwrite=True)
    # no exception; mark exists
    assert t.has("t10_turn_end")


def test_ttfa_delta_computed():
    t = LatencyTracker()
    t.mark("t3_stt_final")
    time.sleep(0.01)
    t.mark("t9_first_audio_to_client")
    deltas = t.deltas()
    assert "ttfa_ms" in deltas
    assert deltas["ttfa_ms"] >= 5.0  # ~10ms, allow scheduler slack


def test_missing_marks_omitted_from_deltas():
    t = LatencyTracker()
    t.mark("t0_mic_first_chunk")
    deltas = t.deltas()
    assert "ttfa_ms" not in deltas  # needs t3 + t9


def test_snapshot_marks_relative_to_t0():
    t = LatencyTracker()
    t.mark("t0_mic_first_chunk")
    time.sleep(0.005)
    t.mark("t3_stt_final")
    marks = t.snapshot()["marks"]
    assert marks["t0_mic_first_chunk"] == 0.0
    assert marks["t3_stt_final"] > 0.0
