# Plant Doctor — Alomat (mobile)

Flutter app for Uzbek farmers: talk to the 3D avatar **Alomat** about a plant
problem, get interviewed by voice, photograph the affected part with guided
camera capture, and receive a spoken + on-screen diagnosis.

The app is a **thin realtime client**: it streams microphone audio up and plays
agent voice down over **one** WebSocket; all AI (Gemini Live STT+LLM, Azure
Uzbek TTS, `gemini-3.1-pro-preview` diagnosis) runs on the FastAPI backend in
`../backend`. No API keys ship in the app.

## Run

```bash
cd mobile
flutter pub get

# Physical device on your LAN (find your Mac IP: ipconfig getifaddr en0):
flutter run \
  --dart-define=WS_URL=ws://<mac-lan-ip>:8012/ws/voice \
  --dart-define=WS_TOKEN=change-me-dev-token
```

- The **default** `WS_URL` is `ws://10.0.2.2:8012/ws/voice`, which is the host
  machine as seen from the **Android emulator** — no define needed there.
- The default `WS_TOKEN` is the backend dev token `change-me-dev-token`.
- Cleartext `ws://` is enabled for dev (`usesCleartextTraffic="true"`).
- Start the backend first: `cd ../backend && uvicorn app.main:app --port 8012 --env-file ../.env`.

## The three modes (single screen)

```
INTERVIEW ──tool.request_photo──► CAMERA ──auto-capture──► CONFIRM
    ▲  full-screen avatar +          │  avatar shrinks to      │ checkmark,
    │  live transcript               │  PiP circle; quality    │ compress,
    └────────── photo.received ◄─────┴──border + hints         │ upload
                (image bubble)                                 ▼
                                                        back to INTERVIEW
```

- **INTERVIEW** — 3D Alomat (WebView, lipsynced to agent audio), live
  transcript below (farmer STT partials, agent tokens, system notes, photo
  bubbles, diagnosis card).
- **CAMERA** — opens when the agent calls `request_photo`. Green border when
  all quality gates pass, else red + an Uzbek hint:
  1. blur — 3×3 Laplacian variance ≥ 100 (pure Dart, isolate);
  2. exposure — histogram, >40% near-black/near-white rejected;
  3. plant-in-frame — on-device ML Kit image labeling (never blocks capture
     on ML failure; single `plantCheckEnabled` const).
  1.5 s of continuous green auto-captures. Gallery picker goes through the
  same checks. Cancel sends `camera.cancelled` so the agent adapts by voice.
  Bad-quality states are reported (`photo.quality`, throttled) and Alomat
  coaches by voice.
- **CONFIRM** — 2 s checkmark → JPEG compress (long side ≤ 1536, q85, EXIF
  stripped, orientation baked) → `photo.upload` (mic frames paused) → waits
  for `photo.received` → image bubble → back to INTERVIEW.

## Architecture

```
mic ──► MicStreamer (record) ──reframe 3200B──► VoiceSocket.sendAudio ──┐
                                                                        ▼
                                            ws://…/ws/voice  ◄── session.start
                                                                        ▲
speaker ◄─ PcmPlayer (flutter_pcm_sound) ◄─ VoiceSocket.audio ◄─────────┘
                 │
        LipsyncAnalyzer (20ms RMS + 3-band IIR, timestamped FIFO)
                 │            synced to PcmPlayer.playbackPositionMs
                 ▼
        AvatarWebView (three.js + nigora-3d.glb, avatarDrive @≤30Hz)

VoiceSocket.events (sealed ServerEvent) ──► VoiceSessionController
        │                                        │
        ▼                                        ▼
 TranscriptProvider                       AppMode (Interview/Camera/Confirm)
 (bubbles + diagnosis card)                      │
        └────────────► InterviewScreen ◄─────────┘
                        (Stack: mode content under always-mounted PiP avatar)
```

### Layers (`lib/`)

- `core/config.dart` — compile-time `WS_URL` / `WS_TOKEN` / voice / rates / frame size.
- `core/protocol/events.dart` — sealed `ServerEvent` hierarchy + `ClientEvent` builders.
- `core/ws/voice_socket.dart` — one WebSocket; text→events, binary→audio; reconnect w/ backoff.
- `core/audio/mic_streamer.dart` — PCM16 @16 kHz reframed to exact 3200-byte frames;
  `voiceCommunication` source + echo cancellation (the speaker plays while the mic runs).
- `core/audio/pcm_player.dart` — playback @24 kHz, odd-byte carry, instant `flush()`
  on barge-in, `playbackPositionMs` for the lipsync cursor.
- `core/audio/lipsync_analyzer.dart` — real DSP: 480-sample windows, RMS→mouth-open
  with attack/decay, low/mid/high band shares via one-pole filters, timestamped FIFO.
- `features/session/` — `AppMode` state machine + `VoiceSessionController`
  (socket/mic/player lifecycle, event routing, photo upload w/ mic pause).
- `features/interview/` — screen, transcript, `AvatarWebView`
  (loads `assets/avatar/avatar.html`, `AvatarBridge` ready/error channel,
  33 ms ticker drives `avatarDrive(open,low,mid,high)`).
- `features/camera/` — `camera_screen` (preview + border + hints + countdown),
  `quality/frame_quality.dart` + `quality/green_gate.dart` (pure, unit-tested),
  `quality_provider` (frame throttling, ML Kit, auto-capture, quality events),
  `confirm_overlay` (compress + upload).
- `features/diagnosis/diagnosis_card.dart` — confidence chip, treatment /
  prevention sections, differential chips — all labels Uzbek.

### Avatar assets

`assets/avatar/` (avatar.html + `nigora-3d.glb` + vendored three.js) is
**copied from `../frontend/test-client/`**, which stays the source of truth.
If the web avatar changes, re-copy:

```bash
cp ../frontend/test-client/nigora-3d.glb assets/avatar/
cp -R ../frontend/test-client/vendor/three assets/avatar/vendor/
# avatar.html is a derived file — port changes by hand (see its header comment)
```

### WebSocket protocol (fixed)

- Up (binary): PCM16 mono LE @16 kHz, exactly 3200-byte frames.
- Down (binary): PCM16 mono @24 kHz (frames may split mid-sample).
- Up (text): `session.start`, `user.interrupt`, `session.end`,
  `photo.upload`, `photo.quality`, `camera.cancelled`.
- Down (text): `stt.partial`, `llm.token`, `tts.started`, `tts.finished`,
  `agent.interrupted`, `usage`, `usage_azure`, `error`, `tool.request_photo`,
  `tool.cancelled`, `photo.received`, `diagnosis.started`, `case.diagnosis`.
  Unknown types are ignored.

## Tests

```bash
flutter test   # 62 tests
```

- `events_test.dart` — `ServerEvent.fromJson` for every server event.
- `reframer_test.dart` / `pcm_player_test.dart` — audio framing + odd-byte carry.
- `lipsync_analyzer_test.dart` — band separation (200 Hz vs 3 kHz), timing, reset.
- `frame_quality_test.dart` — blur/exposure verdicts on synthetic luma grids.
- `auto_capture_test.dart` — 1.5 s green-gate fire/reset/re-arm + quality throttle.

## On-device checklist (manual)

1. Echo test: full speaker volume — the agent must not interrupt itself.
2. Barge-in: speak over Alomat — audio stops < 150 ms, mouth snaps shut.
3. Avatar: GLB loads < 4 s on a mid-range device (placeholder until ready).
4. Camera: blur / dark / bright / no-plant each show red + correct hint;
   steady framing auto-captures at 1.5 s.
5. Full loop: interview → photo(s) → diagnosis card + spoken summary.

## iOS (deferred)

Code is iOS-compatible but untested. Before an iOS build: add
`NSCameraUsageDescription` / `NSPhotoLibraryUsageDescription` to Info.plist
and the `PERMISSION_CAMERA` / `PERMISSION_PHOTOS` macros to the Podfile.
