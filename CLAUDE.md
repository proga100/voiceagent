# Alomat voice agent — Claude Code instructions

Real-time **Uzbek** plant-doctor voice agent for farmers. FastAPI backend on
**Gemini Live** (STT + reasoning + voice) with Azure Neural TTS for the native
uz-UZ voice, and a Flutter app that streams mic audio over one WebSocket and
plays the agent's voice back through a lipsynced 3D avatar ("Alomat").
Production: https://voi.flance.info. Client-facing docs live in
[README.md](README.md), [backend/README.md](backend/README.md),
[mobile/README.md](mobile/README.md), [docs/multichat_contract.md](docs/multichat_contract.md).

## Commands

```bash
# Backend (Python 3.12) — port 8012 avoids the other agents on this machine
cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8012 --env-file ../.env
cd backend && pytest                 # unit tests, no network (app/voice/tests)
cd backend && python scripts/export_openapi.py   # refresh docs/openapi.json for the mobile team

# Browser testers
open http://localhost:8012/tester                # built-in WS tester (backend/app/static/ws_tester.html)
docker compose up --build                        # backend :8012 + static test client :3010

# Mobile (Flutter)
cd mobile && flutter pub get
cd mobile && flutter analyze && flutter test
cd mobile && flutter run --dart-define=WS_URL=ws://<mac-lan-ip>:8012/ws/voice --dart-define=WS_TOKEN=change-me-dev-token
cd mobile && WS_TOKEN=<server VOICE_API_TOKEN> ./build_prod_apk.sh   # the ONLY way to build a shippable APK

# Production (backend only — see the deploy skill)
./deploy/update.sh
```

## Hard rules

- **Ask before anything leaves this machine.** `git push`, `deploy/update.sh`,
  and `adb install` all wait for the user's explicit OK — they review first.
  Commits, tests, `flutter analyze` and read-only probes are fine without asking.
- **NDA: this repo is the voice agent only.** Never copy code from, or add
  paths that point into, the Growz platform repos (`growz`, `growz_ai`). The
  Growz API is consumed over HTTP (`GROWZ_API_URL`, `x-api-key`) — nothing else.
- **Never commit secrets or runtime data.** `.env`, `data/` (farmer memory,
  chats, photos), `gcloud-sa.json`, `*.apk`, `.claude/settings.local.json`.
  A PreToolUse hook refuses to stage them; keep it that way.
- **`deploy/update.sh` never touches the server's `.env`** (rsync excludes it).
  Keys, `VOICE_API_TOKEN`, and the Azure key are edited in
  `/opt/voiceagent-google/.env` on the server, then `docker compose -f docker-compose.prod.yml up -d`.
- **APKs must be built with `--dart-define`.** A bare `flutter build apk`
  silently bakes in the emulator host (`ws://10.0.2.2:8012`) + dev token and
  the app hangs on "Ulanmoqda…" forever. Use `mobile/build_prod_apk.sh`.
- **No force pushes** from an agent session.
- **Uzbek only, fail loudly.** Google Cloud TTS has no `uz-UZ` voice. Do not
  invent one or fall back to Russian/Turkish; the staged path raises
  `UzbekTTSUnavailable` on purpose.

## Architecture

```
backend/app/
  main.py                 FastAPI factory: /health, /ws/voice, /chats/*, /crops, /tester, Swagger
  config.py               pydantic Settings — every env var, with the reasoning inline
  auth.py                 ?token= check on the WebSocket (VOICE_API_TOKEN)
  schemas.py              every /ws/voice JSON control event (surfaces in Swagger)
  api_schemas.py          REST request bodies
  voice/pipeline/         voice_agent.py routes Live vs staged; streaming_session.py = staged path;
                          tools.py (request_photo / finalize_case), diagnosis.py, memory.py,
                          photo_*.py (quality, vision check, store), prompts.py, chunker.py
  voice/providers/        gemini_live, google_stt (Chirp 3 scribe), google_gemini, azure tts, base.py Protocols
  voice/chat/             multichat ChatDoc store (files | postgres) + guided flow
  voice/enrich/           crop picker (Growz catalogue), weather (Open-Meteo), date
  voice/agronom/          agronom second-opinion stub (off by default)
  voice/tests/            pytest, no network
backend/prompts/voice_agent.md   tunable system prompt; tool policy appended unless <!-- tools-included -->
mobile/lib/
  core/config.dart        WS_URL / WS_TOKEN dart-defines; httpBaseFromWs() derives the REST base
  core/ws, core/audio     VoiceSocket, MicStreamer (3200-byte frames), PcmPlayer + JitterBuffer, LipsyncAnalyzer
  features/               interview (avatar + transcript), camera (quality-gated capture), chat, crop, diagnosis, session
deploy/update.sh          rsync + docker compose rebuild + /health wait on root@flance.info:/opt/voiceagent-google
deploy/nginx-voi.conf     RunCloud vhost: static client behind basic auth, /ws/voice + /health proxied to :8014
```

Wire protocol: binary WS frames = PCM16 16 kHz mic audio (exactly 3200 bytes =
100 ms); JSON frames = control events (`chat.start`, `photo.upload`,
`tool.request_photo`, `photo.received`, `diagnosis`, …). Agent audio comes back
as PCM16 24 kHz. The mobile app is a thin client: all AI runs server-side, no
keys ship in the APK.

## Models & voices (verified on this key — don't "upgrade" blindly)

| Purpose | Setting | Value | Note |
|---|---|---|---|
| Live conversation | `GEMINI_LIVE_MODEL` | `gemini-3.1-flash-live-preview` | half-cascade, ~1.3 s to first audio |
| Diagnosis | `DIAGNOSIS_MODEL` | `gemini-3.1-pro-preview` | bare `gemini-3.1-pro` **404s** |
| Text/extraction | `GEMINI_MODEL` | `gemini-3.5-flash` | thinking disabled for latency |
| Subtitle scribe | `GOOGLE_STT_MODEL` | `chirp_3` @ region `eu` | uz-UZ only from multi-region `eu`/`us` endpoints |
| Native voice | `GEMINI_LIVE_VOICE` | `azure:uz-UZ-SardorNeural` / `MadinaNeural` | needs `AZURE_SPEECH_KEY` |
| Fallback voice | `GEMINI_LIVE_VOICE` | `Charon` (male) | Gemini's own voice — accented Uzbek |

## Known production state

- **Azure Speech key is revoked (since 2026-07-30).** Prod auto-falls back to
  Gemini's `Charon` voice (accented). Restoring the native voice = new key from
  portal.azure.com into the server `.env` + container restart. No code change,
  no APK rebuild can fix it.
- **Growz tenant OTP verify returns 500 on a correct code** (Growz backend bug),
  so `tenant_crops_source=mock` — the farmer's real crops can't be fetched yet.
  `backend/scripts/live_growz_tenant_crops.py` is the ready-to-run check.
- **Stale local `:8012` server.** A weeks-old uvicorn accepts the WS handshake
  and then emits nothing. `ps aux | grep uvicorn` and restart it before
  debugging the socket.
- `/ws/voice` without a token returns HTTP 403 (closed pre-accept) — that is
  correct behaviour, not an outage. Prod health: `curl https://voi.flance.info/health`.

## Conventions

- Settings live in `config.py` with the *why* in a comment next to each field;
  new env vars get the same treatment and a line in `.env.example`.
- Feature flags default to the safe/legacy behaviour when flipped off
  (`ENABLE_CASE_TOOLS`, `CHATS_ENABLED`, `MEMORY_ENABLED`, `AGRONOM_ENABLED`).
- Storage paths (`data/chats`, `data/memory`, `data/photos`) are anchored to
  the backend dir in a model validator — don't resolve them against CWD.
- Providers implement the Protocols in `voice/providers/base.py`; pipeline
  code never imports a concrete provider directly.
- Tests never hit the network. Mock providers; the POC scripts under
  `voice/benchmark/` are the only place real credentials are used.
- Mobile: Riverpod notifiers, sealed `ServerEvent` classes, tests under
  `mobile/test/` mirror `lib/` names (`jitter_buffer_test.dart` ↔ `jitter_buffer.dart`).
- UI copy is Uzbek (Latin); Cyrillic transliteration exists for guided-flow
  prompts and labels — keep both in sync when adding strings.
