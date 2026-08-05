"""OpenAPI surface: REST request/response models + /docs metadata.

Response models here are documentation ONLY. Routes keep ``response_model=None``
and return plain dicts, referencing these models via the ``responses=`` mapping,
so the wire shapes stay byte-identical to ``docs/multichat_contract.md`` §2 —
including quirks a serializing response model could not preserve, such as
``photo_url`` appearing only on photo messages. Change a shape here only
together with the contract and the mobile client.

The request-body models (``CreateChatBody`` etc.) ARE live: main.py's routes
parse against them. They live at module scope because — with
``from __future__ import annotations`` in effect — FastAPI resolves a route's
parameter annotations against the route function's module globals, so a class
defined inside ``create_app()`` would not be found and the parameter would
silently stop being treated as a JSON body (importing them into main.py's
globals is enough).

``WS_CLIENT_EVENTS`` / ``WS_SERVER_EVENTS`` register the ``/ws/voice`` control
-plane models (``app/schemas.py``) for injection into the OpenAPI components:
OpenAPI cannot describe WebSockets, so the event models are surfaced in the
Schemas section of ``/docs`` and referenced from the description text below.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import (
    AgentInterrupted,
    AudioEnd,
    CameraCancelled,
    CaseDiagnosis,
    ChatAnswer,
    ChatQuestion,
    ChatState,
    ChatStep,
    DiagnosisStarted,
    ErrorEvent,
    IntentPartial,
    LatencyMetrics,
    LLMToken,
    PhotoQuality,
    PhotoReceived,
    PhotoUpload,
    ChatStart,
    STTFinal,
    STTPartial,
    ToolCancelled,
    ToolRequestPhoto,
    TTSFinished,
    TTSStarted,
    UserInterrupt,
)

_USER_ID_FIELD = Field(
    "",
    description="Stable per-install device id (UUID) that scopes all chat data.",
    examples=["11111111-2222-3333-4444-555555555555"],
)


# ---------------------------------------------------------------------------
# Request bodies (live models — routes parse against these)
# ---------------------------------------------------------------------------


class CreateChatBody(BaseModel):
    """POST /chats request body (contract §2.2)."""

    user_id: str = _USER_ID_FIELD


class AgronomRequestBody(BaseModel):
    """POST /chats/{chat_id}/agronom-request request body (contract P3.3)."""

    user_id: str = _USER_ID_FIELD


class AgronomReviewBody(BaseModel):
    """POST /chats/{chat_id}/agronom-review request body (contract P3.4) —
    the human expert's verdict, submitted with the X-Agronom-Token header."""

    user_id: str = _USER_ID_FIELD
    verdict: str = Field("", description='"confirmed" or "adjusted".')
    expert_summary: str = Field(
        "", description="Farmer-facing Uzbek summary, ≤600 chars. Required."
    )
    expert_notes: list[str] = Field(
        default_factory=list, description="Up to 6 notes, ≤300 chars each."
    )
    adjusted_preparations: list[dict] = Field(
        default_factory=list,
        description="Frozen P2.1 Preparation shape; [] keeps the AI list.",
    )


# ---------------------------------------------------------------------------
# Response models (documentation only — never serialized through)
# ---------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    """FastAPI's HTTPException shape — all documented error statuses use it."""

    detail: str


class LivePricing(BaseModel):
    """Gemini Live token prices, USD per 1M tokens."""

    textIn: float
    audioIn: float
    textOut: float
    audioOut: float


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    provider: str = Field(examples=["google"])
    pipeline: str
    mode: str = Field(
        description='"Gemini Live (native audio)" / "Gemini Live (half-cascade)" / "Staged (TTS: …)".'
    )
    model: str
    llm: str
    stt: str
    tts: str
    language: str = Field(examples=["uz-UZ"])
    voice: str
    azure_brain_model: str = Field(
        description="The brain used when an Azure voice is selected (the client "
        "overrides the display for azure: voices)."
    )
    pricing: LivePricing
    azure_pricing: LivePricing
    azure_tts_price_per_1m_chars: float


class Crop(BaseModel):
    """One Growz crop (Uzbek name + real Growz UUID)."""

    id: str
    name: str = Field(examples=["Pomidor"])
    biology_name: str
    category: str


class CropsResponse(BaseModel):
    data: list[Crop] = Field(
        description="Empty means Growz was unreachable, not an error (fail-open)."
    )


class ChatMessageOut(BaseModel):
    """One stored chat message. ``photo_url`` is present ONLY on photo
    messages (kind == "photo") — omitted otherwise, never null."""

    role: str = Field(description="farmer | rais | system | agronom")
    kind: str = Field(
        description="text | question | answer | photo | diagnosis | agronom_review"
    )
    text: str
    ts: str
    photo_url: str | None = Field(
        None, description="Public photo URL — only on kind == photo messages."
    )


class Preparation(BaseModel):
    """Frozen P2.1 Agroapteka preparation shape (all six keys always present)."""

    name: str
    dose_min: float | None
    dose_max: float | None
    unit: str
    type: str
    description: str


class AgronomReviewOut(BaseModel):
    """§7 agronom verification state, surfaced verbatim on summary + detail."""

    status: str = Field(description="none | pending | done")
    requested_at: str
    reviewed_at: str
    is_mock: bool = Field(description="True = AI second-opinion stub (P3.5).")
    verdict: str = Field(description='"" | confirmed | adjusted')
    expert_summary: str
    expert_notes: list[str]
    adjusted_preparations: list[Preparation] = Field(
        description="[] means the AI preparations list stands unchanged."
    )


class ChatSummary(BaseModel):
    """The list/create response shape (contract §2.1/§2.2)."""

    id: str
    user_id: str
    title: str
    query_type: str = Field(description='"" | disease_pest | weed | general')
    crop_id: str
    crop_name: str
    plant_part: str
    symptom_done: bool
    symptom_summary: str
    general_question: str
    created_at: str
    updated_at: str
    finished: bool
    message_count: int
    last_message: ChatMessageOut | None
    agronom_review: AgronomReviewOut | None


class ChatDetail(BaseModel):
    """The single-chat fetch response shape (contract §2.3): summary fields
    plus ``messages`` and ``last_diagnosis`` (no message_count/last_message)."""

    id: str
    user_id: str
    title: str
    query_type: str = Field(description='"" | disease_pest | weed | general')
    crop_id: str
    crop_name: str
    plant_part: str
    symptom_done: bool
    symptom_summary: str
    general_question: str
    created_at: str
    updated_at: str
    finished: bool
    last_diagnosis: dict | None = Field(
        description="Raw DiagnosisResult dump + summary + preparations, or null."
    )
    messages: list[ChatMessageOut]
    agronom_review: AgronomReviewOut | None


class ChatListResponse(BaseModel):
    data: list[ChatSummary]


class ChatSummaryResponse(BaseModel):
    data: ChatSummary


class ChatDetailResponse(BaseModel):
    data: ChatDetail


# ---------------------------------------------------------------------------
# /ws/voice control-plane registry + /docs metadata
# ---------------------------------------------------------------------------

WS_CLIENT_EVENTS: tuple[type[BaseModel], ...] = (
    ChatStart,
    AudioEnd,
    UserInterrupt,
    PhotoUpload,
    PhotoQuality,
    CameraCancelled,
    ChatAnswer,
)

WS_SERVER_EVENTS: tuple[type[BaseModel], ...] = (
    STTPartial,
    STTFinal,
    IntentPartial,
    LLMToken,
    TTSStarted,
    TTSFinished,
    AgentInterrupted,
    LatencyMetrics,
    ErrorEvent,
    ToolRequestPhoto,
    ToolCancelled,
    PhotoReceived,
    DiagnosisStarted,
    CaseDiagnosis,
    ChatQuestion,
    ChatStep,
    ChatState,
)


def ws_component_schemas() -> dict:
    """JSON schemas for every /ws/voice event model, keyed by model name and
    with internal refs rewritten to ``#/components/schemas/…`` so they can be
    merged straight into the OpenAPI components."""
    from pydantic.json_schema import models_json_schema

    _, defs = models_json_schema(
        [(m, "validation") for m in (*WS_CLIENT_EVENTS, *WS_SERVER_EVENTS)],
        ref_template="#/components/schemas/{model}",
    )
    return defs.get("$defs", defs)


def _event_names(models: tuple[type[BaseModel], ...]) -> str:
    return ", ".join(f"`{m.__name__}`" for m in models)


TAGS_METADATA = [
    {"name": "health", "description": "Service status: active pipeline, models and token pricing."},
    {"name": "crops", "description": "Growz crop catalogue proxy for the mobile picker (the Growz API key stays server-side)."},
    {"name": "chats", "description": "Multichat store (`docs/multichat_contract.md` §2). Device-id scoped — no auth beyond a valid `user_id` UUID."},
    {"name": "agronom", "description": "§7 human-agronom verification: farmer request + expert review submission."},
]

API_DESCRIPTION = f"""
REST control surface + realtime WebSocket voice pipeline for the Uzbek
farming voice assistant (Gemini Live realtime, or staged Cloud STT uz-UZ →
Gemini → TTS).

### Authentication
* **REST** — device-scoped: endpoints take a `user_id` (stable per-install
  device UUID) and never reveal other devices' data. No further auth here;
  in production nginx fronts the service.
* **`POST /chats/{{chat_id}}/agronom-review`** — expert-only, gated by the
  `X-Agronom-Token` header.
* **`WS /ws/voice`** — optional token gate: when `VOICE_API_TOKEN` is set the
  client must connect with `?token=…` (browsers cannot set WS headers);
  a bad/missing token closes the socket with app-level code **4401**.

### WS `/ws/voice` — the voice protocol
OpenAPI cannot model WebSockets, so the contract is documented here and every
event payload is included under **Schemas** below.

* **Binary frames** — client → server: mic audio, PCM16 mono 16 kHz;
  server → client: TTS audio, PCM16 48 kHz. Audio is never JSON.
* **Text frames** — JSON control events discriminated by `type`:
  * client → server: {_event_names(WS_CLIENT_EVENTS)}
  * server → client: {_event_names(WS_SERVER_EVENTS)}
* **Photos** are the one exception to "binary = mic": farmer photos ride the
  JSON plane base64-encoded (`PhotoUpload`) so a still image is never confused
  with a mic frame.
* The case-tool events are emitted as raw dicts on the wire; the models
  document the exact shapes both sides agreed on. Token accounting (`usage`,
  `usage_azure`) and the Azure->Gemini voice swap (`tts.fallback`) were
  REMOVED from the socket on 2026-08-05 — all three are server-side logs now.
* The guided-flow events (`ChatQuestion`/`ChatAnswer`/`ChatStep`/`ChatState`)
  follow `docs/multichat_contract.md`.
"""
