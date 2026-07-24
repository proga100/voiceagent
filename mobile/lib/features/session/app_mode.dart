/// The top-level screen mode state machine.
///
/// Phase 3 only ever renders [InterviewMode]. [CameraMode] (Phase 5 guided
/// camera) and [ConfirmMode] (Phase 5 photo confirmation) are defined now so
/// the session controller can transition into them when the backend asks for a
/// photo — the interview screen currently just flashes a notice and returns to
/// [InterviewMode].
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Sealed set of app screen modes.
sealed class AppMode {
  const AppMode();
}

/// The default voice-interview experience.
class InterviewMode extends AppMode {
  const InterviewMode();
}

/// Phase 5: the guided camera opened for a specific `request_photo` call.
class CameraMode extends AppMode {
  const CameraMode({
    required this.callId,
    required this.targetPart,
    required this.reason,
  });
  final String callId;
  final String targetPart;
  final String reason;
}

/// Phase 5: reviewing a captured photo before upload. Carries the plant part
/// and the capture [origin] (`camera` | `gallery`) so the confirm/upload step
/// can build the `photo.upload` frame without reaching back to the camera.
class ConfirmMode extends AppMode {
  const ConfirmMode({
    required this.path,
    required this.targetPart,
    required this.origin,
  });
  final String path;
  final String targetPart;
  final String origin;
}

/// Holds and mutates the current [AppMode].
class AppModeNotifier extends Notifier<AppMode> {
  @override
  AppMode build() => const InterviewMode();

  /// Returns to the interview experience.
  void toInterview() => state = const InterviewMode();

  /// Enters the guided camera for a pending photo request (Phase 5).
  void toCamera({
    required String callId,
    required String targetPart,
    required String reason,
  }) => state = CameraMode(
    callId: callId,
    targetPart: targetPart,
    reason: reason,
  );

  /// Enters photo confirmation (Phase 5).
  void toConfirm({
    required String path,
    required String targetPart,
    required String origin,
  }) => state = ConfirmMode(path: path, targetPart: targetPart, origin: origin);
}

/// Provider for the app screen mode.
final appModeProvider = NotifierProvider<AppModeNotifier, AppMode>(
  AppModeNotifier.new,
);
