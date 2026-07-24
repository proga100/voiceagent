/// Network-jitter cushion in front of the native audio queue.
///
/// The agent voice arrives as PCM16 mono @ 24 kHz over one WebSocket. Wi-Fi
/// jitter delivers those chunks unevenly, so feeding the very first chunk to the
/// player the instant it lands (zero cushion) means the native queue drains
/// during every network hiccup and the audio scrambles. This buffer holds a
/// small telephony-style cushion before playback starts:
///
///  * idle → buffering: the first fed chunk starts accumulating.
///  * buffering → playing: [drain] releases the whole cushion at once as soon as
///    EITHER [prebufferMs] of audio has accumulated OR [maxWaitMs] has elapsed
///    since the first chunk (so a slow trickle still starts promptly).
///  * playing: subsequent feeds pass straight through ([drain] returns them as
///    they arrive).
///
/// When the native queue drains mid-turn the caller reports [playbackUnderrun];
/// the buffer drops back to buffering with the smaller [rebufferMs] threshold so
/// the next burst is re-cushioned without a long stall.
///
/// The class is pure (no Flutter, no platform channels) and takes an injectable
/// [nowMs] clock, so the whole state machine is unit-testable with a fake clock.
library;

import 'dart:typed_data';

import '../config.dart';

/// Lifecycle of the jitter buffer.
enum JitterState {
  /// Nothing buffered; the next feed begins a fresh cushion.
  idle,

  /// Accumulating the pre-roll cushion; [drain] withholds audio.
  buffering,

  /// Cushion released; feeds pass straight through.
  playing,
}

/// A pre-roll jitter cushion between the socket and the native player.
class JitterBuffer {
  JitterBuffer({
    this.prebufferMs = 500,
    this.rebufferMs = 250,
    this.maxWaitMs = 700,
    int Function()? nowMs,
  }) : _now = nowMs ?? _wallClockNow;

  /// Cushion accumulated before the *first* burst of a turn plays.
  final int prebufferMs;

  /// Smaller cushion re-accumulated after a mid-turn underrun.
  final int rebufferMs;

  /// Hard ceiling on the buffering wait: playback starts at [maxWaitMs] after
  /// the first buffered chunk even if [prebufferMs] was never reached (a slow
  /// trickle still gets heard rather than stalling forever).
  final int maxWaitMs;

  final int Function() _now;
  static int _wallClockNow() => DateTime.now().millisecondsSinceEpoch;

  JitterState _state = JitterState.idle;
  final BytesBuilder _pending = BytesBuilder();

  /// Wall-clock time the first chunk of the current buffering pass arrived, or
  /// `-1` while no chunk has yet been buffered (so [maxWaitMs] only counts once
  /// there is actually audio waiting to play).
  int _firstChunkAt = -1;

  /// The cushion currently required to leave buffering: [prebufferMs] for the
  /// first burst, [rebufferMs] after an underrun.
  late int _thresholdMs = prebufferMs;

  /// Current lifecycle state.
  JitterState get state => _state;

  /// Whether the cushion has been released and feeds are flowing through.
  bool get isPlaying => _state == JitterState.playing;

  /// Whether audio is being withheld while the cushion fills.
  bool get isBuffering => _state == JitterState.buffering;

  /// Milliseconds of audio currently held pending (24 kHz PCM16 mono ⇒ 48
  /// bytes/ms).
  int get bufferedMs => (_pending.length * 1000) ~/ (playbackSampleRate * 2);

  /// Appends a chunk. Starts (or continues) the cushion; whether the bytes are
  /// released is decided by [drain].
  void feed(Uint8List bytes) {
    if (bytes.isEmpty) return;
    if (_state == JitterState.idle) {
      _state = JitterState.buffering;
      _thresholdMs = prebufferMs;
      _firstChunkAt = -1;
    }
    if (_state == JitterState.buffering && _firstChunkAt < 0) {
      _firstChunkAt = _now();
    }
    _pending.add(bytes);
  }

  /// Returns bytes ready for the native queue, or `null` when nothing should be
  /// played yet.
  ///
  ///  * buffering: `null` until the cushion is full (or [maxWaitMs] elapsed),
  ///    then the entire cushion as one concatenated buffer, and state→playing.
  ///  * playing: everything fed since the last drain (pass-through).
  ///  * idle: always `null`.
  Uint8List? drain() {
    switch (_state) {
      case JitterState.idle:
        return null;
      case JitterState.buffering:
        if (_pending.isEmpty) return null;
        final waited = _firstChunkAt < 0 ? 0 : _now() - _firstChunkAt;
        if (bufferedMs >= _thresholdMs || waited >= maxWaitMs) {
          _state = JitterState.playing;
          return _pending.takeBytes();
        }
        return null;
      case JitterState.playing:
        if (_pending.isEmpty) return null;
        return _pending.takeBytes();
    }
  }

  /// Reports that the native queue drained mid-turn while more speech is still
  /// expected. Drops back to buffering with the smaller [rebufferMs] cushion so
  /// the next burst is re-cushioned. No-op unless currently playing.
  void playbackUnderrun() {
    if (_state != JitterState.playing) return;
    _state = JitterState.buffering;
    _thresholdMs = rebufferMs;
    _firstChunkAt = -1;
  }

  /// Barge-in / flush: drops everything and returns to idle so the next turn
  /// re-earns the full [prebufferMs] cushion.
  void reset() {
    _state = JitterState.idle;
    _thresholdMs = prebufferMs;
    _firstChunkAt = -1;
    _pending.clear();
  }
}
