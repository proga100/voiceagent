/// The farmer-driven photo request state.
///
/// When the backend sends `tool.request_photo` the app no longer jumps straight
/// into the camera — that stalled the UI isolate mid-speech on budget phones and
/// felt jarring. Instead the request is parked here as a [PendingPhotoRequest]
/// and the interview screen surfaces a "Rasm olish" call-to-action banner; the
/// farmer opens the camera manually when the agent has finished talking.
///
/// Lifetime of a pending request — it survives until exactly one of:
///  * the farmer opens the camera AND a photo uploads successfully
///    ([clear], called where the confirm/upload flow completes);
///  * the farmer dismisses the banner (banner ✕ → `camera.cancelled` + [clear]);
///  * a fresh `tool.request_photo` [set]s a new one, replacing it.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

/// An outstanding `request_photo` call awaiting the farmer's action.
class PendingPhotoRequest {
  const PendingPhotoRequest({
    required this.callId,
    required this.targetPart,
    required this.reason,
  });

  final String callId;
  final String targetPart;
  final String reason;
}

/// Holds the single outstanding photo request, or `null` when there is none.
class PendingPhotoRequestNotifier extends Notifier<PendingPhotoRequest?> {
  @override
  PendingPhotoRequest? build() => null;

  /// Stores (or replaces) the pending request for a `tool.request_photo`.
  void set({
    required String callId,
    required String targetPart,
    required String reason,
  }) => state = PendingPhotoRequest(
    callId: callId,
    targetPart: targetPart,
    reason: reason,
  );

  /// Drops the pending request (upload done, dismissed or server-cancelled).
  void clear() => state = null;
}

/// Provider for the pending photo request that drives the interview CTA banner.
final pendingPhotoRequestProvider =
    NotifierProvider<PendingPhotoRequestNotifier, PendingPhotoRequest?>(
      PendingPhotoRequestNotifier.new,
    );
