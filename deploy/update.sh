#!/usr/bin/env bash
#
# One-command backend redeploy to voi.flance.info.
#
#   ./deploy/update.sh
#
# Syncs the local backend + prod config to the server, rebuilds the Docker
# image, restarts the container, and waits for /health. The server-side .env
# (secrets: Gemini/Azure keys, VOICE_API_TOKEN) is NEVER touched — it is
# excluded from the sync so production secrets survive every deploy.
#
# NOTE: this redeploys the BACKEND only. The mobile app is a separate APK — a
# Dart/Flutter change needs a fresh `flutter build apk` + reinstall, not this.
#
# Override the target with env vars if the box ever changes:
#   SERVER=root@flance.info  REMOTE_DIR=/opt/voiceagent-google  PORT=8014
set -euo pipefail

SERVER="${SERVER:-root@flance.info}"
REMOTE_DIR="${REMOTE_DIR:-/opt/voiceagent-google}"
PORT="${PORT:-8014}"
COMPOSE="docker-compose.prod.yml"

# Repo root = parent of this script's dir, regardless of where it's called from.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Deploying $REPO_ROOT  ->  $SERVER:$REMOTE_DIR"

# 1. Sync code. --delete keeps the server a mirror of the repo, but every
#    exclude is also protected from deletion, so .env and runtime dirs stay put.
echo "==> Syncing code (rsync)..."
rsync -az --delete \
  --exclude 'mobile/' --exclude '.git/' --exclude '.venv/' --exclude 'backend/.venv/' \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude '.dart_tool/' \
  --exclude 'build/' --exclude 'node_modules/' --exclude '*.wav' \
  --exclude '.env' --exclude '*.apk' --exclude 'data/' \
  ./ "$SERVER:$REMOTE_DIR/"

# 2. Rebuild + restart the container on the server.
echo "==> Rebuilding + restarting container..."
ssh "$SERVER" "cd $REMOTE_DIR && docker compose -f $COMPOSE up -d --build"

# 3. Wait for health (up to ~40s), then report.
echo "==> Waiting for /health..."
ok=""
for i in $(seq 1 20); do
  if ssh "$SERVER" "curl -sf -m 4 http://127.0.0.1:$PORT/health >/dev/null 2>&1"; then
    ok=1; break
  fi
  sleep 2
done

if [ -n "$ok" ]; then
  echo "==> OK — backend healthy on 127.0.0.1:$PORT"
  echo "==> Public check:"
  curl -s -m 8 -o /dev/null -w "    https://voi.flance.info/health -> HTTP %{http_code}\n" https://voi.flance.info/health || true
  echo "==> Done. (Mobile app unchanged — rebuild the APK separately if Dart code changed.)"
else
  echo "!! /health did not come up in time. Last container logs:" >&2
  ssh "$SERVER" "cd $REMOTE_DIR && docker compose -f $COMPOSE logs --tail 30" >&2
  exit 1
fi
