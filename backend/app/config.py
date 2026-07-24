"""Application settings, loaded from environment / .env.

Single ``Settings`` model plus a cached ``get_settings()`` accessor used as a
FastAPI dependency. Field names are provider-neutral on the pipeline side
(``tts_voice``, ``audio_input_sample_rate_hz``) so the orchestration code never
hard-codes a provider; Google-specific knobs are grouped under ``google_*`` /
``gemini_*``.

Google reality (see MIGRATION_PLAN_GOOGLE.md): there is NO Google Cloud
Text-to-Speech Uzbek named voice. Spoken Uzbek on Google comes from the
**Gemini Live API** (``gemini_live`` TTS path). The staged STT->LLM->TTS path
keeps a fallback seam (``tts_provider``) for an external Uzbek voice and FAILS
LOUDLY rather than silently substituting Russian/Turkish.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Backend workdir (voiceagent/backend). The storage-path fields below default to
# values "relative to the backend workdir"; when the process runs from a
# different CWD (e.g. the growz repo root after the Phase 4 in-process mount),
# those relatives must be anchored here so they keep resolving to the same dirs.
_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    provider: Literal["google"] = "google"

    # ---- Google Cloud / Gemini auth ----
    # ADC service-account JSON path for Cloud Speech-to-Text (Vertex/Cloud APIs).
    google_application_credentials: str | None = None
    google_project_id: str = ""
    # API key for the Gemini Developer API (google-genai). If unset, google-genai
    # falls back to ADC / Vertex via google_genai_use_vertexai.
    gemini_api_key: str | None = None
    google_genai_use_vertexai: bool = False

    # ---- STT: Cloud Speech-to-Text v2 ----
    # chirp / chirp_2 are the Uzbek-capable models; they live in specific regions
    # (asia-southeast1, europe-west4). The region is part of the recognizer path
    # AND the client endpoint, so it must match a region that serves the model.
    # chirp_3 (latest, best uz-UZ) is served ONLY from the multi-region
    # endpoints "eu"/"us" — NOT specific regions or "global". The recognizer
    # path + endpoint properties below turn region="eu" into
    # eu-speech.googleapis.com automatically.
    google_stt_region: str = "eu"
    google_stt_language: str = "uz-UZ"
    google_stt_model: str = "chirp_3"

    # ---- LLM: Gemini (text) ----
    # gemini-3.5-flash is the newest text flash; thinking is disabled below for
    # voice latency (a thinking budget would eat the short max_tokens reply).
    gemini_model: str = "gemini-3.5-flash"
    gemini_temperature: float = 0.3
    gemini_max_tokens: int = 120

    # ---- Speech-out path selection ----
    # gemini_live  -> Gemini Live API native audio (ONLY Google path to Uzbek voice)
    # external     -> an external Uzbek TTS plugged into the staged pipeline
    # none         -> staged pipeline with NO voice; fails loudly when asked to speak
    tts_provider: Literal["gemini_live", "external", "none"] = "gemini_live"
    # When true, the whole conversation runs over the Gemini Live realtime session
    # (audio-in -> audio-out) instead of the staged STT->LLM->TTS pipeline.
    use_gemini_live_audio: bool = True

    # ---- Gemini Live API ----
    # Half-cascade Live model: ~1.3s to first audio vs ~3.7s for 2.5 native-audio
    # (measured), and it honours an explicit language_code=uz-UZ. Default voice
    # path. (2.5-flash-native-audio-* still works if you prefer native audio.)
    gemini_live_model: str = "gemini-3.1-flash-live-preview"
    # Brain for Azure-voice mode (STT + reply text); same half-cascade model.
    gemini_live_text_model: str = "gemini-3.1-flash-live-preview"
    gemini_live_language: str = "uz-UZ"
    # Prebuilt Live HD voice (language-agnostic timbre; Uzbek comes from the
    # language code, not the voice name). NOT a Cloud TTS voice id.
    gemini_live_voice: str = "Aoede"

    # ---- Transparent session resumption ----
    # Gemini Live enforces a session duration limit and closes the socket
    # (GoAway -> ConnectionClosed 1011 "deadline expired"). With resumption on,
    # the server issues a resume handle we use to re-establish the SAME session
    # on close — no client-visible session.expired, no lost context.
    # False reproduces today's behavior byte-for-byte (session.expired on close).
    live_session_resumption_enabled: bool = True
    # Bounded transparent-reconnect cap per GeminiLiveSession; once exhausted we
    # fall back to the session.expired path.
    live_session_max_reconnects: int = 5

    # ---- External Uzbek TTS fallback (only used when tts_provider=external) ----
    # e.g. an Azure uz-UZ voice or a self-hosted Chatterbox/FeruzaSpeech endpoint.
    external_tts_url: str | None = None
    external_tts_voice: str = ""

    # ---- Azure Neural TTS (real native Uzbek voice — no accent) ----
    # Used when the client selects an "azure:<voice>" voice: Gemini Live runs in
    # TEXT mode (STT + reasoning) and Azure speaks the reply. Madina (female) and
    # Sardor (male) are the two uz-UZ neural voices.
    azure_speech_key: str | None = None
    azure_speech_region: str = "eastus"
    azure_tts_format: str = "raw-24khz-16bit-mono-pcm"  # matches client 24 kHz
    # Azure Neural TTS price (USD per 1M characters) for the cost panel.
    azure_tts_price_per_1m_chars: float = 16.0
    # Speak the opening clause after this many chars (on the first comma) so the
    # first Azure audio starts sooner. Lower = faster first audio, choppier start.
    azure_first_clause_chars: int = 12

    # ---- Generic audio (provider-neutral) ----
    audio_input_sample_rate_hz: int = 16000   # mic -> STT / Live input
    audio_output_sample_rate_hz: int = 24000  # Gemini Live native audio is 24 kHz
    # Generic "current voice" surfaced to the pipeline (filler bank, /health).
    tts_voice: str = "Aoede"

    # ---- Optional translation bridge ----
    # Gemini handles Uzbek directly, so the bridge defaults OFF (unlike Yandex).
    voice_use_russian_bridge: bool = False
    bridge_user_lang: str = "uz"
    bridge_model_lang: str = "ru"

    # ---- Voice pipeline ----
    voice_pipeline_mode: str = "google_gemini_live"
    voice_transport: Literal["websocket"] = "websocket"
    voice_enable_barge_in: bool = True
    voice_enable_filler: bool = True
    voice_endpoint_silence_ms: int = 500
    voice_input_gate_enabled: bool = True
    voice_input_gate_threshold: float = 0.02
    voice_input_gate_hangover_ms: int = 600

    # ---- Plant-disease case tools (photo request + diagnosis) ----
    # When true, the Live session attaches request_photo / finalize_case tools and
    # the system prompt gains the Uzbek tool policy. Flip OFF to get byte-identical
    # legacy behaviour (no tools attached, plain agriculture prompt).
    enable_case_tools: bool = True
    # Diagnosis is a separate REST call (photos + interview summary -> structured
    # verdict). gemini-3.1-pro-preview is VERIFIED on this key; bare
    # "gemini-3.1-pro" 404s. Falls back to gemini_model if blanked.
    diagnosis_model: str = "gemini-3.1-pro-preview"
    diagnosis_temperature: float = 0.2
    diagnosis_max_tokens: int = 8192  # Pro models think from this same budget
    # Voice-agent system prompt file (relative to the backend workdir). Lets the
    # prompt be tuned without a redeploy; the tool policy is appended by the loader
    # unless the file opts out with a "<!-- tools-included -->" marker.
    voice_agent_prompt_path: str = "prompts/voice_agent.md"
    # Photo guards: cap per-photo size and per-session count so a client cannot
    # flood the Live socket / diagnosis call.
    max_photo_bytes: int = 2_000_000
    max_photos_per_session: int = 5
    # Stream farmer photos into the Live session too (the model comments on them in
    # real time). Set false to send photos ONLY to the diagnosis call (fallback if
    # the Live video channel misbehaves).
    send_photos_to_live: bool = True

    # Upload-time vision check: reject non-plant photos before they reach the
    # conversation or the diagnosis (the anti-keyboard gate).
    verify_photos: bool = True

    # §4 deterministic photo-quality checks (advisory only — never block a photo).
    photo_quality_enabled: bool = True
    photo_blur_var_threshold: float = 60.0     # variance-of-Laplacian below => blurry
    photo_exposure_clip_frac: float = 0.40     # >=40% pixels in a histogram tail => bad exposure
    photo_dup_hamming_max: int = 8             # dHash distance <= this => near-duplicate
    # §5.4 per-selected-photo flash analysis before the Pro diagnosis call.
    per_image_analysis_enabled: bool = True
    # Accepted photo bytes on disk (sibling of chats_dir; same ./data volume in prod).
    # Used as the LOCAL FALLBACK when the DigitalOcean Spaces upload is off/fails.
    photos_dir: str = "data/photos"

    # ---- DigitalOcean Spaces (S3-compatible) photo upload ----
    # When bucket+key+secret are all set, accepted photos are uploaded here
    # (public-read) and the object's CDN URL is stored as the photo's
    # stored_path; on any upload failure the local disk fallback kicks in.
    do_spaces_bucket: str = ""
    do_spaces_key: str = ""
    do_spaces_secret: str = ""
    do_spaces_region: str = "fra1"
    # Endpoint / public CDN base are DERIVED from region+bucket when left blank.
    do_spaces_endpoint: str = ""
    do_spaces_public_base: str = ""
    do_spaces_prefix: str = "voiceagent/photos"

    # ---- Per-farmer memory (Alomat remembers every user) ----
    # When the client sends a user_id in session.start, the farmer's profile
    # (name, region, crops, last problem) is injected into the system prompt and
    # updated after each session by a flash extraction call. Flip OFF for fully
    # stateless sessions.
    memory_enabled: bool = True
    # Profile storage root, relative to the backend workdir. In prod this must
    # sit on the ./data:/app/data volume so memory survives container rebuilds.
    memory_dir: str = "data/memory"
    # Cross-session crop quick-pick chips on the "Qaysi ekin?" step. When OFF,
    # the crop question shows ONLY the «Ekinlar» browse button and each chat
    # carries its own crop in its ChatDoc — no shortlist pulled from the global
    # profile. (The rest of memory — name/region/last-problem context — is
    # unaffected; only the crop chips are gated here.)
    memory_crop_chips_enabled: bool = False

    # Subtitle scribe: Google Chirp 3 re-transcribes each push-to-talk turn for
    # accurate subtitles/transcripts (Gemini Live stays Alomat's ears). It
    # reuses the google_stt_* settings above; needs GOOGLE_APPLICATION_
    # CREDENTIALS + GOOGLE_PROJECT_ID. Absent creds = built-in transcription.

    # ---- Multichat + hybrid guided flow (docs/multichat_contract.md) ----
    # A session.start carrying a chat_id binds the call to a stored chat
    # (server-driven guided flow, then persisted free consultation). Flip OFF
    # for byte-identical legacy behaviour (chat_id is ignored either way when
    # unknown/invalid — this only disables the feature outright).
    chats_enabled: bool = True
    # Chat document storage root, relative to the backend workdir. Rides the
    # same ./data:/app/data volume as memory_dir in prod.
    chats_dir: str = "data/chats"

    # ---- Conversation enrichment (crop + weather + date) ----
    # Before the call opens, Rais is told today's date, the local weather, and
    # which crop the farmer picked. Fully fail-open: any missing piece just drops
    # that line. Flip OFF for a bare session.
    enrich_enabled: bool = True
    enrich_weather_enabled: bool = True
    # Growz main API — the real crop catalogue the picker fetches (x-api-key,
    # read-only /api/ai/crops). The crop UUID it returns is the SAME id the Growz
    # mobile app uses, so it is ready for a future /tenant chat integration.
    growz_api_url: str = "https://v2-api.growz.io"
    growz_api_key: str = ""
    # Profile-derived crop chips on the "Qaysi ekin?" step — the farmer's REAL
    # Growz crops shown next to «Ekinlar». Source:
    #   "mock" — packaged mock JSON (dev; the real GET /api/tenant/crops is behind
    #            the Growz tenant OTP-verify 500 blocker — see memory).
    #   "api"  — GET /api/tenant/crops with the farmer's tenant token (once the
    #            OTP verify is fixed + a token is available). Stub → [] for now.
    #   "off"  — no chips, only «Ekinlar».
    tenant_crops_source: str = "mock"
    # Override the packaged mock file (empty = app/voice/enrich/tenant_crops_mock.json).
    tenant_crops_mock_path: str = ""
    # Weather: Open-Meteo (free, no API key). Coordinates come from the phone GPS;
    # when absent (permission denied / web client) we fall back to Tashkent.
    open_meteo_url: str = "https://api.open-meteo.com/v1/forecast"
    weather_default_lat: float = 41.2995
    weather_default_lon: float = 69.2401

    # ---- Agronom verification stub (docs/multichat_contract.md Phase 3) ----
    # Master switch: the offer sentence, both /chats/{id}/agronom-* endpoints
    # and the mock kickoffs. OFF = byte-identical Phase 2 behaviour.
    agronom_enabled: bool = False
    # AUTO "senior agronom" AI second opinion on each request (is_mock=true).
    # A demo stub — flip OFF when real agronoms take over the submit endpoint.
    agronom_mock_enabled: bool = False
    # Shared secret for POST /chats/{id}/agronom-review (X-Agronom-Token
    # header). Empty (default) = the human submit endpoint is disabled (404).
    agronom_review_token: str = ""

    # ---- App auth / CORS ----
    voice_api_token: str = "change-me-dev-token"
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def stt_recognizer_path(self) -> str:
        """Inline recognizer resource for Cloud Speech-to-Text v2."""
        return (
            f"projects/{self.google_project_id}"
            f"/locations/{self.google_stt_region}/recognizers/_"
        )

    @property
    def stt_api_endpoint(self) -> str:
        """Regional Speech-to-Text endpoint (must match the chirp model region)."""
        return f"{self.google_stt_region}-speech.googleapis.com"

    @model_validator(mode="after")
    def _anchor_relative_paths(self) -> "Settings":
        """Anchor CWD-relative storage paths to the backend dir.

        These four fields default to values "relative to the backend workdir".
        When the process runs from another CWD (the Phase 4 in-process mount runs
        from the growz repo root), a bare relative like ``data/chats`` would
        resolve against the wrong directory. Anchor any RELATIVE value to
        ``_BACKEND_DIR``; absolute values (env overrides, tests' tmp_path dirs)
        pass through unchanged.
        """
        for field in ("voice_agent_prompt_path", "photos_dir", "memory_dir", "chats_dir"):
            value = getattr(self, field)
            if value and not Path(value).is_absolute():
                setattr(self, field, str(_BACKEND_DIR / value))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
