# Google Uzbek Voice Agent

Real-time, conversational **Uzbek** voice agent on **Google** — a clone of the
Yandex `nigora` pipeline with the provider seam swapped to Google.

## ⚠️ About the "Uzbek voice" on Google

**Google Cloud Text-to-Speech has NO Uzbek voice** — not in Standard, WaveNet,
Neural2, Chirp3-HD, or Gemini-TTS. There is no `uz-UZ-*` Cloud TTS voice to
name, and this project never hardcodes a fake one or silently substitutes
Russian/Turkish.

The supported Google path to **spoken Uzbek** is the **Gemini Live API**: Uzbek
is in its language list, and the half-cascade Live model honours an explicit
`language_code=uz-UZ`. So:

```
Browser mic (PCM16 16k) → WebSocket
  → Gemini Live API (audio-in → audio-out, language_code=uz-UZ)   [default path]
  → input/output transcription → stt.* / llm.token events
  → native Uzbek audio (24 kHz) back → WebAudio playback
```

Recognition-only on Google Cloud is also available: **Cloud Speech-to-Text v2**
supports `uz-UZ` via the `chirp` / `chirp_2` models (regions `asia-southeast1`,
`europe-west4`).

### Two pipeline shapes (`USE_GEMINI_LIVE_AUDIO`)

| Mode | Flow | Uzbek voice from |
|---|---|---|
| **Live** (default) | Gemini Live realtime audio-in/out | Gemini Live `uz-UZ` |
| **Staged** | Cloud STT `uz-UZ` → Gemini text → TTS | `TTS_PROVIDER` (below) |

`TTS_PROVIDER` for the staged path:
- `gemini_live` — synthesize each sentence through a one-shot Live turn (Uzbek ✅)
- `external` — POST to `EXTERNAL_TTS_URL` (e.g. an Azure `uz-UZ` voice or a
  self-hosted Chatterbox/FeruzaSpeech service)
- `none` — **fails loudly** (`UzbekTTSUnavailable`); never substitutes a language

## Quick start (local)

```bash
cp .env.example .env       # set GEMINI_API_KEY (+ GOOGLE_PROJECT_ID/ADC for STT), VOICE_API_TOKEN
docker compose up --build
# Ports 8012/3010 so this runs alongside the Yandex agent (8000/3000, 8010).
# backend: http://localhost:8012  (health: /health)
# test client: http://localhost:3010  (append ?token=<VOICE_API_TOKEN>)
```

Run backend without Docker (Python 3.12):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8012 --env-file ../.env
```

## API docs (Swagger)

- Swagger UI: `http://localhost:8012/docs` — REST endpoints plus, under
  **Schemas**, every `/ws/voice` JSON control event (`app/schemas.py`); the
  WS framing/auth contract is in the page's description.
- Also served: `/redoc` and the raw spec at `/openapi.json`.
- Export a diffable copy for the mobile team:

```bash
cd backend && python scripts/export_openapi.py   # writes ../docs/openapi.json
```

## Proof-of-concept tests (need real creds)

```bash
cd backend
# 1) Prove Gemini Live speaks Uzbek → writes poc_live_uzbek.wav
python -m app.voice.benchmark.poc_live_audio
# 2) Prove Cloud STT transcribes Uzbek (16 kHz mono WAV)
python -m app.voice.benchmark.poc_stt_uz path/to/uzbek_16k_mono.wav
```

## Unit tests (no network)

```bash
cd backend && pytest
```

## Open items to confirm on a real account

- Gemini Live half-cascade emits intelligible Uzbek with `language_code=uz-UZ`
  (list membership ≠ guaranteed quality).
- `chirp_2 uz-UZ` is available for **streaming** in the chosen region.
- Billing/quota enabled for Speech-to-Text v2 + Gemini Live.

## Layout

`backend/app/voice/` — `providers/` (Google wrappers: `google_stt`,
`google_gemini`, `gemini_live`, `gemini_tts`, `google_auth`, `google_translate`),
`pipeline/` (`streaming_session.py` orchestrates the staged path; `voice_agent.py`
routes Live vs staged), `vad/`, `benchmark/` (POC scripts), `tests/`. The
pipeline core is provider-agnostic (typed against `providers/base.py` Protocols).
See `../MIGRATION_PLAN_GOOGLE.md`.
