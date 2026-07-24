# voiceagent-google

Uzbek voice agent on **Google** (Gemini Live + Cloud Speech-to-Text `uz-UZ`),
cloned from the Yandex `nigora` pipeline.

**Key fact:** Google Cloud TTS has no Uzbek voice — spoken Uzbek comes from the
**Gemini Live API** (`language_code=uz-UZ`). See [backend/README.md](backend/README.md)
and [MIGRATION_PLAN_GOOGLE.md](MIGRATION_PLAN_GOOGLE.md).

```bash
cp .env.example .env   # set GEMINI_API_KEY (+ GOOGLE_PROJECT_ID/ADC for STT), VOICE_API_TOKEN
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8012 --env-file ../.env   # 8012 avoids the Yandex agent (8000/8010)
```

## Plant Doctor (case tools + mobile app)

The agent can interview a farmer about a plant problem, request photos
(`request_photo` tool → guided camera in the app), and produce a structured
diagnosis (`finalize_case` tool → `gemini-3.1-pro-preview`, spoken back in
Uzbek). Gated by `ENABLE_CASE_TOOLS` (off = plain voice agent).

- **[mobile/](mobile/README.md)** — Flutter app: 3D Alomat avatar with lipsync,
  live transcript, quality-gated camera capture, diagnosis card.
- **[frontend/test-client/](frontend/test-client/)** — browser test client;
  its "Case tools (test)" panel exercises the photo/diagnosis protocol.
- Probe scripts: `backend/app/voice/benchmark/poc_live_tools.py` documents the
  Live-API tool/image mechanics (photos must use the `video` realtime channel).
