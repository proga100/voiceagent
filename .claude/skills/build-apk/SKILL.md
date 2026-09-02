---
name: build-apk
description: Build, install and run the Flutter mobile app (Alomat) against dev or production. Use when the user asks to build an APK, run the app on a phone or emulator, install to a device, or the app cannot connect / hangs on "Ulanmoqda…".
---

# Building and running the mobile app

The app is a thin client: `WS_URL` and `WS_TOKEN` are baked in at **build
time** via `--dart-define` (`mobile/lib/core/config.dart`). Nothing else is
configurable at runtime — no settings screen, no keys in the APK.

## Production APK (the only shippable build)

```bash
cd mobile
WS_TOKEN=<server VOICE_API_TOKEN> ./build_prod_apk.sh
# → build/app/outputs/flutter-apk/app-release.apk, wired to wss://voi.flance.info/ws/voice
```

The token must equal `VOICE_API_TOKEN` in `/opt/voiceagent-google/.env` on
the server. Get it with
`ssh root@flance.info 'grep ^VOICE_API_TOKEN /opt/voiceagent-google/.env'`
— never paste it into a file that is tracked.

**Never ship a bare `flutter build apk`.** Without the defines the APK
silently targets the Android emulator host (`ws://10.0.2.2:8012`) with the
dev token, and on a real phone it spins on "Ulanmoqda…" forever. This has
happened once already.

## Install

Ask the user before installing to their device:

```bash
adb devices
adb install -r mobile/build/app/outputs/flutter-apk/app-release.apk
```

## Dev run

```bash
# Backend first
cd backend && uvicorn app.main:app --reload --port 8012 --env-file ../.env

# Emulator: defaults already point at the host machine, no defines needed
cd mobile && flutter run

# Physical phone on the same Wi-Fi
cd mobile && flutter run \
  --dart-define=WS_URL=ws://$(ipconfig getifaddr en0):8012/ws/voice \
  --dart-define=WS_TOKEN=change-me-dev-token
```

Cleartext `ws://` is enabled for dev builds (`usesCleartextTraffic`). If the
backend is mounted under a prefix, keep it in `WS_URL`
(`wss://host/voice/ws/voice`) — `httpBaseFromWs()` derives the REST base from
it and the `/chats`, `/crops` calls will 404 otherwise.

## Before handing over an APK

```bash
cd mobile && flutter analyze && flutter test
```

Bump `version:` in `pubspec.yaml` when the protocol changed (new events in
`backend/app/schemas.py`) so a stale install is recognisable.

## Avatar rebuild (rare)

The 3D avatar is a prebuilt bundle under `mobile/assets/avatar/`. Only rebuild
when `mobile/avatar_src/` changed: `cd mobile/avatar_src && ./build.sh`.
