"""Per-connection orchestrator entry point.

Owns the WebSocket receive loop and translates frames to session calls: binary
frames are mic audio; JSON frames are control events. Keeps the transport
(FastAPI WebSocket) at the edge so the sessions stay transport-agnostic.

Two session shapes are selected by ``USE_GEMINI_LIVE_AUDIO``:
  * **Gemini Live** (default) — one realtime audio-in/audio-out session. The only
    Google path that speaks Uzbek.
  * **Staged** — STT (uz-UZ) -> Gemini text -> TTS (per ``TTS_PROVIDER``), the
    same orchestrator as the Yandex pipeline.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import WebSocket, WebSocketDisconnect

from app.config import Settings
from app.voice.chat import ChatDoc, ChatGuide, ChatStore, build_guide_prompt_block
from app.voice.chat.models import UZ, now_iso
from app.voice.pipeline.memory import (
    MemoryStore,
    build_memory_context,
    finalize_session_memory,
    valid_device_id,
)
from app.voice.enrich import build_session_enrichment, get_tenant_crops
from app.voice.pipeline.prompts import ENGLISH_GREETING_KICKOFF, load_system_prompt
from app.voice.pipeline.streaming_session import StreamingSession
from app.voice.providers.factory import (
    build_auth,
    build_gpt,
    build_stt,
    build_translate,
    build_tts,
)
from app.voice.providers.gemini_live import GeminiLiveSession

logger = logging.getLogger("voice.agent")


def _local_photo_path(url: str, settings):
    """If ``url`` is our own ``/photos/{user}/{chat}/{name}`` serving route
    (the Spaces-less fallback POST /photos hands out), map it to the file
    under ``settings.photos_dir`` — sanitized, no traversal. None otherwise."""
    from pathlib import Path
    from urllib.parse import urlparse

    from app.voice.pipeline.photo_store import _sanitize

    marker = "/photos/"
    path = urlparse(url).path
    if marker not in path:
        return None
    parts = path.split(marker, 1)[1].split("/")
    if len(parts) != 3:
        return None
    segs = [_sanitize(p) for p in parts]
    if not all(segs):
        return None
    p = Path(getattr(settings, "photos_dir", "data/photos")) / segs[0] / segs[1] / segs[2]
    return p if p.is_file() else None


async def _download_photo(url: str, settings) -> tuple[bytes, str] | None:
    """Fetch the photo the client referenced in ``photo.upload`` (2026-08-05
    protocol: `value` carries a URL minted by POST /photos, never base64).

    SSRF hardening: the URL is CLIENT input, so it is never fetched blindly.
    Our own local-fallback route is read straight from disk (no HTTP at all);
    ANY other http(s) URL is fetched as-is.

    NOTE (team decision 2026-08-05, twice confirmed): no host restriction —
    the photo may live on the main Growz backend, any bucket, any CDN, and on
    a LAN address in dev. This deliberately accepts the SSRF exposure that
    comes with fetching a client-supplied URL; the remaining guards are the
    2 MB cap, the image/* content-type check and the 20 s timeout.
    Returns ``(bytes, mime)`` or ``None`` — the WS loop must not die because
    a fetch failed. Module-level so tests can monkeypatch it."""
    max_bytes = getattr(settings, "max_photo_bytes", 2_000_000)
    try:
        local = _local_photo_path(url, settings)
    except Exception:  # noqa: BLE001
        local = None
    if local is not None:
        try:
            data = local.read_bytes()
            if not data or len(data) > max_bytes:
                return None
            mime = "image/png" if local.suffix == ".png" else "image/jpeg"
            return data, mime
        except OSError:
            logger.warning("photo local read failed: %s", url)
            return None

    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        logger.warning("photo url rejected (not an http(s) url): %s", url)
        return None
    try:
        import httpx

        async with httpx.AsyncClient(
            timeout=20.0, follow_redirects=True, max_redirects=5,
            # Many CDNs (Wikimedia among them) 403 a UA-less request.
            headers={"User-Agent": "GrowzVoiceAgent/1.0 (+photo-fetch)"},
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.content
        if not data or len(data) > max_bytes:
            logger.warning("photo download rejected: %s bytes from %s",
                           len(data) if data else 0, url)
            return None
        mime = (r.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
        if not mime.startswith("image/"):
            mime = "image/jpeg"
        return data, mime
    except Exception:  # noqa: BLE001
        logger.warning("photo download failed: %s", url, exc_info=True)
        return None


def _chat_turn_recorder(store: ChatStore, doc: ChatDoc, guide: ChatGuide):
    """``on_turn_committed`` hook: only consult/symptom/general-phase turns
    are recorded raw (contract §4.7) — plain guide-phase button steps are
    captured deterministically by the guide itself (question/answer/photo
    messages), never here. Every recorded farmer turn also feeds the guide's
    sync trigger-detection / symptom-backstop hook (contract §4.10)."""

    def _record(role: str, text: str) -> None:
        try:
            text = (text or "").strip()
            if not text or not guide.records_transcript():
                return
            if role == "farmer":
                guide.note_farmer_turn(text)
            store.append_message(doc, role, "text", text)
        except Exception:  # noqa: BLE001 — a recorder must never break the call
            logger.exception("chat turn recording failed")

    return _record


def _build_session(websocket: WebSocket, settings: Settings, session_id: str):
    auth = build_auth(settings)

    async def send_json(payload: dict) -> None:
        await websocket.send_json(payload)

    async def send_bytes(data: bytes) -> None:
        await websocket.send_bytes(data)

    async def close_client() -> None:
        # session.expired left the protocol (2026-08-05): when the Live session
        # is terminally dead the server closes the farmer's socket instead —
        # the app's auto-reconnect then opens a FRESH session that resumes the
        # stored chat.
        await websocket.close(code=1000)

    if settings.use_gemini_live_audio:
        logger.info("voice agent: Gemini Live realtime path")
        return GeminiLiveSession(
            settings=settings,
            auth=auth,
            send_json=send_json,
            send_bytes=send_bytes,
            close_client=close_client,
            system_prompt=load_system_prompt(settings),
            session_id=session_id,
        )

    logger.info("voice agent: staged STT->Gemini->TTS path")
    return StreamingSession(
        settings=settings,
        stt=build_stt(settings, auth),
        gpt=build_gpt(settings, auth),
        tts=build_tts(settings, auth),
        translate=build_translate(settings, auth),
        send_json=send_json,
        send_bytes=send_bytes,
        session_id=session_id,
    )


async def run_voice_agent(websocket: WebSocket, settings: Settings, session_id: str) -> None:
    session = _build_session(websocket, settings, session_id)

    started = False
    # Per-farmer memory (Alomat remembers users). Populated when chat.start
    # carries a valid user_id; None → memoryless session (old behaviour).
    mem_store: MemoryStore | None = None
    mem_device = ""
    mem_key = ""
    mem_profile = None
    # Multichat (docs/multichat_contract.md). Populated when chat.start
    # carries a chat_id bound to an existing, owned chat; chat_guide is None
    # whenever the guided flow itself could not be wired (chat_doc may still
    # be set so teardown persists whatever the plain session produced).
    chat_store: ChatStore | None = None
    chat_doc: ChatDoc | None = None
    chat_guide: ChatGuide | None = None
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if (data := message.get("bytes")) is not None:
                if not started:
                    # Allow audio before an explicit chat.start for simple clients.
                    await session.start()
                    started = True
                await session.on_audio_chunk(data)
                continue

            text = message.get("text")
            if text is None:
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            etype = event.get("type")
            if etype == "chat.start":
                if not started:
                    # sample_rate / voice are SERVER settings (2026-08-05): the
                    # session already took the rate from
                    # settings.audio_input_sample_rate_hz at construction, and
                    # the voice comes from settings.gemini_live_voice — which
                    # may carry an "azure:<neural-voice>" prefix. Nothing the
                    # client sends is read here any more.
                    # In English mode never pass an azure:-prefixed voice token;
                    # effective_live_voice already returns "Charon" unconditionally.
                    if not getattr(settings, "is_english", False):
                        cfg_voice = getattr(settings, "effective_live_voice", None)
                        if cfg_voice and hasattr(session, "set_voice"):
                            session.set_voice(cfg_voice)
                    # Reply-script switch (before connect): language="uz-Cyrl"
                    # -> the agent answers in Uzbek Cyrillic. Only the agent's
                    # free speech/text switches; button labels stay Latin.
                    lang = (event.get("language") or "").strip().lower()
                    is_cyrl = "cyrl" in lang
                    if is_cyrl and hasattr(session, "set_memory"):
                        from app.voice.pipeline.prompts import (
                            CYRILLIC_REPLY_DIRECTIVE,
                        )
                        session.set_memory(CYRILLIC_REPLY_DIRECTIVE)
                    # Multichat: resolve chat_id -> stored ChatDoc BEFORE the
                    # rest of the prompt is assembled (the guide policy/history
                    # block and the enrichment crop fallback both need it). Any
                    # failure -> plain session, exactly today's behaviour
                    # (contract §8).
                    user_id = event.get("user_id")
                    if valid_device_id(user_id) and hasattr(session, "photo_user_id"):
                        session.photo_user_id = user_id
                    chat_id = (event.get("chat_id") or "").strip()
                    if (
                        not getattr(settings, "is_english", False)
                        and getattr(settings, "chats_enabled", False)
                        and chat_id
                        and valid_device_id(user_id)
                        and hasattr(session, "set_tool_extension")
                    ):
                        try:
                            from app.voice.chat.store import valid_chat_id

                            chat_store = ChatStore(settings)
                            doc = chat_store.read(user_id, chat_id)
                            if doc is None and valid_chat_id(chat_id):
                                # Auto-create (2026-08-05): the id is MINTED by
                                # the main Growz backend (App <-> Main <-> AI
                                # topology) — an unknown id starts a fresh chat
                                # under it instead of degrading to a plain,
                                # guideless session.
                                doc = chat_store.create(user_id, chat_id)
                                logger.info(
                                    "chat auto-created for external id %s", chat_id
                                )
                            if doc is not None:
                                chat_doc = doc
                        except Exception:  # noqa: BLE001
                            logger.exception("chat load failed — continuing without")
                            chat_store = None
                            chat_doc = None
                    if chat_doc is not None and hasattr(session, "photo_chat_id"):
                        session.photo_chat_id = chat_doc.id
                    if chat_doc is not None and chat_store is not None:
                        try:
                            chat_guide = ChatGuide(
                                settings, chat_store, chat_doc,
                                send_json=websocket.send_json,
                                speak_raw=session._speak_text,
                                inject_context=getattr(
                                    session, "inject_context", None
                                ),
                                finalize_case=getattr(
                                    session, "finalize_from_guide", None
                                ),
                            )
                            # uz-Cyrl: transliterate the guided-flow button
                            # labels/prompts to Cyrillic at emission too.
                            chat_guide.cyrillic = is_cyrl
                            if not chat_doc.finished:
                                session.set_tool_extension(
                                    chat_guide.build_tools(), chat_guide.handle_tool
                                )
                            session.on_turn_committed = _chat_turn_recorder(
                                chat_store, chat_doc, chat_guide
                            )
                            # Phase 2 (P2.3): "weed" chats use the weed
                            # catalogue; disease_pest/general/"" use diseases.
                            session.diagnosis_kind = (
                                chat_doc.query_type or "disease_pest"
                            )
                            # Phase 3 (P3.6): speak the agronom-check offer
                            # after the diagnosis read-aloud, chat-bound only.
                            session.agronom_offer = bool(
                                getattr(settings, "agronom_enabled", False)
                            )
                            guide_block = build_guide_prompt_block(chat_doc)
                            if guide_block:
                                session.set_memory(guide_block)
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "chat guide setup failed — chat kept, guide disabled"
                            )
                            chat_guide = None
                        # Profile-derived crop chips for «Qaysi ekin?» — the
                        # farmer's REAL Growz crops shown next to «Ekinlar».
                        # Source = settings.tenant_crops_source (mock JSON until
                        # the tenant OTP-verify 500 is fixed, then /api/tenant/
                        # crops). Independent of per-farmer memory; fail-open.
                        if chat_guide is not None:
                            try:
                                profile_crops = await get_tenant_crops(
                                    settings, user_id
                                )
                                if profile_crops:
                                    chat_guide.set_memory_crops(
                                        [c["name"] for c in profile_crops]
                                    )
                            except Exception:  # noqa: BLE001
                                logger.exception(
                                    "tenant crop chips failed — continuing without"
                                )
                    # Per-farmer memory: load the profile and extend the system
                    # prompt BEFORE the Live connection opens. Any failure →
                    # memoryless session, never a broken one.
                    # English demo mode: skip memory/enrich/guided-flow context
                    # injection; the session runs as a free English plant-doctor
                    # conversation (case tools still attached).
                    mem_kickoff = ""
                    if (
                        not getattr(settings, "is_english", False)
                        and getattr(settings, "memory_enabled", False)
                        and hasattr(session, "set_memory")
                        and valid_device_id(user_id)
                    ):
                        try:
                            mem_store = MemoryStore(settings)
                            mem_device = user_id
                            mem_key, mem_profile = mem_store.load_for_device(user_id)
                            if (
                                chat_guide is not None
                                and mem_profile is not None
                                and getattr(
                                    settings, "memory_crop_chips_enabled", False
                                )
                            ):
                                try:
                                    chat_guide.set_memory_crops(list(mem_profile.crops))
                                except Exception:  # noqa: BLE001
                                    logger.exception(
                                        "set_memory_crops failed — continuing without chips"
                                    )
                            block, mem_kickoff = build_memory_context(mem_profile)
                            # While an unfinished guide owns the opening, DON'T
                            # inject the memory facts/follow-up directive: it makes
                            # Rais auto-answer the steps from what it remembers
                            # (e.g. self-select the last crop) and free-form a
                            # diagnosis, fighting the guided flow. The profile is
                            # still loaded (teardown persists it); memory returns
                            # for plain sessions and for the consult phase of an
                            # already-finished chat.
                            guide_owns_opening = (
                                chat_guide is not None
                                and chat_doc is not None
                                and not chat_doc.finished
                            )
                            if not guide_owns_opening:
                                session.set_memory(block)
                        except Exception:  # noqa: BLE001
                            logger.exception("memory load failed — continuing without")
                            mem_store = None
                    # Enrichment: today's date + local weather + the picked crop.
                    # Appends to the system prompt like memory does; fully
                    # fail-open so a slow weather API never blocks the call. A
                    # resumed chat's own crop is the fallback when the client
                    # sends none (a brand-new chat has no crop yet — the guide
                    # picks it later).
                    # English demo mode: skip Uzbek enrichment context injection.
                    if (
                        not getattr(settings, "is_english", False)
                        and getattr(settings, "enrich_enabled", False)
                        and hasattr(session, "set_memory")
                    ):
                        try:
                            crop_name = (event.get("crop_name") or "").strip()
                            if not crop_name and chat_doc is not None:
                                crop_name = chat_doc.crop_name
                            enrich_block = await build_session_enrichment(
                                settings, crop_name, event.get("lat"), event.get("lon"),
                            )
                            if enrich_block:
                                session.set_memory(enrich_block)
                        except Exception:  # noqa: BLE001
                            logger.exception("enrichment failed — continuing without")
                    await session.start()
                    started = True
                    if chat_guide is not None:
                        # The guide speaks first (question or resume kickoff);
                        # the memory kickoff is suppressed for chat-bound calls.
                        try:
                            await chat_guide.start()
                        except Exception:  # noqa: BLE001
                            logger.exception("chat guide start failed")
                    elif mem_store is not None and mem_kickoff:
                        # Alomat speaks first: onboarding intro or greeting.
                        await session.speak_system(mem_kickoff)
                    elif getattr(settings, "is_english", False) and hasattr(session, "speak_system"):
                        # English demo mode: no memory kickoff is produced (the
                        # is_english guard above skips the whole memory block),
                        # so we emit a dedicated English-language opening so the
                        # agent speaks first instead of waiting silently.
                        await session.speak_system(ENGLISH_GREETING_KICKOFF)
                    # Dev repro harness — inert unless DEBUG_AUTOPILOT=1.
                    if os.environ.get("DEBUG_AUTOPILOT") == "1":
                        from app.voice.pipeline.dev_autopilot import run_dev_autopilot
                        asyncio.create_task(run_dev_autopilot(session))
            elif etype == "user.interrupt":
                await session.on_user_interrupt()
            elif etype in ("audio.mute", "audio.unmute") and hasattr(session, "set_muted"):
                # Toggle the agent's spoken voice without dropping the session:
                # the text/transcription keeps flowing, only audio is silenced.
                session.set_muted(etype == "audio.mute")
            elif etype == "text.input" and hasattr(session, "on_user_text"):
                # Typed farmer message — the quiet-environment / noisy-field
                # fallback for voice. Answered with voice+text as usual.
                txt = (event.get("value") or "").strip()
                if txt and started:
                    await session.on_user_text(txt[:500])
            elif etype == "chat.answer" and chat_guide is not None:
                if event.get("chat_id") == chat_guide.doc.id:
                    crop_obj = event.get("crop")
                    # "value" left chat.answer (2026-08-05): every client
                    # always sent "" — the crop name (its only real payload
                    # historically) rides in crop.name now.
                    await chat_guide.on_answer(
                        event.get("step_id") or "",
                        event.get("option_id") or "",
                        "",
                        crop=crop_obj if isinstance(crop_obj, dict) else None,
                    )
            elif etype == "photo.upload" and hasattr(session, "on_photo"):
                # New shape (2026-08-05): {chat_id, photo_id, value: <URL>} —
                # the bytes were POSTed to /photos over REST first; the server
                # downloads them from the URL here. target_part is resolved
                # server-side (the client stopped sending it).
                ev_chat = (event.get("chat_id") or "").strip()
                if chat_doc is not None and ev_chat and ev_chat != chat_doc.id:
                    continue  # stale event from another chat — drop silently
                fetched = await _download_photo(
                    (event.get("value") or "").strip(), settings,
                )
                if fetched is None:
                    continue
                photo_bytes, photo_mime = fetched
                # Refresh the interview's answer from the live chat doc — the
                # guide mutates it in place, so plant_part answered mid-call
                # is already here.
                if chat_doc is not None and hasattr(
                    session, "interview_plant_part"
                ):
                    session.interview_plant_part = chat_doc.plant_part or None
                accepted = await session.on_photo(
                    event.get("photo_id"), photo_bytes, photo_mime, None,
                )
                # Only count/advance the guide for photos the session actually
                # stored — a rejected (non-plant/oversized/over-cap) upload must
                # not inflate photos_collected toward the auto-finalize cap.
                if chat_guide is not None and accepted:
                    # The just-stored photo is the last in the session list; carry
                    # its public URL onto the photo message for the agronom UI.
                    # The client's URL IS the canonical location now (it came
                    # from POST /photos); no need to dig out the re-stored copy.
                    photo_url = (event.get("value") or "").strip()
                    await chat_guide.on_photo_received(
                        event.get("photo_id") or "", photo_url=photo_url,
                    )
            elif etype == "debug.log":
                # Client audio telemetry (app built with AUDIO_DEBUG=true).
                # print(): app loggers have no handler under uvicorn's config.
                print(f"[client-debug] {event.get('msg')}", flush=True)
    except WebSocketDisconnect:
        logger.info("voice agent disconnected")
    finally:
        await session.close()
        # Multichat teardown: fold the session's diagnosis (if any) into the
        # chat, bump updated_at, save. Wrapped like memory finalize below —
        # must never break socket teardown (contract §4.7, §8).
        if chat_store is not None and chat_doc is not None:
            try:
                diag = getattr(session, "last_diagnosis", None)
                if diag:
                    chat_doc.last_diagnosis = diag
                    text = (
                        f"{UZ['diagPrefix']} {diag.get('disease', '')} "
                        f"(ishonch: {diag.get('confidence', '')})"
                    )
                    # Phase 2 (P2.5): append the preparation names, only when
                    # the diagnosis carried any.
                    preps = diag.get("preparations") or []
                    if preps:
                        text += f" {UZ['prepPrefix']} " + ", ".join(preps)
                    chat_store.append_message(chat_doc, "rais", "diagnosis", text)
                else:
                    chat_doc.updated_at = now_iso()
                    chat_store.save(chat_doc)
            except Exception:  # noqa: BLE001
                logger.exception("chat finalize failed")
            # Phase 3 (P3.5): kick off the AI second-opinion review IFF a
            # request is pending and a diagnosis now exists. A NEW, separate
            # try-block AFTER chat finalize (so an agronom bug can never
            # break diagnosis persistence) and BEFORE memory finalize. Must
            # read the FRESH doc — the in-memory one predates any mid-call
            # agronom-request written to disk by the REST endpoint.
            try:
                from app.voice.agronom.review import maybe_start_mock_review

                fresh = chat_store.read(chat_doc.user_id, chat_doc.id)
                if fresh is not None:
                    maybe_start_mock_review(settings, chat_store, fresh)
            except Exception:  # noqa: BLE001
                logger.exception("agronom teardown kickoff failed")
        # Persist what this session taught us about the farmer. Runs AFTER
        # close() (which cancels in-flight tasks) as a plain awaited call, so
        # it cannot be cancelled by teardown; it never raises.
        if mem_store is not None:
            try:
                await asyncio.wait_for(
                    finalize_session_memory(
                        settings,
                        build_auth(settings),
                        mem_store,
                        mem_device,
                        mem_key,
                        mem_profile,
                        session.transcript_text()
                        if hasattr(session, "transcript_text") else "",
                        getattr(session, "last_diagnosis", None),
                    ),
                    25.0,
                )
            except Exception:  # noqa: BLE001
                logger.exception("post-session memory update failed")
