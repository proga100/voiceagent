"""WebSocket control-event schemas (JSON).

Audio travels as raw binary WebSocket frames (PCM16) and is NOT modelled here:
inbound binary = mic audio (16k), outbound binary = TTS audio (48k). Only the
JSON control plane is validated by these pydantic models. Farmer photos are the
exception to "binary = mic": they ride the JSON plane base64-encoded
(``photo.upload``) so a still image is never confused with a mic frame.

Case-tool events (photo request / diagnosis) are, like ``usage``, emitted as raw
dicts on the wire; the models below document that contract for the client.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Client -> Server
# ---------------------------------------------------------------------------


class ChatStart(BaseModel):
    """Opens the session. Renamed from ``session.start`` (2026-08-05); the
    old name is gone, so server and app must ship together.

    ``sample_rate`` and ``voice`` were removed at the same time — both are
    server settings now (``AUDIO_INPUT_SAMPLE_RATE_HZ`` / ``GEMINI_LIVE_VOICE``
    in .env). ``language`` stays client-sent.
    """

    type: Literal["chat.start"] = "chat.start"
    language: str = "uz-UZ"
    # Stable per-install device id (UUID) — keys Alomat's per-farmer memory.
    # Empty/absent → memoryless session (web test client, older apps).
    user_id: str = ""
    # Conversation enrichment (all optional). The crop the farmer picked before
    # the call (real Growz UUID + Uzbek name) and their GPS for local weather.
    crop_id: str = ""
    crop_name: str = ""
    lat: float | None = None
    lon: float | None = None
    # Multichat (docs/multichat_contract.md): binds this session to a stored
    # chat. Empty/unknown/owner-mismatched -> plain session (unchanged today).
    chat_id: str = ""


class AudioEnd(BaseModel):
    type: Literal["audio.end"] = "audio.end"


class UserInterrupt(BaseModel):
    type: Literal["user.interrupt"] = "user.interrupt"


class PhotoUpload(BaseModel):
    """Reworked 2026-08-05: the photo bytes travel over REST (``POST /photos``
    returns a public URL) and this WS event carries ONLY that URL in ``value``.
    The server downloads the bytes itself. base64/mime/width/height/origin are
    gone; ``chat_id`` guards against stale events from another chat."""

    type: Literal["photo.upload"] = "photo.upload"
    chat_id: str = ""
    photo_id: str | None = None
    value: str = ""  # public URL returned by POST /photos


class CameraCancelled(BaseModel):
    type: Literal["camera.cancelled"] = "camera.cancelled"


# ---------------------------------------------------------------------------
# Server -> Client
# ---------------------------------------------------------------------------


class STTPartial(BaseModel):
    type: Literal["stt.partial"] = "stt.partial"
    value: str


class LLMToken(BaseModel):
    type: Literal["llm.token"] = "llm.token"
    token: str


class TTSStarted(BaseModel):
    type: Literal["tts.started"] = "tts.started"


class TTSFinished(BaseModel):
    type: Literal["tts.finished"] = "tts.finished"


class AgentInterrupted(BaseModel):
    type: Literal["agent.interrupted"] = "agent.interrupted"


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


# ---- Case tools (photo request + diagnosis) -------------------------------


class ToolRequestPhoto(BaseModel):
    type: Literal["tool.request_photo"] = "tool.request_photo"
    call_id: str
    reason: str = ""
    target_part: str = "leaf"


class ToolCancelled(BaseModel):
    type: Literal["tool.cancelled"] = "tool.cancelled"
    call_ids: list[str] = Field(default_factory=list)


class PhotoReceived(BaseModel):
    type: Literal["photo.received"] = "photo.received"
    photo_id: str
    count: int


class DiagnosisStarted(BaseModel):
    type: Literal["diagnosis.started"] = "diagnosis.started"
    case_id: str


class CaseDiagnosis(BaseModel):
    type: Literal["case.diagnosis"] = "case.diagnosis"
    case_id: str
    # Raw DiagnosisResult.model_dump() + the interview summary that produced it.
    result: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    # Phase 2: Growz Agroapteka preparations for result.likely_disease.
    # SEPARATE from result (Gemini verdict vs Growz catalogue lookup); [] on
    # any lookup failure — never blocks the diagnosis. Shape: P2.1 addendum.
    preparations: list[dict[str, Any]] = Field(default_factory=list)


# ---- Multichat + hybrid guided flow (docs/multichat_contract.md) ----------
# The guided-flow question/answer/step events ride the same JSON control
# plane. Server emits these as raw dicts (like ``usage`` above); the models
# below document the exact shapes both sides agreed on.


class ChatAnswerCrop(BaseModel):
    """The crop picked on the ``crop`` step. Carried as its own object so
    ``option_id`` can keep naming the tapped button (``open_crop_picker`` / a
    saved chip) instead of doubling as a data field."""

    id: str = ""
    name: str = ""


class ChatAnswer(BaseModel):
    # v2: step_id also carries "symptom" | "general" | "diag_offer" — see
    # docs/multichat_contract.md §1.5. diag_offer is the one step id that is
    # valid while the chat's pending step is a DIFFERENT id ("general").
    type: Literal["chat.answer"] = "chat.answer"
    chat_id: str = ""
    step_id: str = ""
    option_id: str = ""
    value: str = ""
    # Only on the crop step; other steps omit it.
    crop: ChatAnswerCrop | None = None


class ChatOption(BaseModel):
    id: str
    label: str


class ChatQuestion(BaseModel):
    type: Literal["chat.question"] = "chat.question"
    chat_id: str
    # v1: query_type | crop | plant_part | photo
    # v2 adds: symptom | general | diag_offer (docs/multichat_contract.md §1.3)
    step_id: str
    prompt: str
    # v1: buttons | crop_picker | photo
    # v2 adds: symptom | free (docs/multichat_contract.md §1.3)
    kind: str = "buttons"
    options: list[ChatOption] = Field(default_factory=list)


class ChatStep(BaseModel):
    type: Literal["chat.step"] = "chat.step"
    chat_id: str
    step_id: str
    option_id: str = ""
    value: str = ""
    label: str = ""


class ChatState(BaseModel):
    type: Literal["chat.state"] = "chat.state"
    chat_id: str
    # v1: guide | consult
    # v2 adds: symptom | general (docs/multichat_contract.md §1.2)
    phase: str
    selections: dict[str, str] = Field(default_factory=dict)
