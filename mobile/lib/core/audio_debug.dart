/// Flag-gated, permanent audio telemetry.
///
/// [kAudioDebug] is a compile-time constant fed by
/// `--dart-define=AUDIO_DEBUG=true`. Every telemetry call site is wrapped in
/// `if (kAudioDebug) { ... }`, so with the flag off the whole thing — string
/// building included — is tree-shaken away and costs nothing in production.
///
/// With the flag on, [audioDebug] forwards a short message through the
/// statically-registered [AudioDebug.sink]. The session controller wires that
/// sink to the live socket (as a `{"type":"debug.log","msg":...}` frame) right
/// after the socket is created, so on-device audio behaviour on a real budget
/// phone can be watched from the backend without a debugger attached.
library;

/// Whether audio telemetry is compiled in. Off by default.
const bool kAudioDebug = bool.fromEnvironment('AUDIO_DEBUG');

/// Sends [msg] to the registered sink when [kAudioDebug] is set; a no-op
/// otherwise. Call sites should still guard with `if (kAudioDebug)` so the
/// message string is never built in release builds.
void audioDebug(String msg) {
  if (!kAudioDebug) return;
  AudioDebug.sink?.call(msg);
}

/// Static registration point for the telemetry sink (the DebugTap pattern):
/// the session controller sets [sink] after creating the socket and clears it
/// on teardown.
class AudioDebug {
  AudioDebug._();

  /// Receives short telemetry strings while a session is live. `null` when no
  /// session is up (or when telemetry is compiled out).
  static void Function(String msg)? sink;
}
