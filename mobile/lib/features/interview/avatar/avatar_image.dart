/// 2D avatar (the Rais Growz character) — a single PNG animated to feel alive.
///
/// No WebView, no GLB, no real lipsync: instead the image gently "breathes"
/// while idle and pulses / bobs while Rais speaks. The speech energy is the
/// SAME signal the old three.js avatar used — `analyzer.frameAt(playback
/// positionMs).open` (0..1 mouth-openness from the agent-audio lipsync FIFO) —
/// so the motion tracks the actual voice, just as a whole-body pulse rather
/// than a moving mouth.
///
/// Driven by ONE vsync [AnimationController] (frame-synced, cheap) — not a
/// Timer — and wrapped in a [RepaintBoundary] so the ~60 Hz rebuild repaints
/// only the avatar, never the transcript/controls (budget phones scramble
/// audio if the whole tree repaints).
library;

import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../session/voice_session_controller.dart';

class AvatarView extends ConsumerStatefulWidget {
  const AvatarView({super.key, this.speaking = false});

  /// Whether a session is live (idle-breathes either way; kept for parity with
  /// the old avatar's API and possible future tinting).
  final bool speaking;

  @override
  ConsumerState<AvatarView> createState() => _AvatarViewState();
}

class _AvatarViewState extends ConsumerState<AvatarView>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;

  /// Smoothed speech energy 0..1 (lerped toward the raw lipsync `open`).
  double _energy = 0;

  @override
  void initState() {
    super.initState();
    // 3.2 s breathing cycle, repeated. Its .value (0..1) also serves as our
    // per-vsync clock for reading the live speech energy.
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 3200),
    )..repeat();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return RepaintBoundary(
      child: AnimatedBuilder(
        animation: _ctrl,
        builder: (context, _) {
          // Read the live agent-audio speech energy every frame and ease
          // toward it (attack faster than release → snappy on, soft off).
          final controller = ref.read(voiceSessionProvider.notifier);
          final frame = controller.analyzer.frameAt(
            controller.playbackPositionMs.toDouble(),
          );
          final target = (frame?.open ?? 0).clamp(0.0, 1.0);
          _energy += (target - _energy) * (target > _energy ? 0.5 : 0.15);

          // Idle breathing: slow ±0.8% scale + a sub-pixel vertical drift.
          final breathe = math.sin(_ctrl.value * 2 * math.pi);
          final breatheScale = 1.0 + breathe * 0.008;
          final breatheDy = breathe * 2.0;

          // Speaking: a firmer pulse + a small upward bob, on top of breathing.
          final speakScale = 1.0 + _energy * 0.05;
          final speakDy = -_energy * 6.0;

          return Transform.translate(
            offset: Offset(0, breatheDy + speakDy),
            child: Transform.scale(
              scale: breatheScale * speakScale,
              alignment: Alignment.bottomCenter,
              child: Image.asset(
                'assets/avatar/rais.png',
                fit: BoxFit.contain,
                filterQuality: FilterQuality.medium,
              ),
            ),
          );
        },
      ),
    );
  }
}
