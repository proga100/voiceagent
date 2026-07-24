/// Avatar view (Phase 4) — a transparent WebView hosting the three.js Alomat
/// head, driven in real time from the lipsync analyzer.
///
/// Ownership: this widget owns a ~30 Hz [Timer.periodic] ticker. Each tick it
/// reads the session controller's playback cursor and pulls the aligned
/// [LipsyncFrame] via `analyzer.frameAt(playbackPositionMs)`, then pushes it to
/// the page as `window.avatarDrive(...)`. When the FIFO drains (speech ended, or
/// barge-in cleared it) it closes the mouth once with `window.avatarSetOpen(0)`.
///
/// The widget is kept mounted across [AppMode] changes — the PiP container in
/// [InterviewScreen] only resizes it, never unmounts it, so the WebView (and its
/// costly GLB load) survives. Until the page reports `ready` (or on `error:*`)
/// the Phase-3 placeholder circle is shown on top; the avatar never crashes the
/// app if the GLB fails.
library;

import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:webview_flutter/webview_flutter.dart';

import '../../../core/audio_debug.dart';
import '../../session/voice_session_controller.dart';

/// The 3D avatar. [level]/[speaking] style the loading placeholder (mic ring).
class AvatarWebView extends ConsumerStatefulWidget {
  const AvatarWebView({
    super.key,
    this.label = 'Rais',
    this.level = 0,
    this.speaking = false,
  });

  /// Caption shown under the placeholder circle while the page loads.
  final String label;

  /// Mic input level `0..1`, drives the placeholder ring thickness/opacity.
  final double level;

  /// Whether the agent is currently speaking (tints the placeholder).
  final bool speaking;

  @override
  ConsumerState<AvatarWebView> createState() => _AvatarWebViewState();
}

enum _AvatarStatus { loading, ready, error }

class _AvatarWebViewState extends ConsumerState<AvatarWebView> {
  // ~30 Hz: matches the drive throttle ceiling and the page's smoothing.
  static const Duration _tickInterval = Duration(milliseconds: 33);

  // Skip a drive if every channel is within this of the last sent value.
  static const double _epsilon = 0.01;

  late final WebViewController _controller;
  Timer? _ticker;
  _AvatarStatus _status = _AvatarStatus.loading;

  // Drive throttle bookkeeping.
  bool _jsPending = false; // a runJavaScript is in flight — coalesce ticks
  bool _wasSpeaking = false; // drove a frame last tick → owe a close on drain
  double _lastOpen = -1, _lastLow = -1, _lastMid = -1, _lastHigh = -1;

  // Flag-gated telemetry: per-500ms drive summary (compiled away otherwise).
  int _dbgTicks = 0, _dbgNull = 0, _dbgDrives = 0, _dbgLastLogAt = 0;

  @override
  void initState() {
    super.initState();
    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(const Color(0x00000000)) // transparent → Flutter bg
      ..addJavaScriptChannel('AvatarBridge', onMessageReceived: _onBridge)
      ..loadFlutterAsset('assets/avatar/avatar.html');
    _ticker = Timer.periodic(_tickInterval, _tick);
  }

  void _onBridge(JavaScriptMessage message) {
    if (!mounted) return;
    final data = message.message;
    if (data == 'ready') {
      setState(() => _status = _AvatarStatus.ready);
    } else if (data.startsWith('error:')) {
      setState(() => _status = _AvatarStatus.error);
    }
  }

  void _tick(Timer _) {
    if (_status != _AvatarStatus.ready) return;
    final controller = ref.read(voiceSessionProvider.notifier);
    final frame = controller.analyzer.frameAt(
      controller.playbackPositionMs.toDouble(),
    );
    if (frame != null) {
      _drive(frame.open, frame.low, frame.mid, frame.high);
      _wasSpeaking = true;
    } else if (_wasSpeaking) {
      _setOpen(0); // FIFO drained (end of turn or barge-in) → close once
      _wasSpeaking = false;
    }
    if (kAudioDebug) {
      _dbgTicks++;
      if (frame == null) {
        _dbgNull++;
      } else {
        _dbgDrives++;
      }
      final nowW = DateTime.now().millisecondsSinceEpoch;
      if (nowW - _dbgLastLogAt >= 500) {
        _dbgLastLogAt = nowW;
        audioDebug(
          'tick n=$_dbgTicks null=$_dbgNull drives=$_dbgDrives '
          'pos=${controller.playbackPositionMs}',
        );
        _dbgTicks = 0;
        _dbgNull = 0;
        _dbgDrives = 0;
      }
    }
  }

  void _drive(double open, double low, double mid, double high) {
    if (_jsPending) return; // a frame is already crossing the bridge
    final o = open.clamp(0.0, 1.0);
    final l = low.clamp(0.0, 1.0);
    final m = mid.clamp(0.0, 1.0);
    final h = high.clamp(0.0, 1.0);
    if ((o - _lastOpen).abs() < _epsilon &&
        (l - _lastLow).abs() < _epsilon &&
        (m - _lastMid).abs() < _epsilon &&
        (h - _lastHigh).abs() < _epsilon) {
      return; // unchanged within epsilon — nothing to send
    }
    _lastOpen = o;
    _lastLow = l;
    _lastMid = m;
    _lastHigh = h;
    _jsPending = true;
    _controller
        .runJavaScript(
          'window.avatarDrive(${o.toStringAsFixed(3)},${l.toStringAsFixed(3)},'
          '${m.toStringAsFixed(3)},${h.toStringAsFixed(3)})',
        )
        .whenComplete(() => _jsPending = false);
  }

  void _setOpen(double v) {
    if (_status != _AvatarStatus.ready) return;
    final o = v.clamp(0.0, 1.0);
    // Force the next drive to send (bands invalidated) so speech re-opens it.
    _lastOpen = o;
    _lastLow = _lastMid = _lastHigh = -1;
    _controller.runJavaScript('window.avatarSetOpen(${o.toStringAsFixed(3)})');
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Stack(
      fit: StackFit.expand,
      children: [
        WebViewWidget(controller: _controller),
        if (_status != _AvatarStatus.ready)
          _Placeholder(
            label: widget.label,
            level: widget.level,
            speaking: widget.speaking,
            error: _status == _AvatarStatus.error,
          ),
      ],
    );
  }
}

/// Loading/error overlay: the Phase-3 circle with a mic-level ring. Sizes itself
/// to its container so it reads both full-size and as a 96 px PiP.
class _Placeholder extends StatelessWidget {
  const _Placeholder({
    required this.label,
    required this.level,
    required this.speaking,
    required this.error,
  });

  final String label;
  final double level;
  final bool speaking;
  final bool error;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final base = speaking
        ? theme.colorScheme.primary
        : theme.colorScheme.secondary;
    final lvl = level.clamp(0.0, 1.0);
    return LayoutBuilder(
      builder: (context, c) {
        final compact = c.maxHeight < 160; // PiP-sized
        final side = math.min(c.maxWidth, c.maxHeight);
        final circle = side * (compact ? 0.66 : 0.42);
        final ring = 4 + lvl * 14;
        return ColoredBox(
          color: theme.colorScheme.surface,
          child: Center(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                AnimatedContainer(
                  duration: const Duration(milliseconds: 90),
                  width: circle,
                  height: circle,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: base.withValues(alpha: 0.22),
                    border: Border.all(
                      color: base.withValues(alpha: 0.35 + lvl * 0.65),
                      width: ring,
                    ),
                  ),
                  child: Icon(
                    error ? Icons.warning_amber_rounded : Icons.eco,
                    size: circle * 0.42,
                    color: error ? theme.colorScheme.error : base,
                  ),
                ),
                if (!compact) ...[
                  const SizedBox(height: 10),
                  Text(
                    error ? 'Avatar yuklanmadi' : label,
                    style: theme.textTheme.titleMedium,
                  ),
                ],
              ],
            ),
          ),
        );
      },
    );
  }
}
