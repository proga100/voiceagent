/// Compile-time configuration for the realtime voice client.
///
/// Everything here is fixed at build time via `--dart-define`, so the thin
/// client can be pointed at a LAN backend without code changes. The defaults
/// target the Android emulator (`10.0.2.2` is the host machine as seen from the
/// emulated device).
library;

/// WebSocket endpoint of the FastAPI voice backend.
///
/// Override with `--dart-define=WS_URL=ws://<mac-lan-ip>:8012/ws/voice` when
/// running on a physical device.
const String wsUrl = String.fromEnvironment(
  'WS_URL',
  defaultValue: 'ws://10.0.2.2:8012/ws/voice',
);

/// Bearer token appended to the WS URL as `?token=`. The backend dev default is
/// `change-me-dev-token`.
const String wsToken = String.fromEnvironment(
  'WS_TOKEN',
  defaultValue: 'change-me-dev-token',
);

/// HTTP(S) base of the same backend, derived from [wsUrl] — the REST plane
/// (e.g. `GET /crops`). No separate dart-define.
///
/// Only the `/ws/voice` suffix is dropped; any prefix in front of it is KEPT,
/// because the backend may be mounted under one. Since the Phase 4 merge the
/// voice agent runs inside growz-ai under `/voice`, so its REST plane lives at
/// `<host>/voice/chats`. Dropping the whole path would aim every REST call at
/// growz-ai's own root, where `/chats` and `/crops` are 404.
///
/// `wss://voi.flance.info/ws/voice`        → `https://voi.flance.info`
/// `wss://test-ai.growz.io/voice/ws/voice` → `https://test-ai.growz.io/voice`
/// `ws://10.0.2.2:8012/ws/voice`           → `http://10.0.2.2:8012`
String get httpBaseUrl => httpBaseFromWs(wsUrl);

/// The derivation behind [httpBaseUrl], as a pure function so it is testable
/// without rebuilding with a different `--dart-define`.
String httpBaseFromWs(String ws) {
  final u = Uri.parse(ws);
  final scheme = u.scheme == 'wss' ? 'https' : 'http';
  final port = u.hasPort ? ':${u.port}' : '';
  const suffix = '/ws/voice';
  final path = u.path.endsWith(suffix)
      ? u.path.substring(0, u.path.length - suffix.length)
      : u.path;
  return '$scheme://${u.host}$port$path';
}

// defaultVoice removed 2026-08-05 — the voice is a SERVER setting now
// (GEMINI_LIVE_VOICE in .env, which still accepts `azure:uz-UZ-SardorNeural`).
// The app no longer sends it.

/// BCP-47 language tag sent in `chat.start`.
const String defaultLanguage = 'uz-UZ';

/// Microphone capture rate for local capture. NOT sent to the server any
/// more — the backend takes its own AUDIO_INPUT_SAMPLE_RATE_HZ (2026-08-05).
const int micSampleRate = 16000;

/// Agent voice playback rate — the backend streams PCM16 mono @ 24 kHz.
const int playbackSampleRate = 24000;

/// Exact size of one outgoing mic frame in bytes: 100 ms @ 16 kHz mono PCM16
/// (`16000 * 0.1 * 2`). The backend requires this frame size exactly.
const int micFrameBytes = 3200;
