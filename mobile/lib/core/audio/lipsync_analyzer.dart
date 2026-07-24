/// Avatar lipsync driver — turns the agent PCM stream into timestamped
/// mouth-open + spectral-band frames that drive the 3D avatar.
///
/// The controller [ingest]s every 24 kHz PCM16 chunk as it arrives from the
/// socket (which runs *ahead* of playback). Analysis happens in fixed 20 ms
/// windows; each window produces a [LipsyncFrame] tagged with its position on
/// the agent-audio timeline (`mediaTimeMs = cumulative samples / 24`). A ticker
/// in the widget layer then reads [PcmPlayer.playbackPositionMs] and calls
/// [frameAt] to pull the frame for the audio *currently being heard*, keeping
/// the mouth in sync with the ear rather than the socket.
library;

import 'dart:collection';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

/// One analysed lip-sync frame, positioned on the agent-audio timeline.
@immutable
class LipsyncFrame {
  const LipsyncFrame({
    required this.mediaTimeMs,
    required this.open,
    required this.low,
    required this.mid,
    required this.high,
  });

  /// Position of this frame on the agent-audio timeline, in ms.
  final double mediaTimeMs;

  /// Mouth openness `0..1`.
  final double open;

  /// Low-band spectral share `0..1` (below ~300 Hz).
  final double low;

  /// Mid-band spectral share `0..1` (~300–1500 Hz).
  final double mid;

  /// High-band spectral share `0..1` (above ~1500 Hz).
  final double high;
}

/// Produces timestamped [LipsyncFrame]s (and a live [mouthOpen] signal) from
/// streamed agent PCM16 @ 24 kHz.
class LipsyncAnalyzer {
  LipsyncAnalyzer({
    this.gain = 6.0,
    this.attack = 0.5,
    this.decay = 0.2,
  }) {
    _alpha1 = _onePoleAlpha(300); // low band cutoff
    _alpha2 = _onePoleAlpha(1500); // low+mid cutoff
  }

  /// Loudness → openness gain (tuned so speech lands ≈0.3–0.9).
  final double gain;

  /// Smoothing coefficient while the mouth is opening (fast).
  final double attack;

  /// Smoothing coefficient while the mouth is closing (slower).
  final double decay;

  static const int _sampleRate = 24000;
  static const int _window = 480; // 20 ms @ 24 kHz

  /// Safety cap so the FIFO never grows unbounded if nothing drains it.
  ///
  /// Sized to hold an agent turn that has streamed *ahead* of playback: the
  /// analyzer [ingest]s each chunk the instant it arrives, but playback now lags
  /// by the [JitterBuffer] prebuffer (up to ~0.7 s) on top of the network lead,
  /// so up to ~1 s more frames sit here before [frameAt] consumes them. 60 s of
  /// 20 ms frames leaves generous headroom over that lead. (See PcmPlayer /
  /// JitterBuffer.)
  static const int _maxFrames = 3000; // 60 s / 20 ms


  /// Mouth openness `0..1` at the current playback position. Updated by
  /// [frameAt]; snapped to `0` by [reset]. Kept for listeners that only need
  /// openness (the ValueListenable the widget layer can bind to).
  final ValueNotifier<double> mouthOpen = ValueNotifier<double>(0);

  final Queue<LipsyncFrame> _fifo = Queue<LipsyncFrame>();

  // Window accumulator + carries between ingest() calls.
  final Int16List _acc = Int16List(_window);
  int _accLen = 0;
  int? _byteCarry; // leftover odd byte when a sample is split across chunks
  int _ingestedSamples = 0; // cumulative samples folded into completed windows

  // One-pole low-pass filter states (carried across windows).
  double _lp1 = 0;
  double _lp2 = 0;
  double _smoothedRms = 0;

  late final double _alpha1;
  late final double _alpha2;

  static double _onePoleAlpha(double fc) {
    const dt = 1.0 / _sampleRate;
    final rc = 1.0 / (2 * math.pi * fc);
    return dt / (rc + dt);
  }

  /// Analyses [pcm] (PCM16 LE mono @ 24 kHz) into 20 ms windows, carrying the
  /// remainder — and a possible split byte — into the next call.
  void ingest(Uint8List pcm) {
    if (pcm.isEmpty) return;
    var offset = 0;
    if (_byteCarry != null) {
      _addSample(_signed(_byteCarry! | (pcm[0] << 8)));
      _byteCarry = null;
      offset = 1;
    }
    final available = pcm.length - offset;
    final pairs = available >> 1;
    for (var i = 0; i < pairs; i++) {
      final lo = pcm[offset + i * 2];
      final hi = pcm[offset + i * 2 + 1];
      _addSample(_signed(lo | (hi << 8)));
    }
    if (available.isOdd) _byteCarry = pcm[pcm.length - 1];
  }

  static int _signed(int u) => u >= 0x8000 ? u - 0x10000 : u;

  void _addSample(int s) {
    _acc[_accLen++] = s;
    if (_accLen == _window) {
      _processWindow();
      _accLen = 0;
    }
  }

  void _processWindow() {
    var sumTot = 0.0, sumLp1 = 0.0, sumLp2 = 0.0;
    for (var i = 0; i < _window; i++) {
      final x = _acc[i] / 32768.0;
      _lp1 += _alpha1 * (x - _lp1);
      _lp2 += _alpha2 * (x - _lp2);
      sumTot += x * x;
      sumLp1 += _lp1 * _lp1;
      sumLp2 += _lp2 * _lp2;
    }
    final totalE = sumTot / _window;
    final lowE = sumLp1 / _window;
    final lp2E = sumLp2 / _window;
    final midE = math.max(0.0, lp2E - lowE);
    final highE = math.max(0.0, totalE - lp2E);
    final bandSum = lowE + midE + highE + 1e-9; // guard div0
    final low = lowE / bandSum;
    final mid = midE / bandSum;
    final high = highE / bandSum;

    final rms = math.sqrt(totalE);
    // Attack fast, decay slow so the mouth snaps open but relaxes shut.
    final coeff = rms > _smoothedRms ? attack : decay;
    _smoothedRms += (rms - _smoothedRms) * coeff;
    final open = (_smoothedRms * gain).clamp(0.0, 1.0);

    _ingestedSamples += _window;
    _fifo.addLast(
      LipsyncFrame(
        mediaTimeMs: _ingestedSamples * 1000.0 / _sampleRate,
        open: open,
        low: low,
        mid: mid,
        high: high,
      ),
    );
    while (_fifo.length > _maxFrames) {
      _fifo.removeFirst();
    }
  }

  /// Returns the newest frame at or before [playbackMs], dropping every older
  /// frame; `null` if nothing has reached [playbackMs] yet (or the FIFO is
  /// empty). Updates [mouthOpen] to the returned frame's openness.
  LipsyncFrame? frameAt(double playbackMs) {
    LipsyncFrame? found;
    while (_fifo.isNotEmpty && _fifo.first.mediaTimeMs <= playbackMs) {
      found = _fifo.removeFirst();
    }
    if (found != null) mouthOpen.value = found.open;
    return found;
  }

  /// Barge-in / flush: clears the FIFO, resets analysis state and snaps the
  /// mouth shut. Called in lock-step with [PcmPlayer.flush] so the analyzer and
  /// the playback cursor restart from the same zero.
  void reset() {
    _fifo.clear();
    _accLen = 0;
    _byteCarry = null;
    _lp1 = 0;
    _lp2 = 0;
    _smoothedRms = 0;
    _ingestedSamples = 0;
    mouthOpen.value = 0;
  }

  /// Releases the notifier.
  void dispose() => mouthOpen.dispose();
}
