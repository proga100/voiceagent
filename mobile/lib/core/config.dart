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

/// HTTP(S) origin of the same backend, derived from [wsUrl] — the REST plane
/// (e.g. `GET /crops`). `wss://host/ws/voice` → `https://host`,
/// `ws://host:8012/ws/voice` → `http://host:8012`. No separate dart-define.
String get httpBaseUrl {
  final u = Uri.parse(wsUrl);
  final scheme = u.scheme == 'wss' ? 'https' : 'http';
  final port = u.hasPort ? ':${u.port}' : '';
  return '$scheme://${u.host}$port';
}

/// TTS voice id sent in `session.start`. `azure:<neural-voice>` picks the Azure
/// speech backend on the server. Sardor = the male uz-UZ voice (Rais persona).
const String defaultVoice = 'azure:uz-UZ-SardorNeural';

/// BCP-47 language tag sent in `session.start`.
const String defaultLanguage = 'uz-UZ';

/// Microphone capture rate — the backend expects PCM16 mono @ 16 kHz.
const int micSampleRate = 16000;

/// Agent voice playback rate — the backend streams PCM16 mono @ 24 kHz.
const int playbackSampleRate = 24000;

/// Exact size of one outgoing mic frame in bytes: 100 ms @ 16 kHz mono PCM16
/// (`16000 * 0.1 * 2`). The backend requires this frame size exactly.
const int micFrameBytes = 3200;
