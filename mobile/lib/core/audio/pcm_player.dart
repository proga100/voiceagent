/// Low-latency playback of the agent's voice PCM.
///
/// The backend streams PCM16 mono @ 24 kHz. Frames may split mid-sample, so
/// [PcmByteCarry] holds the leftover odd byte and prepends it to the next
/// chunk before playback. Aligned bytes then pass through a [JitterBuffer] that
/// adds a small telephony-style cushion so a Wi-Fi hiccup does not drain the
/// native queue mid-word. [PcmPlayer] feeds the cushioned samples to
/// `flutter_pcm_sound` and supports an instant [flush] for barge-in.
library;

import 'dart:typed_data';

import 'package:flutter_pcm_sound/flutter_pcm_sound.dart';

import '../audio_debug.dart';
import '../config.dart';
import 'jitter_buffer.dart';

/// Keeps a single leftover byte between chunks so a 16-bit sample is never
/// split across a frame boundary.
class PcmByteCarry {
  int? _carry;

  /// Whether a leftover byte is currently held.
  bool get hasCarry => _carry != null;

  /// Returns an even-length slice ready to play, carrying any trailing odd byte
  /// into the next call. May return an empty list (e.g. a lone byte).
  Uint8List absorb(Uint8List incoming) {
    Uint8List data;
    if (_carry != null) {
      data = Uint8List(incoming.length + 1)
        ..[0] = _carry!
        ..setRange(1, incoming.length + 1, incoming);
      _carry = null;
    } else {
      data = incoming;
    }
    if (data.length.isOdd) {
      _carry = data[data.length - 1];
      data = Uint8List.sublistView(data, 0, data.length - 1);
    }
    return data;
  }

  /// Drops any held byte (used on flush).
  void reset() => _carry = null;
}

/// Streams PCM to the native player and tracks an approximate playback cursor.
class PcmPlayer {
  final PcmByteCarry _carry = PcmByteCarry();

  /// Jitter cushion in front of the native queue (see [JitterBuffer]). Aligned
  /// bytes go in; whatever it releases is what actually reaches the native
  /// layer, so the cursor below anchors to *its* output, not to socket ingress.
  final JitterBuffer _jitter = JitterBuffer();

  bool _isSetup = false;

  /// Total 16-bit samples handed to the *native* queue (jitter output only —
  /// buffered-but-unplayed audio must not count here or the cursor would run
  /// ahead of what is audible).
  int _samplesFed = 0;

  /// Milliseconds of audio actually handed to the native queue so far.
  int get _fedMs => (_samplesFed * 1000 / playbackSampleRate).round();

  // Flag-gated telemetry bookkeeping (all wall-clock ms). Compiled away when
  // [kAudioDebug] is false.
  int _lastWsFeedAt = 0;
  int _lastCursorLogAt = 0;

  /// Idempotent setup of the native audio player.
  Future<void> setup() async {
    if (_isSetup) return;
    // Ask for more data when the native queue drops below ~200 ms.
    FlutterPcmSound.setFeedThreshold((playbackSampleRate * 0.2).round());
    FlutterPcmSound.setFeedCallback(_onFeed);
    await FlutterPcmSound.setup(
      sampleRate: playbackSampleRate,
      channelCount: 1,
    );
    _isSetup = true;
  }

  void _onFeed(int remainingFrames) {
    // Required plugin callback. Deliberately unused: it proved unreliable on
    // real devices (never fired on a Helio G85 Honor), which is why the
    // playback cursor is wall-clock based instead.
  }

  // Wall-clock playback cursor. The plugin's low-buffer callback proved
  // unreliable on real devices (never fired on a Helio G85 Honor), so the
  // cursor cannot come from `fed - remaining`. Instead: audio plays in real
  // time from the moment the *native* feed (re)starts, so position = played-
  // before-last-drain + elapsed-since, capped at the total natively fed. The
  // clock is deliberately started only when jitter output actually reaches the
  // native layer (not on jitter ingress), so the buffered cushion does not
  // advance the cursor.
  int _basePlayedMs = 0;
  final Stopwatch _wallClock = Stopwatch();

  /// Whether the cursor has caught up to everything natively fed — i.e. the
  /// native queue has drained.
  ///
  /// This is the on-device underrun signal. The plugin's low-buffer callback
  /// never fires on budget SoCs (Helio G85), so [feed] uses this instead: if
  /// the cursor is saturated when the next WS burst arrives *and* the jitter
  /// buffer still thinks the turn is going, the queue ran dry mid-turn — report
  /// it so the next burst is re-cushioned. Cheap and side-effect free.
  bool get saturated {
    final fedMs = _fedMs;
    if (fedMs == 0) return false;
    return _basePlayedMs + _wallClock.elapsedMilliseconds >= fedMs;
  }

  /// Queues an incoming PCM chunk: odd-byte carry → jitter cushion → native
  /// queue. Only whatever the jitter buffer releases reaches the native layer
  /// (and starts the cursor clock).
  Future<void> feed(Uint8List pcm) async {
    if (!_isSetup) await setup();

    if (kAudioDebug) {
      final nowW = DateTime.now().millisecondsSinceEpoch;
      if (_lastWsFeedAt != 0 && nowW - _lastWsFeedAt > 150) {
        audioDebug('feed-gap ${nowW - _lastWsFeedAt}ms');
      }
      _lastWsFeedAt = nowW;
    }

    // Odd-byte carry BEFORE the jitter buffer so 16-bit alignment survives the
    // buffer concatenating bursts together.
    final aligned = _carry.absorb(pcm);

    // Underrun heuristic: the queue drained mid-turn (see [saturated]). Drop the
    // jitter buffer back to buffering so this next burst re-cushions.
    if (saturated && _jitter.isPlaying) {
      if (kAudioDebug) {
        audioDebug('jb underrun pos=${_basePlayedMs + _wallClock.elapsedMilliseconds} fed=$_fedMs');
      }
      _jitter.playbackUnderrun();
    }

    _jitter.feed(aligned);
    final wasBuffering = _jitter.isBuffering;
    final bufMs = kAudioDebug ? _jitter.bufferedMs : 0;
    final out = _jitter.drain();
    if (out == null || out.isEmpty) return;
    if (kAudioDebug && wasBuffering) audioDebug('jb play buffered=${bufMs}ms');

    // First native feed of this (re)buffered burst → (re)start the clock so the
    // cursor anchors to when audio actually reaches the native layer.
    if (!_wallClock.isRunning) _wallClock.start();

    // Copy into a tight buffer so PcmArrayInt16 wraps exactly these bytes.
    final tight = Uint8List.fromList(out);
    _samplesFed += tight.length ~/ 2;
    await FlutterPcmSound.feed(
      PcmArrayInt16(bytes: ByteData.sublistView(tight)),
    );
    // Restart playback if the queue had drained.
    FlutterPcmSound.start();

    if (kAudioDebug) {
      final nowW = DateTime.now().millisecondsSinceEpoch;
      if (nowW - _lastCursorLogAt >= 500) {
        _lastCursorLogAt = nowW;
        final pos = _basePlayedMs + _wallClock.elapsedMilliseconds;
        audioDebug('cur pos=${pos < _fedMs ? pos : _fedMs} fed=$_fedMs');
      }
    }
  }

  /// Instantly stops and clears playback, then re-arms for the next turn.
  /// Called on barge-in ([UserInterrupt]) and [AgentInterrupted].
  Future<void> flush() async {
    _jitter.reset();
    _carry.reset();
    _samplesFed = 0;
    _basePlayedMs = 0;
    _wallClock
      ..stop()
      ..reset();
    if (!_isSetup) return;
    try {
      await FlutterPcmSound.release();
    } catch (_) {
      // Ignore release races.
    }
    _isSetup = false;
    await setup();
  }

  /// Approximate ms of audio actually played so far. Wall-clock based (see
  /// [_wallClock]); saturates at the total fed and pauses across drains so
  /// the cursor never outruns the audio. Feeds the Phase-4 lipsync cursor.
  int get playbackPositionMs {
    final fedMs = _fedMs;
    final pos = _basePlayedMs + _wallClock.elapsedMilliseconds;
    if (pos < fedMs) return pos;
    // Queue drained: pin the cursor and pause the clock until the next feed.
    _basePlayedMs = fedMs;
    _wallClock
      ..stop()
      ..reset();
    return fedMs;
  }

  /// Whether agent audio is actively playing (cursor still advancing). Used to
  /// keep heavyweight camera-side work (ML Kit) out of the speech window.
  bool get isPlaybackActive {
    return _wallClock.isRunning &&
        _basePlayedMs + _wallClock.elapsedMilliseconds < _fedMs;
  }

  /// Releases native audio resources.
  Future<void> dispose() async {
    _carry.reset();
    if (_isSetup) {
      try {
        await FlutterPcmSound.release();
      } catch (_) {
        // Ignore release races.
      }
    }
    _isSetup = false;
  }
}
