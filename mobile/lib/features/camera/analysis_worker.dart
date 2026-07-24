/// One long-lived isolate that runs the pure [analyzeFrame] off the UI isolate.
///
/// The guided camera analyses ~3 frames/second. The old code called
/// `compute(analyzeFrame, ...)` per frame, which spawns and tears down a fresh
/// isolate every time — on budget SoCs (Helio G85) those repeated spawns stall
/// the main isolate and starve audio delivery during camera use. This worker
/// spawns exactly one isolate (lazily, on the first [analyze]) and reuses it for
/// the whole camera session. Requests carry a sequence id so a late reply is
/// matched to its waiter.
///
/// It is best-effort: [analyze] rejects (throws) if the isolate cannot be
/// spawned or has died, letting the caller fall back to a plain `compute` call
/// without the camera ever crashing.
library;

import 'dart:async';
import 'dart:isolate';

import 'quality/frame_quality.dart';

/// A reusable analysis isolate. Not safe to share across threads; owned by the
/// camera quality notifier and killed in its dispose.
class AnalysisWorker {
  Isolate? _isolate;
  SendPort? _workerTx; // request port inside the worker
  ReceivePort? _fromWorker; // all worker → main traffic (handshake + replies)
  Completer<void>? _ready; // completes when the handshake arrives
  bool _dead = false;

  int _seq = 0;
  final Map<int, Completer<QualityResult>> _pending = {};

  /// Analyses [req] on the worker isolate, spawning it on first use. Throws if
  /// the worker is unavailable (spawn failed or the isolate died) so the caller
  /// can fall back to `compute`.
  Future<QualityResult> analyze(FrameAnalysisRequest req) async {
    await _ensureSpawned();
    final tx = _workerTx;
    if (tx == null || _dead) throw StateError('analysis worker unavailable');
    final seq = _seq++;
    final completer = Completer<QualityResult>();
    _pending[seq] = completer;
    tx.send([seq, req]);
    return completer.future;
  }

  Future<void> _ensureSpawned() async {
    if (_dead) throw StateError('analysis worker dead');
    if (_workerTx != null) return;
    if (_ready != null) return _ready!.future;

    final ready = Completer<void>();
    _ready = ready;
    final from = ReceivePort();
    _fromWorker = from;
    from.listen(_onMessage);
    try {
      _isolate = await Isolate.spawn(
        _workerMain,
        from.sendPort,
        onError: from.sendPort, // errors arrive as [String, String]
        onExit: from.sendPort, // exit arrives as null
      );
    } catch (_) {
      _die();
      rethrow;
    }
    return ready.future;
  }

  void _onMessage(dynamic msg) {
    if (msg is SendPort) {
      _workerTx = msg;
      if (_ready != null && !_ready!.isCompleted) _ready!.complete();
      return;
    }
    if (msg is List &&
        msg.length == 2 &&
        msg[0] is int &&
        msg[1] is QualityResult) {
      _pending.remove(msg[0] as int)?.complete(msg[1] as QualityResult);
      return;
    }
    // onExit sends `null`; onError sends `[error, stack]`. Either way the worker
    // is gone — fail everything pending and mark dead so callers fall back.
    _die();
  }

  void _die() {
    _dead = true;
    _workerTx = null;
    if (_ready != null && !_ready!.isCompleted) {
      _ready!.completeError(StateError('analysis worker died'));
    }
    for (final c in _pending.values) {
      if (!c.isCompleted) c.completeError(StateError('analysis worker died'));
    }
    _pending.clear();
    _fromWorker?.close();
    _fromWorker = null;
    _isolate = null;
  }

  /// Kills the isolate and fails any in-flight requests. Idempotent.
  void dispose() {
    _isolate?.kill(priority: Isolate.immediate);
    _isolate = null;
    _workerTx = null;
    for (final c in _pending.values) {
      if (!c.isCompleted) c.completeError(StateError('analysis worker disposed'));
    }
    _pending.clear();
    _fromWorker?.close();
    _fromWorker = null;
  }
}

/// Worker isolate entry point: hand back a request port, then answer each
/// `[seq, FrameAnalysisRequest]` with `[seq, QualityResult]`.
void _workerMain(SendPort toMain) {
  final rx = ReceivePort();
  toMain.send(rx.sendPort);
  rx.listen((msg) {
    if (msg is List && msg.length == 2 && msg[1] is FrameAnalysisRequest) {
      final seq = msg[0] as int;
      toMain.send([seq, analyzeFrame(msg[1] as FrameAnalysisRequest)]);
    }
  });
}
