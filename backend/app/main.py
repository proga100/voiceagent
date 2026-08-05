"""FastAPI app factory + WebSocket entry point.

``/health`` plus ``/ws/voice`` driving the Google voice pipeline
(Gemini Live realtime, or staged Cloud STT uz-UZ -> Gemini -> TTS) via
``run_voice_agent``. Binary WS frames are mic audio (PCM16 16k); JSON frames
are control events.
"""
from __future__ import annotations

import logging
import secrets
import uuid

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse

from app import api_schemas
from app.api_schemas import AgronomRequestBody, AgronomReviewBody, CreateChatBody
from app.auth import verify_token
from app.config import get_settings
from app.voice.pipeline.voice_agent import run_voice_agent

logger = logging.getLogger("voice")

# Gemini Live token prices (USD per 1M tokens), by model family. Audio in/out are
# billed far higher than text; 3.1 has pricier text than 2.5 native-audio.
_LIVE_PRICES = {
    "3.1-flash-live": {"textIn": 0.75, "audioIn": 3.00, "textOut": 4.50, "audioOut": 12.00},
    "native-audio":   {"textIn": 0.50, "audioIn": 3.00, "textOut": 2.00, "audioOut": 12.00},
}


def _pricing_for(model: str) -> dict:
    if "native-audio" in model:
        return _LIVE_PRICES["native-audio"]
    return _LIVE_PRICES["3.1-flash-live"]  # default (3.1 / other half-cascade)


# Request-body models live in api_schemas.py; the imports above put them in
# this module's globals, which is what FastAPI resolves route annotations
# against under ``from __future__ import annotations`` (see api_schemas.py).

_USER_ID_QUERY = Query(
    "", description="Stable per-install device id (UUID) that scopes all chat data."
)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Google Uzbek Voice Agent",
        version="0.1.0",
        description=api_schemas.API_DESCRIPTION,
        openapi_tags=api_schemas.TAGS_METADATA,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get(
        "/health",
        tags=["health"],
        summary="Service status and active pipeline",
        response_model=None,
        responses={200: {"model": api_schemas.HealthResponse}},
    )
    async def health() -> dict:
        """Which voice pipeline is live (Gemini Live vs staged STT→LLM→TTS),
        the models behind it, and the token prices the client's cost panel
        needs. No auth."""
        if settings.use_gemini_live_audio:
            native = "native-audio" in settings.gemini_live_model
            mode = "Gemini Live (native audio)" if native else "Gemini Live (half-cascade)"
            model = settings.gemini_live_model
            stt = f"{model} (built-in)"
            tts = f"{model} (built-in)"
        else:
            mode = f"Staged (TTS: {settings.tts_provider})"
            model = settings.gemini_model
            stt = f"Cloud STT {settings.google_stt_language}/{settings.google_stt_model}"
            tts = settings.tts_provider
        return {
            "status": "ok",
            "provider": settings.provider,
            "pipeline": settings.voice_pipeline_mode,
            "mode": mode,
            "model": model,
            "llm": model,
            "stt": stt,
            "tts": tts,
            "language": settings.google_stt_language,
            "voice": settings.tts_voice,
            # The brain used when an Azure voice is selected (client overrides the
            # display for azure: voices, since /health can't know the live choice).
            "azure_brain_model": settings.gemini_live_text_model,
            # Token prices (USD/1M) so the client's cost panel matches the model.
            "pricing": _pricing_for(model if settings.use_gemini_live_audio else settings.gemini_live_model),
            "azure_pricing": _pricing_for(settings.gemini_live_text_model),
            "azure_tts_price_per_1m_chars": settings.azure_tts_price_per_1m_chars,
        }

    @app.get(
        "/crops",
        tags=["crops"],
        summary="Growz crop catalogue for the mobile picker",
        response_model=None,
        responses={200: {"model": api_schemas.CropsResponse}},
    )
    async def crops() -> dict:
        """Growz crop catalogue for the mobile picker (Uzbek names + real UUIDs).

        The Growz API key stays server-side; the app only ever sees this list.
        Fail-open: an empty ``data`` means Growz was unreachable, not an error.
        """
        from app.voice.enrich import get_crops

        return {"data": await get_crops(settings)}

    @app.get(
        "/tester",
        tags=["debug"],
        summary="Built-in WebSocket tester page",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    async def tester() -> HTMLResponse:
        """A single self-contained page that drives the whole guided flow from a
        browser: creates a chat, opens the socket, renders each ``chat.question``
        as buttons, logs every payload and plays the agent's PCM back.

        Exists because Postman cannot export WebSocket requests
        (postmanlabs/postman-app-support#11252), so there is no way to hand a
        colleague a file that reproduces the full cycle. This page is that file,
        served next to the socket it talks to.

        Served by default so a fresh deploy needs no extra variable; set
        ``VOICE_TESTER_ENABLED=false`` to take it off a host. Hidden from the
        OpenAPI schema either way — it is a debugging tool, not a product
        surface.
        """
        if not settings.voice_tester_enabled:
            raise HTTPException(status_code=404, detail="not found")
        page = Path(__file__).with_name("static") / "ws_tester.html"
        try:
            return HTMLResponse(page.read_text(encoding="utf-8"))
        except OSError:
            logger.exception("tester page missing at %s", page)
            raise HTTPException(status_code=404, detail="not found")

    # ---- Multichat (docs/multichat_contract.md §2) -------------------------
    # Same trust level as /crops: device-id scoping, no auth beyond the URL
    # shape checks below. nginx carves out /chats from Basic Auth like /crops.

    @app.get(
        "/chats",
        tags=["chats"],
        summary="List a device's chats (newest first)",
        response_model=None,
        responses={
            200: {"model": api_schemas.ChatListResponse},
            400: {"model": api_schemas.ErrorDetail, "description": "Invalid user_id."},
        },
    )
    async def list_chats(
        user_id: str = _USER_ID_QUERY,
        limit: int = Query(50, description="Max summaries returned (clamped server-side)."),
    ) -> dict:
        """Chat summaries for one device (contract §2.1). A corrupt store
        never 500s — it degrades to an empty list."""
        from app.voice.chat import ChatStore
        from app.voice.pipeline.memory import valid_device_id

        if not valid_device_id(user_id):
            raise HTTPException(status_code=400, detail="invalid user_id")
        try:
            store = ChatStore(settings)
            return {"data": store.list_summaries(user_id, limit)}
        except Exception:  # noqa: BLE001 — a corrupt store must never 500 the list
            logger.exception("chats list failed")
            return {"data": []}

    @app.post(
        "/chats",
        tags=["chats"],
        summary="Create an empty chat",
        response_model=None,
        responses={
            200: {"model": api_schemas.ChatSummaryResponse},
            400: {"model": api_schemas.ErrorDetail, "description": "Invalid user_id."},
        },
    )
    async def create_chat(body: CreateChatBody) -> dict:
        """Create a chat for the device and return its summary (contract
        §2.2). The chat is bound to a voice session later via
        ``ChatStart.chat_id`` on the WebSocket."""
        from app.voice.chat import ChatStore, build_summary
        from app.voice.pipeline.memory import valid_device_id

        if not valid_device_id(body.user_id):
            raise HTTPException(status_code=400, detail="invalid user_id")
        store = ChatStore(settings)
        doc = store.create(body.user_id)
        return {"data": build_summary(doc)}

    @app.get(
        "/chats/{chat_id}",
        tags=["chats"],
        summary="Fetch one chat with full message history",
        response_model=None,
        responses={
            200: {"model": api_schemas.ChatDetailResponse},
            400: {"model": api_schemas.ErrorDetail, "description": "Invalid user_id."},
            404: {
                "model": api_schemas.ErrorDetail,
                "description": "Unknown chat — or owned by another device "
                "(never distinguished).",
            },
        },
    )
    async def get_chat(chat_id: str, user_id: str = _USER_ID_QUERY) -> dict:
        """Full chat detail: messages, last diagnosis and agronom-review state
        (contract §2.3)."""
        from app.voice.chat import ChatStore, build_detail
        from app.voice.pipeline.memory import valid_device_id

        if not valid_device_id(user_id):
            raise HTTPException(status_code=400, detail="invalid user_id")
        store = ChatStore(settings)
        doc = store.read(user_id, chat_id)
        if doc is None or doc.user_id != user_id:
            # Do not reveal whether the chat exists for another owner.
            raise HTTPException(status_code=404, detail="chat not found")
        return {"data": build_detail(doc)}

    # ---- Photo upload over REST (2026-08-05) --------------------------------
    # The photo bytes leave the WebSocket: the client POSTs them here first and
    # then sends only the returned URL in the `photo.upload` WS event.

    @app.post(
        "/photos",
        tags=["chats"],
        summary="Upload a farmer photo, get back its public URL",
        response_model=None,
        responses={
            200: {"model": api_schemas.UploadPhotoResponse},
            400: {"model": api_schemas.ErrorDetail,
                  "description": "Invalid user_id / base64 / empty photo."},
            413: {"model": api_schemas.ErrorDetail, "description": "Photo too large."},
        },
    )
    async def upload_photo(body: api_schemas.UploadPhotoBody, request: Request) -> dict:
        import asyncio
        import base64 as b64

        from app.voice.pipeline.memory import valid_device_id
        from app.voice.pipeline.photo_store import (
            PhotoStore, _ext_for_mime, _sanitize,
        )

        if not valid_device_id(body.user_id):
            raise HTTPException(status_code=400, detail="invalid user_id")
        try:
            data = b64.b64decode(body.data or "", validate=True)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="invalid base64")
        if not data:
            raise HTTPException(status_code=400, detail="empty photo")
        if len(data) > settings.max_photo_bytes:
            raise HTTPException(status_code=413, detail="photo too large")

        photo_id = (body.photo_id or "").strip() or uuid.uuid4().hex
        chat_id = (body.chat_id or "").strip() or "nochat"
        store = PhotoStore(settings)
        stored = await asyncio.to_thread(
            store.save, body.user_id, chat_id, photo_id, data, body.mime,
        )
        if stored is None:
            raise HTTPException(status_code=500, detail="photo store failed")
        if stored.startswith(("http://", "https://")):
            url = stored  # Spaces CDN
        else:
            # Local-disk fallback: serve through the GET route below.
            u, c = _sanitize(body.user_id), _sanitize(chat_id)
            name = f"{_sanitize(photo_id)}{_ext_for_mime(body.mime)}"
            url = f"{str(request.base_url).rstrip('/')}/photos/{u}/{c}/{name}"
        return {"data": {"photo_id": photo_id, "url": url}}

    @app.get(
        "/photos/{user_id}/{chat_id}/{name}",
        include_in_schema=False,  # binary serving route, not part of the JSON API
    )
    async def get_photo(user_id: str, chat_id: str, name: str):
        from fastapi.responses import FileResponse

        from app.voice.pipeline.photo_store import _sanitize

        u, c = _sanitize(user_id), _sanitize(chat_id)
        n = _sanitize(name)
        if not u or not c or not n:
            raise HTTPException(status_code=404, detail="not found")
        path = Path(settings.photos_dir) / u / c / n
        if not path.is_file():
            raise HTTPException(status_code=404, detail="not found")
        media = "image/png" if n.endswith(".png") else "image/jpeg"
        return FileResponse(path, media_type=media)

    # ---- Agronom verification stub (docs/multichat_contract.md Phase 3 §7) ---

    @app.post(
        "/chats/{chat_id}/agronom-request",
        tags=["agronom"],
        summary="Farmer requests a human-agronom review",
        response_model=None,
        responses={
            200: {"model": api_schemas.ChatSummaryResponse},
            400: {"model": api_schemas.ErrorDetail, "description": "Invalid user_id."},
            404: {
                "model": api_schemas.ErrorDetail,
                "description": "Chat not found, or the agronom feature is disabled.",
            },
        },
    )
    async def agronom_request(chat_id: str, body: AgronomRequestBody) -> dict:
        """Mark the chat's diagnosis as pending expert review (contract P3.3).
        Idempotent: repeat calls while pending/done change nothing. May start
        the mock AI second-opinion runner (P3.5) when configured."""
        from app.voice.agronom.review import maybe_start_mock_review
        from app.voice.chat import AgronomReview, ChatStore, build_summary
        from app.voice.chat.models import now_iso
        from app.voice.chat.store import lock_for
        from app.voice.pipeline.memory import valid_device_id

        if not settings.agronom_enabled:
            raise HTTPException(status_code=404, detail="agronom not available")
        if not valid_device_id(body.user_id):
            raise HTTPException(status_code=400, detail="invalid user_id")

        store = ChatStore(settings)
        async with lock_for(chat_id):
            doc = store.read(body.user_id, chat_id)
            if doc is None or doc.user_id != body.user_id:
                raise HTTPException(status_code=404, detail="chat not found")
            if doc.agronom_review is None:
                doc.agronom_review = AgronomReview(
                    status="pending", requested_at=now_iso()
                )
                doc.updated_at = now_iso()
                store.save(doc)
                logger.warning(
                    "agronom review requested: user=%s chat=%s crop=%s disease=%s",
                    body.user_id, chat_id, doc.crop_name,
                    (doc.last_diagnosis or {}).get("disease", ""),
                )
            # Already pending or done -> no change (idempotent).

        maybe_start_mock_review(settings, store, doc)
        return {"data": build_summary(doc)}

    @app.post(
        "/chats/{chat_id}/agronom-review",
        tags=["agronom"],
        summary="Expert submits the agronom verdict",
        response_model=None,
        responses={
            200: {"model": api_schemas.ChatSummaryResponse},
            400: {
                "model": api_schemas.ErrorDetail,
                "description": "Invalid user_id / verdict, or empty expert_summary.",
            },
            401: {"model": api_schemas.ErrorDetail, "description": "Invalid token."},
            404: {
                "model": api_schemas.ErrorDetail,
                "description": "Chat not found, or the agronom feature is disabled.",
            },
            409: {
                "model": api_schemas.ErrorDetail,
                "description": "The farmer never requested a review for this chat.",
            },
        },
    )
    async def agronom_review_submit(
        chat_id: str,
        body: AgronomReviewBody,
        x_agronom_token: str | None = Header(
            default=None,
            description="Expert reviewer token (AGRONOM_REVIEW_TOKEN).",
        ),
    ) -> dict:
        """Land the human expert's verdict on a pending review (contract
        P3.4) and append the agronom message to the chat. Expert-only —
        gated by ``X-Agronom-Token``."""
        from app.voice.chat import ChatStore, build_summary, sanitize_expert_payload
        from app.voice.chat.models import UZ, now_iso
        from app.voice.chat.store import lock_for
        from app.voice.pipeline.memory import valid_device_id

        if not settings.agronom_enabled or not settings.agronom_review_token:
            raise HTTPException(status_code=404, detail="agronom not available")
        if not x_agronom_token or not secrets.compare_digest(
            x_agronom_token, settings.agronom_review_token
        ):
            raise HTTPException(status_code=401, detail="invalid token")
        if not valid_device_id(body.user_id):
            raise HTTPException(status_code=400, detail="invalid user_id")

        store = ChatStore(settings)
        async with lock_for(chat_id):
            doc = store.read(body.user_id, chat_id)
            if doc is None or doc.user_id != body.user_id:
                raise HTTPException(status_code=404, detail="chat not found")
            if doc.agronom_review is None:
                raise HTTPException(status_code=409, detail="review not requested")
            if body.verdict not in ("confirmed", "adjusted"):
                raise HTTPException(status_code=400, detail="invalid verdict")
            summary, notes, preps = sanitize_expert_payload(
                body.expert_summary, body.expert_notes, body.adjusted_preparations
            )
            if not summary:
                raise HTTPException(status_code=400, detail="empty expert_summary")

            r = doc.agronom_review
            r.status = "done"
            r.reviewed_at = now_iso()
            r.is_mock = False
            r.verdict = body.verdict
            r.expert_summary = summary
            r.expert_notes = notes
            r.adjusted_preparations = preps
            if not r.requested_at:
                r.requested_at = now_iso()
            store.append_message(
                doc, "agronom", "agronom_review", f"{UZ['agronomPrefix']} {summary}"
            )
        return {"data": build_summary(doc)}

    @app.websocket("/ws/voice")
    async def ws_voice(websocket: WebSocket) -> None:
        # Auth is optional: when VOICE_API_TOKEN is unset/empty the endpoint is
        # open (handy for local testing). Set a token to re-enable the gate.
        # Browsers can't set WS headers, so the token travels via ?token=.
        if settings.voice_api_token:
            token = websocket.query_params.get("token")
            if not verify_token(token, settings):
                await websocket.close(code=4401)  # 4401: app-level unauthorized
                return

        await websocket.accept()
        session_id = uuid.uuid4().hex
        logger.info("ws/voice connected: %s", session_id)
        await run_voice_agent(websocket, settings, session_id)

    def openapi_with_ws_events() -> dict:
        # OpenAPI cannot describe WebSockets, so no HTTP route references the
        # /ws/voice control-plane models (app/schemas.py) and the generator
        # would drop them. Inject them into components so /docs shows the full
        # event contract next to the REST schemas.
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
        )
        schema.setdefault("components", {}).setdefault("schemas", {}).update(
            api_schemas.ws_component_schemas()
        )
        app.openapi_schema = schema
        return schema

    app.openapi = openapi_with_ws_events

    return app


app = create_app()
