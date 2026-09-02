---
name: local-dev
description: Run and debug the voice agent locally — start the backend, drive a session from the browser tester, read events, and diagnose a silent or broken WebSocket. Use when the user says run it, start the server, test the voice flow, the socket is dead, no audio, or wants to reproduce a conversation without the phone.
---

# Local development loop

## Start

```bash
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8012 --env-file ../.env
```

- `.env` needs at least `GEMINI_API_KEY`; leave `VOICE_API_TOKEN` empty to
  open `/ws/voice` without a token locally.
- Port **8012** on purpose — 8000/8010 belong to the Yandex agent on this Mac.
- Swagger with every WS control event: http://localhost:8012/docs

## Drive a session without the phone

http://localhost:8012/tester — the built-in tester
(`backend/app/static/ws_tester.html`). It runs the whole guided flow, shows
every event, plays the agent's audio, and has mute/unmute + the case-tools
(photo / diagnosis) panel. The static client at `frontend/test-client/`
(`docker compose up` → :3010) is the older browser client with the 3D avatar.

## The socket is "dead" — check in this order

1. **Is the server stale?** `ps aux | grep uvicorn` and look at the start
   date. A weeks-old process accepts the 101 handshake and then emits nothing
   — identical symptoms to broken code. Restart it (or start a second
   instance on a free port with logs to a file) before touching the pipeline.
2. **Tokenless 403?** That is correct: `auth.py` closes pre-accept. Add
   `?token=` or blank `VOICE_API_TOKEN`.
3. **Handshake OK, no `llm.token` after `chat.start` + `text.input`?** Gemini
   Live rejected the session — check the key, the model name
   (`gemini-3.1-flash-live-preview`), and quota in AI Studio. Logs say which.
4. **Audio in, nothing back?** Frames must be exactly 3200 bytes of PCM16
   16 kHz; the input gate (`VOICE_INPUT_GATE_THRESHOLD`) may be eating a quiet
   mic — lower it or set `VOICE_INPUT_GATE_ENABLED=false` to test.
5. **Voice sounds accented?** Azure key missing/revoked → Gemini fallback.
   Expected until a new key lands; `/health` shows `voice`.

## Tests

```bash
cd backend && pytest                       # no network, ~seconds
cd backend && pytest app/voice/tests/test_chunker.py -q
cd mobile && flutter analyze && flutter test
```

POC scripts that DO hit Google (only with real creds, never in tests):
`python -m app.voice.benchmark.poc_live_audio`, `poc_stt_uz`, `poc_live_tools`.

## Tuning without a redeploy

- System prompt: `backend/prompts/voice_agent.md` (tool policy appended unless
  the file carries `<!-- tools-included -->`).
- Every knob is an env var documented in `backend/app/config.py` — read the
  comment next to the field before changing a default.
