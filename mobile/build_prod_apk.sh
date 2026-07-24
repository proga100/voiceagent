#!/usr/bin/env bash
#
# Build the release APK wired to PRODUCTION (voi.flance.info).
#
#   ./build_prod_apk.sh
#
# The app reads its backend URL + auth token from --dart-define at BUILD time
# (see lib/core/config.dart). A plain `flutter build apk` omits them and the
# APK silently falls back to the emulator host (ws://10.0.2.2:8012) + dev token,
# so it can never connect on a real phone. Always build the shippable APK with
# this script.
#
# The token is NOT hard-coded here — it must match the server's VOICE_API_TOKEN.
# Pass it via the WS_TOKEN env var (or a local, git-ignored token file).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

WS_URL="${WS_URL:-wss://voi.flance.info/ws/voice}"
WS_TOKEN="${WS_TOKEN:-}"

if [ -z "$WS_TOKEN" ]; then
  echo "!! WS_TOKEN is empty. Set it to the server's VOICE_API_TOKEN, e.g.:" >&2
  echo "     WS_TOKEN=xxxxxxxx ./build_prod_apk.sh" >&2
  exit 1
fi

echo "==> Building release APK for $WS_URL"
flutter build apk --release \
  --dart-define=WS_URL="$WS_URL" \
  --dart-define=WS_TOKEN="$WS_TOKEN"

echo "==> Built: build/app/outputs/flutter-apk/app-release.apk"
