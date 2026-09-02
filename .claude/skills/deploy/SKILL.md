---
name: deploy
description: Deploy the voice-agent backend to production (voi.flance.info), verify the release, read prod logs, and roll back. Use when the user asks to deploy, ship, redeploy, restart prod, check whether a deploy worked, rotate a key on the server, or recover from a bad deploy.
---

# Deploying to voi.flance.info

Production is a Docker container on the RunCloud box `root@flance.info`
(217.216.49.28) at `/opt/voiceagent-google`. The container binds
`127.0.0.1:8014`; the host nginx (`deploy/nginx-voi.conf`) terminates TLS,
serves the static test client behind HTTP basic auth, and proxies `/ws/voice`
and `/health` without auth (the mobile app must reach them).

## Always ask first

The user reviews before anything ships. Present what will go out (the diff or
commit list), then wait for an explicit OK before running `deploy/update.sh`.
Same rule for `adb install` of a new APK.

## The deploy

```bash
./deploy/update.sh          # rsync → docker compose -f docker-compose.prod.yml up -d --build → wait for /health
```

What it does and does not do:

- **Syncs the backend only.** `mobile/` is excluded; a Dart change needs a
  fresh APK (see the `build-apk` skill), not a deploy.
- **Never touches the server `.env`** or `data/` (farmer memory, chats,
  photos). Both are rsync excludes AND bind-mounted, so redeploys can't lose
  them.
- Overridable: `SERVER=… REMOTE_DIR=… PORT=… ./deploy/update.sh`. Do not
  point it anywhere else without the user saying so.

## Verifying a release

```bash
curl -s https://voi.flance.info/health | python3 -m json.tool
# tokenless WS upgrade → 403 is CORRECT (closed pre-accept); anything else is a problem
curl -s -o /dev/null -w "%{http_code}\n" -H "Upgrade: websocket" -H "Connection: Upgrade" https://voi.flance.info/ws/voice
ssh root@flance.info 'cd /opt/voiceagent-google && docker compose -f docker-compose.prod.yml logs --since 2m'
```

Check the `voice` field in `/health`: `azure:…` means the native Uzbek voice
is live; `Charon` means the Azure key is still dead and prod is on the Gemini
fallback (accented).

## Changing secrets on the server

The server `.env` is the only copy of production secrets. Edit it in place and
restart — never upload a local `.env`:

```bash
ssh root@flance.info 'cd /opt/voiceagent-google && nano .env && docker compose -f docker-compose.prod.yml up -d'
```

`VOICE_API_TOKEN` there must equal the `WS_TOKEN` baked into the shipped APK.
Changing it means a new APK.

## When it goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `/health` never comes up after deploy | container crashed on boot | `docker compose logs` — usually a missing env var or a bad import |
| Agent speaks with an accent | Azure key revoked/401 → Gemini fallback | new key from portal.azure.com into server `.env`, restart |
| Agent silent, no events after handshake | Gemini Live session limit / key quota | logs; check `GEMINI_API_KEY` quota in AI Studio |
| Diagnosis 404s | model name drift | `DIAGNOSIS_MODEL=gemini-3.1-pro-preview` (bare `gemini-3.1-pro` 404s) |
| App stuck on "Ulanmoqda…" but `/health` fine | APK built without dart-defines or token mismatch | rebuild with `build_prod_apk.sh`, matching `VOICE_API_TOKEN` |
| Static tester 401 | nginx basic auth | `htpasswd -bB /etc/nginx-rc/voi.htpasswd <user> <pass>` |

## Rolling back

Same pipeline, previous commit:

```bash
git revert <bad-sha>     # or git checkout <good-sha> -- backend/
./deploy/update.sh       # after the user's OK
```

`data/` is untouched by rollbacks. If `chat_store=postgres` was ever enabled,
the `voice_chats` table is additive JSONB — no schema rollback needed.
