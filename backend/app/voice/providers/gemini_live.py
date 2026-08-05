"""Gemini Live API — the ONLY Google path to spoken Uzbek.

Google Cloud Text-to-Speech has no Uzbek voice. The Gemini Live API, however,
lists Uzbek among its supported languages, and the **half-cascade** Live model
(``gemini-live-2.5-flash-preview``) honours an explicit ``language_code`` in its
``speech_config`` — so we drive Uzbek output by setting ``language_code=uz-UZ``.
The prebuilt voice name (e.g. ``Aoede``) is only a timbre; the language comes
from the language code, NOT from a Cloud TTS voice id.

This module exposes two entry points over one Live connection shape:

* :func:`synthesize_uzbek` — one-shot *text -> audio* (used by the staged TTS
  provider and by the ``poc_live_audio`` proof-of-concept).
* :class:`GeminiLiveSession` — a full realtime *audio-in -> audio-out*
  conversation with input/output transcription and built-in interruption,
  used directly by the WebSocket agent when ``USE_GEMINI_LIVE_AUDIO`` is on.

If the Live API / audio modality is unavailable on the account, region, or SDK,
calls raise a clear error rather than silently degrading to another language.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date, datetime
from typing import AsyncIterator, Awaitable, Callable
from zoneinfo import ZoneInfo

from app.config import Settings
from app.voice.enrich.tenant_crops import build_planting_profile, get_tenant_planting
from app.voice.enrich.treatments import (
    find_preparations,
    find_preparations_by_id,
    get_crop_diseases,
)
from app.voice.pipeline.chunker import SentenceChunker
from app.voice.pipeline.diagnosis import DiagnosisResult, diagnose
from app.voice.pipeline.photo_analysis import analyze_selected_photos
from app.voice.pipeline.photo_quality import assess_photo_quality, dhash_distance
from app.voice.pipeline.photo_select import select_best_photos
from app.voice.pipeline.photo_store import PhotoStore
from app.voice.pipeline.tools import PhotoAttachment, build_case_tools
from app.voice.providers.google_auth import GoogleAuth

logger = logging.getLogger("voice.gemini_live")


def _live_config(
    settings: Settings, *, system_prompt: str | None, with_transcription: bool,
    voice: str | None = None, tools=None,
    with_resumption: bool = False, resume_handle: str | None = None,
):
    from google.genai import types

    # Live models only accept the AUDIO modality (TEXT is rejected). In Azure mode
    # we still connect with AUDIO but read the reply from output_transcription and
    # voice it with Azure instead of playing Gemini's own audio.
    cfg: dict = {"response_modalities": ["AUDIO"]}

    # Both families accept a prebuilt voice (the timbre — try different voices to
    # reduce the Uzbek accent). Native-audio models auto-detect the spoken
    # language and REJECT an explicit language_code (they still speak Uzbek when
    # prompted in Uzbek); half-cascade models additionally honour language_code.
    voice_cfg = types.VoiceConfig(
        prebuilt_voice_config=types.PrebuiltVoiceConfig(
            voice_name=voice or settings.gemini_live_voice
        )
    )
    if "native-audio" in settings.gemini_live_model:
        cfg["speech_config"] = types.SpeechConfig(voice_config=voice_cfg)
    else:
        cfg["speech_config"] = types.SpeechConfig(
            language_code=settings.gemini_live_language,
            voice_config=voice_cfg,
        )

    if system_prompt:
        cfg["system_instruction"] = types.Content(
            role="user", parts=[types.Part(text=system_prompt)]
        )
    if with_transcription:
        cfg["input_audio_transcription"] = types.AudioTranscriptionConfig()
        cfg["output_audio_transcription"] = types.AudioTranscriptionConfig()
    # Case tools (request_photo / finalize_case) — only the realtime conversation
    # attaches them; synthesize_uzbek must stay tool-free (it is pure text->audio).
    if tools:
        cfg["tools"] = tools
    # Transparent session resumption: handle=None makes the server ISSUE resume
    # handles for this session; handle=<h> RESUMES an existing session on it.
    if with_resumption and settings.live_session_resumption_enabled:
        cfg["session_resumption"] = types.SessionResumptionConfig(
            handle=resume_handle
        )
    return types.LiveConnectConfig(**cfg)


async def synthesize_uzbek(
    settings: Settings,
    auth: GoogleAuth,
    text: str,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> AsyncIterator[bytes]:
    """Yield raw PCM (24 kHz) audio for ``text`` via a one-shot Live turn."""
    if not text.strip():
        return
    from google.genai import types

    client = auth.genai_client()
    config = _live_config(settings, system_prompt=None, with_transcription=False)
    async with client.aio.live.connect(
        model=settings.gemini_live_model, config=config
    ) as session:
        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]),
            turn_complete=True,
        )
        async for response in session.receive():
            if cancelled and cancelled():
                break
            data = getattr(response, "data", None)
            if data:
                yield data
            sc = getattr(response, "server_content", None)
            if sc is not None and getattr(sc, "turn_complete", False):
                break


def _is_remote_close(exc: BaseException) -> bool:
    """True when the Live server closed the socket on purpose (session
    duration limit -> GoAway, then ConnectionClosed 1011 'deadline expired' or
    1008). Matched by exception type name (the SDK raises
    ``websockets.exceptions.ConnectionClosed*``) plus message markers, so we
    don't depend on the SDK's internal websocket library."""
    if "ConnectionClosed" in type(exc).__name__:
        return True
    msg = str(exc).lower()
    return any(m in msg for m in ("deadline expired", "goaway", "1011", "1008"))


def _azure_failure_is_permanent(exc: BaseException) -> bool:
    """True when retrying Azure cannot possibly help.

    A missing key (``AzureTTSUnavailable``) or a rejected one (401/403) stays
    broken for the rest of the session, so we switch voices on the first hit
    instead of burning a sentence per attempt. Throttling (429), 5xx and
    network timeouts are transient — those get another chance. Matched by
    exception type NAME (like ``_is_remote_close``) so this module needs no
    import of the Azure provider or httpx.
    """
    if type(exc).__name__ == "AzureTTSUnavailable":
        return True
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in (401, 403)


def _diagnosis_spoken(
    result: DiagnosisResult, preparations: list[dict], *, offer_agronom: bool = False
) -> str:
    """The [TIZIM] read-aloud script for a finished diagnosis (contract
    addendum P2.4) — FROZEN. Byte-identical to v1 when ``preparations`` is
    empty; the v1 prefix keeps its deployed bytes (plain ``'`` in ``o'qib``),
    the added sentence uses the proper okina ``ʻ`` (U+02BB). Phase 3 (P3.6):
    ``offer_agronom`` appends the §7 offer suffix LAST, after everything;
    byte-identical to Phase 2 when it stays False (the default)."""
    base = (
        "[TIZIM] Tashxis tayyor. Fermerga shu xulosani qisqa o'qib ber: "
        + result.spoken_summary
    )
    if not preparations:
        out = base
    else:
        names = " va ".join(p["name"] for p in preparations[:2])
        out = (
            base
            + f" Soʻngra bir jumlada qoʻshib ayt: davolash uchun {names} kabi "
            "preparatlar bor, ularni Growz Agroaptekasidan olsa boʻladi."
        )
    if offer_agronom:
        out += _AGRONOM_OFFER_SPOKEN
    return out


# Phase 3 (contract addendum P3.6): appended LAST, after everything, when
# GeminiLiveSession.agronom_offer is True. Backend-only [TIZIM]-family
# string (okina ʻ U+02BB; NOT in strings.dart).
_AGRONOM_OFFER_SPOKEN = (
    " Oxirida yana bir jumlada qoʻshib ayt: xohlasa, bu javobni agronom "
    "tekshirib beradi — agronom diagnoz, preparatlar roʻyxati va dozalarni "
    "koʻrib chiqib, aniqroq tavsiya beradi; buning uchun ekrandagi "
    "«Agronomga yuborish» tugmasini bossin."
)

# §4.1 — spoken verbatim (inside the guillemets) when image_confidence=="low",
# REPLACING the normal "Rasm tekshirildi" note (not appended to it).
_LOW_QUALITY_NOTE = (
    "[TIZIM] Rasm qabul qilindi, lekin sifati past. Fermerga AYNAN shu gapni "
    "ayt: «Rasm biroz noaniq, lekin tahlil qilib koʻraman. Aniqroq javob "
    "uchun yana yaqinroq va yorugʻroq rasm yuborsangiz yaxshi boʻladi.» "
    "Soʻng suhbatni davom ettir.]"
)


def _photo_refs(
    photos: list[PhotoAttachment], selected: list[PhotoAttachment]
) -> list[dict]:
    """Bytes-free photo references for wire events / persistence (§5.5).
    ``per_image_analysis`` is only populated for photos in ``selected``."""
    sel = {id(p) for p in selected}
    return [
        {
            "photo_id": p.photo_id,
            "stored_path": p.stored_path,
            "selected": id(p) in sel,
            "image_confidence": p.image_confidence,
            "duplicate_of": p.duplicate_of,
            "per_image_analysis": p.per_image_analysis if id(p) in sel else None,
        }
        for p in photos
    ]


class GeminiLiveSession:
    """Full realtime Uzbek voice conversation over the Gemini Live API.

    Drop-in alternative to the staged ``StreamingSession``: mic PCM in -> Uzbek
    audio out, with input/output transcripts surfaced as ``stt.*`` / ``llm.token``
    events so the existing test client renders unchanged. Interruption is handled
    natively by the Live server (it stops generating when the user speaks).
    """

    def __init__(
        self,
        *,
        settings: Settings,
        auth: GoogleAuth,
        send_json: Callable[[dict], Awaitable[None]],
        send_bytes: Callable[[bytes], Awaitable[None]],
        close_client: Callable[[], Awaitable[None]] | None = None,
        system_prompt: str,
        session_id: str = "default",
    ) -> None:
        self._s = settings
        self._auth = auth
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._close_client = close_client
        self._system_prompt = system_prompt
        self._session_id = session_id
        self._input_sample_rate = settings.audio_input_sample_rate_hz
        self._voice = settings.gemini_live_voice
        self._azure_mode = False
        self._azure_voice = ""
        self._azure = None  # lazy AzureTTSProvider
        self._chunker = None  # SentenceChunker for per-sentence Azure synthesis
        self._spoke = False   # sent tts.started for the current Azure turn?
        # Azure runs in its own task so synthesis never blocks reading the Gemini
        # socket (which would trip its keepalive ping timeout). Sentences flow
        # through a queue; a generation counter drops stale work on barge-in.
        self._azure_q: asyncio.Queue | None = None
        self._azure_task: asyncio.Task | None = None
        self._azure_chars = 0  # cumulative chars synthesized (for cost panel)
        self._azure_failures = 0  # consecutive synth failures; reset on success
        self._gen = 0
        self._session = None
        self._cm = None
        self._recv_task: asyncio.Task | None = None
        # Transparent session resumption: on a server-side deadline close we
        # re-establish the SAME Google Live session via SessionResumptionConfig;
        # when that is exhausted we close the client socket (no event).
        self._resume_handle: str | None = None
        self._go_away: bool = False       # breadcrumb; reconnect trigger is the close
        self._reconnects: int = 0
        self._reconnecting: bool = False  # guards every send against a half-open socket
        self._closed: bool = False        # closed-by-user signal (set first in close())
        # Connect parameters captured by start() so a reconnect rebuilds the
        # exact same Live config (Azure task/queue/chunker belong to start()).
        self._live_model: str = ""
        self._live_tools = None
        # Case tools: photos captured this session (fed to Live + diagnosis) and
        # the background diagnosis task spawned by finalize_case.
        self._photos: list[PhotoAttachment] = []
        self._case_task: asyncio.Task | None = None
        self._photo_seq = 0
        # §4 near-duplicate detection: photo_id -> dHash, in insertion order
        # (first-match-wins). Persistence root for accepted photo bytes.
        self._photo_hashes: dict[str, int] = {}
        self._photo_store = PhotoStore(settings)
        # Set externally (like diagnosis_kind) once user_id/chat_id are known.
        self.photo_user_id: str | None = None
        self.photo_chat_id: str | None = None
        # Which plant part the model last asked for via request_photo. The
        # client does not send target_part on photo.upload — the server resolves
        # it here, falling back to the interview's plant_part (set by
        # voice_agent) and finally to the whole plant.
        self.last_requested_part: str | None = None
        self.interview_plant_part: str | None = None
        # Per-farmer memory: transcript accumulated for the post-session
        # extraction call (FERMER:/ALOMAT: lines), and the diagnosis recorded
        # deterministically so memory never depends on the LLM re-reading it.
        self._mem_in: list[str] = []    # current-turn farmer transcription
        self._mem_out: list[str] = []   # current-turn agent transcription
        self._transcript: list[str] = []
        self.last_diagnosis: dict | None = None
        # Phase 2: which Growz catalogue find_preparations uses. Set by
        # voice_agent.py from the bound chat's query_type; "disease_pest" for
        # plain (chatless) sessions and for "general" chats.
        self.diagnosis_kind: str = "disease_pest"
        # Phase 3: speak the agronom-check offer after the diagnosis read-aloud.
        # Set by voice_agent.py only for chat-bound sessions with agronom_enabled.
        self.agronom_offer: bool = False
        # Parallel scribe (Google Chirp 3): raw PCM of the current farmer turn,
        # re-transcribed at turn end for accurate subtitles/transcripts. Purely
        # a scribe — Gemini keeps hearing the raw audio; nothing feeds back.
        from app.voice.providers.subtitle_stt import build_subtitle_stt
        self._stt = build_subtitle_stt(settings, auth)
        self._turn_audio = bytearray()
        self._stt_tasks: set[asyncio.Task] = set()
        # Multichat extension seam (docs/multichat_contract.md §1.6, §4.7):
        # extra Live function-tool declarations + their dispatch handler
        # (set via set_tool_extension, BEFORE start()), and an optional
        # sync hook fired for every committed transcript line.
        self._extra_tools: list | None = None
        self._tool_handler: Callable[[str, dict], Awaitable[dict | None]] | None = None
        self.on_turn_committed: Callable[[str, str], None] | None = None

    def set_input_sample_rate(self, sample_rate: int | None) -> None:
        if sample_rate and 8000 <= sample_rate <= 48000:
            self._input_sample_rate = sample_rate

    def set_voice(self, voice: str | None) -> None:
        """Pick the voice. ``azure:<uz-UZ-...Neural>`` routes the reply through
        Azure Neural TTS (native Uzbek); anything else is a Gemini Live timbre."""
        if not voice:
            return
        if voice.startswith("azure:") and self._s.azure_speech_key:
            self._azure_mode = True
            self._azure_voice = voice.split(":", 1)[1] or "uz-UZ-SardorNeural"
        elif voice.startswith("azure:"):
            # Azure asked for but never configured: stay on the Gemini voice
            # rather than starting a pipeline whose every sentence would 401.
            logger.warning("azure voice %r requested but AZURE_SPEECH_KEY is "
                           "unset — using the Gemini voice", voice)
            self._azure_mode = False
        else:
            self._azure_mode = False
            self._voice = voice

    def set_memory(self, block: str | None) -> None:
        """Append the per-farmer memory block to the system prompt. Must be
        called before :meth:`start` (the prompt is fixed at connect time)."""
        if block:
            self._system_prompt = f"{self._system_prompt}\n\n{block}"

    def set_tool_extension(
        self,
        tools: list,
        handler: Callable[[str, dict], Awaitable[dict | None]],
    ) -> None:
        """Attach extra Live function tools (merged into the case tools at
        :meth:`start`) plus their dispatch ``handler(name, args) -> dict |
        None``. Must be called before :meth:`start`. ``_handle_tool_call``
        tries the handler for any tool name it does not itself recognise,
        BEFORE falling back to the ``unknown_tool`` ack; a non-None return is
        sent as the FunctionResponse."""
        self._extra_tools = tools
        self._tool_handler = handler

    async def speak_system(self, text: str) -> None:
        """Voice a system instruction (greeting kickoff etc.) through the
        normal output path — Alomat speaks first."""
        await self._speak_text("[TIZIM] " + text)

    async def on_user_text(self, text: str) -> None:
        """A typed farmer message (`text.input`): recorded in the transcript
        like speech (input transcription never fires for typed turns) and
        answered through the normal voice output path."""
        if self._session is None or self._reconnecting:
            # The Live socket is down (deadline reconnect in flight, or gone).
            # _speak_text would no-op — and silently swallowing a typed message
            # looks like the agent ignoring the farmer, with the line ALSO
            # landing in the transcript as something the model never heard.
            # Tell the client instead, and record nothing.
            await self._send_json({
                "type": "error", "code": "not_ready",
                "message": "Aloqa tiklanmoqda — xabar yetib bormadi, "
                           "birozdan soʻng qayta yuboring.",
            })
            return
        self._flush_transcript_turn()
        self._transcript.append(f"FERMER: {text}")
        # Typed turns must flow through the same commit hook as spoken ones
        # (contract §4.7) — otherwise the chat guide never sees a typed general
        # question (no general_question capture, no server-side diag_offer) and
        # the turn is not persisted. The model's reply commits separately as
        # "rais", so this does not double-record.
        if self.on_turn_committed is not None:
            try:
                self.on_turn_committed("farmer", text)
            except Exception:  # noqa: BLE001 — a recorder must never break the call
                logger.exception("on_turn_committed failed (farmer, typed)")
        await self._speak_text(text)

    # Turns shorter than this (0.25 s at 16 kHz) are accidental taps/noise —
    # not worth a Scribe call.
    _MIN_SCRIBE_BYTES = 8000

    def _flush_transcript_turn(self) -> None:
        """Move the current turn's buffers into the transcript as tagged lines."""
        farmer = "".join(self._mem_in).strip()
        agent = "".join(self._mem_out).strip()
        self._mem_in.clear()
        self._mem_out.clear()
        audio = bytes(self._turn_audio)
        self._turn_audio.clear()  # never leak one turn's audio into the next
        if farmer:
            self._transcript.append("FERMER: " + farmer)
            if self.on_turn_committed is not None:
                try:
                    self.on_turn_committed("farmer", farmer)
                except Exception:  # noqa: BLE001 — a recorder must never break the call
                    logger.exception("on_turn_committed failed (farmer)")
            # Parallel scribe: replace the garbled line + app subtitle with a
            # real transcription of this turn's actual audio.
            if self._stt is not None and len(audio) >= self._MIN_SCRIBE_BYTES:
                idx = len(self._transcript) - 1
                task = asyncio.create_task(self._scribe_turn(idx, farmer, audio))
                self._stt_tasks.add(task)
                task.add_done_callback(self._stt_tasks.discard)
        if agent:
            self._transcript.append("RAIS: " + agent)
            if self.on_turn_committed is not None:
                try:
                    self.on_turn_committed("rais", agent)
                except Exception:  # noqa: BLE001 — a recorder must never break the call
                    logger.exception("on_turn_committed failed (rais)")

    async def _scribe_turn(self, idx: int, orig: str, audio: bytes) -> None:
        """Accurate transcription of one finished farmer turn. Never raises."""
        try:
            from app.voice.providers.subtitle_stt import plausible, to_latin_uz

            text = await self._stt.transcribe_pcm(audio, self._input_sample_rate)
            text = to_latin_uz(text.strip())
            # WARNING level: the container only surfaces warnings+, and scribe
            # troubleshooting needs these breadcrumbs (a few lines per turn).
            logger.warning(
                "scribe %s: %.1fs audio -> %r (rough %r)",
                type(self._stt).__name__, len(audio) / 32000, text[:90], orig[:60],
            )
            if not text or text == orig or len(text) > 500:
                return
            if not plausible(text, orig):
                # Both transcribed the same audio yet share no words: the
                # scribe hallucinated (TV intros etc). Keep the rough line —
                # Alomat's reply followed that hearing anyway.
                logger.warning("scribe result rejected as implausible")
                return
            if 0 <= idx < len(self._transcript):
                self._transcript[idx] = "FERMER: " + text
            await self._send_json(
                {"type": "stt.corrected", "text": text, "orig": orig}
            )
        except Exception as exc:  # noqa: BLE001 — subtitles are decoration, never fatal
            logger.warning(
                "scribe %s failed: %s: %s",
                type(self._stt).__name__, type(exc).__name__, str(exc)[:200],
            )

    def transcript_text(self) -> str:
        """Full session transcript for memory extraction; long sessions keep
        the head (onboarding facts) and tail (latest issue status)."""
        self._flush_transcript_turn()
        text = "\n".join(self._transcript)
        if len(text) > 9000:
            text = text[:3000] + "\n…\n" + text[-6000:]
        return text

    async def _open_live(self, resume_handle: str | None = None) -> None:
        """(Re)establish the Google Live connection with the captured config.

        Called by start() for the initial connect (resume_handle=None -> the
        server issues resume handles) and by the receive loop for a transparent
        reconnect (resume_handle=<h> -> RESUME the same session). Rebuilds ONLY
        the Google socket; the Azure task/queue/chunker belong to start()."""
        client = self._auth.genai_client()
        config = _live_config(
            self._s, system_prompt=self._system_prompt, with_transcription=True,
            voice=self._voice, tools=self._live_tools,
            with_resumption=True, resume_handle=resume_handle,
        )
        self._cm = client.aio.live.connect(model=self._live_model, config=config)
        self._session = await self._cm.__aenter__()

    async def start(self) -> None:
        # Attach the case tools only when enabled; disabled = legacy tool-free path.
        tools = build_case_tools() if self._s.enable_case_tools else None
        if self._extra_tools:
            tools = (tools or []) + self._extra_tools
        self._live_tools = tools
        # Azure mode uses the half-cascade model (faster reply transcription, its
        # own audio is discarded); native-audio is for the Gemini-voice path.
        self._live_model = self._s.gemini_live_model
        if self._azure_mode:
            from app.voice.providers.azure_tts import AzureTTSProvider
            self._azure = AzureTTSProvider(self._s)
            # Smaller first-clause threshold => first Azure audio starts sooner.
            self._chunker = SentenceChunker(
                first_clause_min_chars=self._s.azure_first_clause_chars
            )
            self._azure_q = asyncio.Queue()
            asyncio.create_task(self._azure.prewarm())  # open TLS early
            self._azure_task = asyncio.create_task(self._azure_speak_loop())
            self._live_model = self._s.gemini_live_text_model
        await self._open_live()
        self._recv_task = asyncio.create_task(self._receive_loop())

    # 2 MB ≈ 64 s at 16 kHz 16-bit mono — nobody's PTT turn is longer; beyond
    # the cap we keep the head (the speech) and stop appending (silence tail).
    _TURN_AUDIO_CAP = 2 * 1024 * 1024

    async def on_audio_chunk(self, chunk: bytes) -> None:
        if self._session is None or self._reconnecting:
            return
        if self._stt is not None and len(self._turn_audio) < self._TURN_AUDIO_CAP:
            self._turn_audio += chunk
        from google.genai import types

        await self._session.send_realtime_input(
            audio=types.Blob(
                data=chunk,
                mime_type=f"audio/pcm;rate={self._input_sample_rate}",
            )
        )

    async def on_user_interrupt(self) -> None:
        # Live handles barge-in server-side from the audio stream; nothing to do.
        await self._send_json({"type": "agent.interrupted"})

    @staticmethod
    def _usage_payload(um) -> dict:
        """Flatten Gemini Live ``usage_metadata`` into a ``usage`` event the client
        can price. Token counts are cumulative for the session; modality detail
        (AUDIO vs TEXT) matters because Live bills them at very different rates."""
        def by_modality(details) -> dict:
            out: dict[str, int] = {}
            for d in (details or []):
                mod = getattr(d, "modality", None)
                name = str(getattr(mod, "name", mod) or "").upper() or "OTHER"
                out[name] = out.get(name, 0) + int(getattr(d, "token_count", 0) or 0)
            return out

        prompt = getattr(um, "prompt_token_count", 0) or 0
        response = (
            getattr(um, "response_token_count", None)
            or getattr(um, "candidates_token_count", None)
            or 0
        )
        return {
            "type": "usage",
            "total": int(getattr(um, "total_token_count", 0) or 0),
            "prompt": int(prompt),
            "response": int(response),
            "prompt_modalities": by_modality(getattr(um, "prompt_tokens_details", None)),
            "response_modalities": by_modality(getattr(um, "response_tokens_details", None)),
        }

    async def _receive_loop(self) -> None:
        # google-genai's session.receive() generator ENDS at each turn_complete,
        # so it must be re-entered for the next turn. If we don't, nothing keeps
        # reading the upstream socket and it dies with a keepalive ping timeout
        # (1011) — which is why only the first turn worked. Loop until close().
        # Outer loop re-establishes the Google socket on a transparent reconnect;
        # the inner loop re-enters receive() at each turn_complete.
        while True:
          try:
            while True:
                async for response in self._session.receive():
                    # Capture resumption state before anything else: the server
                    # streams a new resume handle we stash for reconnects, and a
                    # GoAway heads-up before the deadline close.
                    sru = getattr(response, "session_resumption_update", None)
                    if (sru is not None and getattr(sru, "resumable", False)
                            and getattr(sru, "new_handle", None)):
                        self._resume_handle = sru.new_handle
                    ga = getattr(response, "go_away", None)
                    if ga is not None:
                        self._go_away = True
                        logger.warning(
                            "gemini live GoAway: time_left=%s",
                            getattr(ga, "time_left", None),
                        )
                    data = getattr(response, "data", None)
                    # In Azure mode we discard Gemini's own audio — Azure voices
                    # the reply instead (from the output transcription below).
                    if data and not self._azure_mode:
                        await self._send_bytes(data)
                    # Token usage is intentionally NOT forwarded to the client
                    # (team decision 2026-08-05): it's server-side telemetry,
                    # not something the farmer's app acts on. Log it instead so
                    # cost visibility survives the removal.
                    um = getattr(response, "usage_metadata", None)
                    if um is not None:
                        logger.debug("live usage: %s", self._usage_payload(um))
                    # Case tools: the model may call request_photo / finalize_case,
                    # or the server may cancel an in-flight call (barge-in etc.).
                    tc = getattr(response, "tool_call", None)
                    if tc is not None:
                        for fc in (tc.function_calls or []):
                            await self._handle_tool_call(fc)
                    # tool.cancelled left the protocol (2026-08-05): a
                    # cancelled request_photo only meant "hide the CTA banner",
                    # and a stale banner is harmless (dismiss/upload both work).
                    tcc = getattr(response, "tool_call_cancellation", None)
                    if tcc is not None:
                        logger.info(
                            "live tool call cancelled: %s",
                            list(getattr(tcc, "ids", None) or []),
                        )
                    sc = getattr(response, "server_content", None)
                    if sc is None:
                        continue
                    it = getattr(sc, "input_transcription", None)
                    if it is not None and getattr(it, "text", None):
                        self._mem_in.append(it.text)
                        await self._send_json(
                            {"type": "stt.partial", "value": it.text}
                        )
                    ot = getattr(sc, "output_transcription", None)
                    if ot is not None and getattr(ot, "text", None):
                        self._mem_out.append(ot.text)
                        await self._send_json({"type": "llm.token", "token": ot.text})
                        # Azure mode: queue each completed sentence for the speak
                        # task (non-blocking) as the reply transcription streams.
                        if self._azure_mode:
                            for sentence in self._chunker.push(ot.text):
                                self._azure_q.put_nowait((self._gen, sentence))
                    if getattr(sc, "interrupted", False):
                        self._flush_transcript_turn()
                        if self._azure_mode:
                            self._barge_in_azure()
                        else:
                            self._spoke = False
                        await self._send_json({"type": "agent.interrupted"})
                    if getattr(sc, "turn_complete", False):
                        self._flush_transcript_turn()
                        if self._azure_mode:
                            for sentence in self._chunker.flush():
                                self._azure_q.put_nowait((self._gen, sentence))
                            self._azure_q.put_nowait((self._gen, None))  # turn end
                        else:
                            self._spoke = False
                            await self._send_json({"type": "tts.finished"})
          except asyncio.CancelledError:
            raise
          except Exception as exc:  # noqa: BLE001
            # Transparent resumption: on a server-side deadline close (and only
            # while we hold a resume handle, resumption is on, the user hasn't
            # hung up, and we're under the reconnect cap) re-establish the SAME
            # Google session with SessionResumptionConfig(handle=...) — no
            # client-visible event, no lost context.
            if (
                _is_remote_close(exc)
                and self._s.live_session_resumption_enabled
                and self._resume_handle
                and not self._closed
                and self._reconnects < self._s.live_session_max_reconnects
            ):
                try:
                    # _session = None FIRST so every sender no-ops immediately.
                    self._reconnecting = True
                    self._session = None
                    # Best-effort close of ONLY the dead Google socket — never
                    # the client WS, self._photos, ChatGuide, or self._case_task.
                    try:
                        await self._cm.__aexit__(None, None, None)
                    except Exception:  # noqa: BLE001
                        pass
                    # Bounded: a connect that hangs would leave _reconnecting
                    # True forever, silently eating every typed message after
                    # it. Timing out falls through to the socket-close path instead.
                    await asyncio.wait_for(
                        self._open_live(self._resume_handle), timeout=15.0
                    )
                    self._reconnects += 1
                    logger.warning(
                        "gemini live transparent reconnect #%d (handle=%s…)",
                        self._reconnects, str(self._resume_handle)[:8],
                    )
                    continue  # resume the outer loop on the fresh socket
                except Exception as rexc:  # noqa: BLE001 — FAIL-OPEN
                    # A reconnect failure never crashes the WS loop: fall through
                    # to the socket-close path below.
                    logger.warning(
                        "gemini live transparent reconnect failed, "
                        "falling back to closing the client socket: %s", rexc,
                    )
                finally:
                    # Never leave _reconnecting stuck True.
                    self._reconnecting = False
            if _is_remote_close(exc):
                # Expected end-of-life: Gemini Live enforces a session duration
                # limit and closes the socket (GoAway / 1011 deadline). One calm
                # log line, and the client is told so it can end the session
                # cleanly (memory finalize runs on its session.end) and offer a
                # restart — where the greeting continues the conversation.
                # session.expired left the protocol (2026-08-05): close the
                # farmer's socket instead — the app auto-reconnects into a
                # FRESH Live session that resumes the stored chat + memory.
                logger.warning(
                    "gemini live session expired (resumption exhausted): %s", exc
                )
                if self._close_client is not None:
                    try:
                        await self._close_client()
                    except Exception:  # noqa: BLE001
                        pass
                return
            logger.exception("gemini live receive failed")
            await self._send_json(
                {"type": "error", "code": "live", "message": str(exc)}
            )
            return

    def _barge_in_azure(self) -> None:
        """Drop everything queued/in-flight for the current turn (barge-in)."""
        self._gen += 1  # stale items (older gen) are skipped by the speak loop
        if self._azure_q is not None:
            while not self._azure_q.empty():
                try:
                    self._azure_q.get_nowait()
                except asyncio.QueueEmpty:
                    break
        self._spoke = False

    async def _fallback_to_gemini(self, reason: str) -> None:
        """Azure died — finish the session in Gemini's own voice, seamlessly.

        This is nearly free because the Live socket is ALREADY connected with
        the AUDIO modality (see ``_live_config``): in Azure mode the receive
        loop generates that audio and throws it away. Clearing ``_azure_mode``
        just stops the discarding, so the very next frames reach the farmer —
        no reconnect, no re-prompt, no lost conversation context.

        The cost is the accent (Gemini approximates Uzbek where Azure is
        native) plus the tail of the sentence Azure choked on. Both beat the
        alternative, which is an agent that types but never speaks again.
        """
        if not self._azure_mode:
            return
        self._azure_mode = False
        logger.warning("azure tts -> gemini voice fallback: %s", reason)
        # Drain what Azure will now never speak. If the end-of-turn marker is
        # among it, this turn is already over and nobody else will close it —
        # so we owe the client the tts.finished the speak loop would have sent.
        # Mid-turn there is no marker yet: Gemini keeps talking and the receive
        # loop's turn_complete sends exactly one finish. Hence no duplicate.
        owed_finish = False
        if self._azure_q is not None:
            while not self._azure_q.empty():
                try:
                    _stale_gen, sentence = self._azure_q.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if sentence is None:
                    owed_finish = True
        self._spoke = False
        if owed_finish:
            await self._send_json({"type": "tts.finished"})
        # NOT sent to the client (2026-08-05): the app never parsed it, and a
        # voice swap needs no UI. Server-side log keeps the event visible.
        logger.warning("TTS fallback azure -> gemini: %s", reason)
        if self._azure is not None:
            try:
                await self._azure.aclose()
            except Exception:  # noqa: BLE001
                pass
            self._azure = None

    async def _azure_speak_loop(self) -> None:
        """Consume queued sentences and voice them with Azure, in order. Runs in
        its own task so Azure latency never blocks reading the Gemini socket."""
        try:
            while True:
                gen, sentence = await self._azure_q.get()
                if gen != self._gen:
                    continue  # stale (superseded by a barge-in)
                if sentence is None:  # end-of-turn marker
                    self._spoke = False
                    await self._send_json({"type": "tts.finished"})
                    continue
                if not sentence.strip():
                    continue
                if not self._spoke:
                    # tts.started removed from the wire (2026-08-05): the app
                    # never acted on it — its handler was a no-op.
                    self._spoke = True
                # Azure bills per character. Counted server-side only
                # (2026-08-05): the app stored the number but never showed it,
                # so this was pure telemetry on the farmer's socket.
                self._azure_chars += len(sentence)
                try:
                    async for frame in self._azure.synthesize_chunk(
                        sentence, self._azure_voice
                    ):
                        if gen != self._gen:  # barge-in mid-sentence
                            break
                        await self._send_bytes(frame)
                    self._azure_failures = 0  # a good sentence clears the streak
                except Exception as exc:  # noqa: BLE001
                    logger.exception("azure tts failed")
                    self._azure_failures += 1
                    give_up = _azure_failure_is_permanent(exc) or (
                        self._azure_failures >= self._s.azure_tts_max_failures
                    )
                    if self._s.azure_tts_fallback_to_gemini and give_up:
                        await self._fallback_to_gemini(str(exc))
                        return  # nothing will be queued for Azure again
                    await self._send_json(
                        {"type": "error", "code": "azure_tts", "message": str(exc)}
                    )
        except asyncio.CancelledError:
            raise

    # ---- Case tools -------------------------------------------------------

    async def _ack_tool(self, fc, response: dict) -> None:
        """Ack a function call so the model resumes the turn (POC learning 1)."""
        if self._session is None or self._reconnecting:
            return
        from google.genai import types

        await self._session.send_tool_response(
            function_responses=types.FunctionResponse(
                id=fc.id, name=fc.name, response=response
            )
        )

    async def _handle_tool_call(self, fc) -> None:
        """Dispatch a Live function call, then ack it immediately (never blocks the
        model waiting on the client camera or the slow diagnosis call)."""
        name = getattr(fc, "name", "")
        args = dict(getattr(fc, "args", None) or {})
        if name == "request_photo":
            part = args.get("target_part", "leaf")
            # Remember what we asked for: the client no longer echoes target_part
            # back on photo.upload, and the model may ask for a part that differs
            # from the interview's plant_part ("now the root, then the whole
            # plant"). Without this the mismatch check below would compare the
            # photo against the wrong part.
            self.last_requested_part = part
            await self._send_json({
                "type": "tool.request_photo",
                "call_id": fc.id,
                "reason": args.get("reason", ""),
                "target_part": part,
            })
            # The app shows a "Rasm olish" button; the farmer opens the camera
            # when ready, so the photo may arrive with a delay (or not at all).
            await self._ack_tool(fc, {
                "status": "photo_button_shown",
                "note": (
                    "Fermer ekranidagi «Rasm olish» tugmasini bosib rasm oladi. "
                    "Unga rasmni qanday olishni bir qisqa jumlada ayt va kut; "
                    "rasm kechikishi mumkin, suhbatni tabiiy davom ettir."
                ),
            })
        elif name == "finalize_case":
            # Double-diagnosis guard (both directions): if a case is already
            # running — e.g. the farmer tapped «Tayyor» (finalize_from_guide)
            # a beat before the model emitted finalize_case — ack but do NOT
            # spawn a second diagnosis.
            if self._case_task is not None and not self._case_task.done():
                await self._ack_tool(fc, {
                    "status": "processing",
                    "note": "Tahlil allaqachon boshlangan, kutib turing.",
                })
            else:
                case_id = uuid.uuid4().hex
                await self._send_json({"type": "diagnosis.started", "case_id": case_id})
                await self._ack_tool(fc, {
                    "status": "processing",
                    "note": "Fermerga tahlil qilinayotganini bir jumlada ayt.",
                })
                summary = dict(args.get("summary", {}) or {})
                self._case_task = asyncio.create_task(
                    self._run_finalize_case(case_id, summary)
                )
        elif self._tool_handler is not None:
            try:
                result = await self._tool_handler(name, args)
            except Exception:  # noqa: BLE001 — an extension must never crash the call
                logger.exception("tool extension handler failed: %s", name)
                result = None
            await self._ack_tool(fc, result if result is not None else {"error": "unknown_tool"})
        else:
            await self._ack_tool(fc, {"error": "unknown_tool"})

    async def finalize_from_guide(self, summary: dict) -> None:
        """Deterministic finalize from the ChatGuide (done_photos/skip/cap).
        No-ops when a case is already running (double-diagnosis guard) so a
        guide trigger can never race a model-driven finalize_case."""
        if self._case_task is not None and not self._case_task.done():
            return
        case_id = uuid.uuid4().hex
        await self._send_json({"type": "diagnosis.started", "case_id": case_id})
        self._case_task = asyncio.create_task(
            self._run_finalize_case(case_id, dict(summary or {}))
        )

    async def _run_finalize_case(self, case_id: str, summary: dict) -> None:
        """Run the diagnosis off the read loop, stream the verdict, then have the
        agent read the short spoken summary. NEVER crashes the session — voices an
        Uzbek apology on failure instead."""
        try:
            # §4 — near-duplicates are excluded from the ranking input (they
            # add no new information); never hand an empty list when photos
            # exist (the first photo can never be a dup of itself).
            ranking_input = [p for p in self._photos if p.duplicate_of is None] or self._photos
            # Phase 2 (P2.6): rank down to <=3 photos before the expensive Pro
            # call. self._photos is NEVER mutated — unselected photos stay in
            # the session (Live already saw them; memory/teardown unaffected).
            photos_for_dx = await select_best_photos(
                self._s, self._auth, ranking_input, max_n=3
            )
            crop_name = (summary.get("crop") or "").strip()
            # «Profilda bo'lsa, Sistem avtomatik oladi» — auto-pull the farmer's
            # saved-planting profile (planting date, growth period, region, field,
            # current agrotech task, GPS) into the interview so the diagnosis is
            # context-aware. Best-effort; a missing profile just drops the key.
            try:
                planting = await get_tenant_planting(self._s, crop_name)
                if planting:
                    today = datetime.now(ZoneInfo("Asia/Tashkent")).date()
                    profile = build_planting_profile(planting, today)
                    if profile:
                        summary["farmer_profile"] = profile
            except Exception:  # noqa: BLE001
                logger.exception("farmer profile lookup failed — diagnosing without")
            # Give the diagnosis model the crop's Growz disease candidates so it
            # returns a real growz_disease_id (robust link to preparations, not a
            # fuzzy name match). Best-effort — [] on any failure.
            try:
                candidates = await get_crop_diseases(
                    self._s, crop_name, self.diagnosis_kind
                )
            except Exception:  # noqa: BLE001
                logger.exception("crop disease candidates fetch failed")
                candidates = []
            # §5.4 — one specialist pre-read per SELECTED photo, attached to
            # those photos only. Best-effort: any failure -> all None, the
            # diagnose() call below runs exactly as if the step never ran.
            if self._s.per_image_analysis_enabled and photos_for_dx:
                try:
                    analyses = await analyze_selected_photos(
                        self._s, self._auth, photos_for_dx
                    )
                    for p, a in zip(photos_for_dx, analyses):
                        p.per_image_analysis = a.model_dump() if a else None
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("per-image analysis failed")
            result = await diagnose(
                self._s, self._auth, summary, photos_for_dx,
                growz_diseases=candidates,
                per_image_analyses=[p.per_image_analysis for p in photos_for_dx],
            )
            # Phase 2 (P2.2): Growz Agroapteka preparations — a SEPARATE,
            # independently fail-open lookup AFTER diagnose(), so a preparations
            # bug can never consume a successfully computed diagnosis. Prefer the
            # model-picked growz_disease_id (exact); fall back to fuzzy name match.
            try:
                preparations = []
                if result.growz_disease_id:
                    preparations = await find_preparations_by_id(
                        self._s, result.growz_disease_id
                    )
                if not preparations:
                    preparations = await find_preparations(
                        self._s, result.likely_disease, self.diagnosis_kind,
                        crop_name=crop_name,
                    )
            except Exception:  # noqa: BLE001 — belt over an already-fail-open call
                logger.exception("preparations lookup failed")
                preparations = []
            photo_refs = _photo_refs(self._photos, photos_for_dx)
            # Recorded deterministically for the per-farmer memory — the
            # post-session extraction never has to re-read the diagnosis.
            self.last_diagnosis = {
                "disease": result.likely_disease,
                "confidence": result.confidence,
                "date": date.today().isoformat(),
                "preparations": [p["name"] for p in preparations][:3],
                # Phase 3 (P3.2): additive keys — the agronom reviewer needs
                # the full AI verdict; every consumer uses .get() (tolerant).
                "result": result.model_dump(),
                "summary": summary,
                "preparations_full": preparations,
                # §5.5 — bytes-free photo references (file paths + confidence
                # + per-image pre-reads for the selected subset).
                "photos": photo_refs,
            }
            await self._send_json({
                "type": "case.diagnosis",
                "case_id": case_id,
                "result": result.model_dump(),
                "summary": summary,
                "preparations": preparations,
                "photos": photo_refs,
            })
            await self._speak_text(
                _diagnosis_spoken(result, preparations, offer_agronom=self.agronom_offer)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("diagnosis failed")
            await self._send_json(
                {"type": "error", "code": "diagnosis", "message": str(exc)}
            )
            await self._speak_text(
                "[TIZIM] Tashxisni tayyorlab boʻlmadi. Fermerdan uzr soʻrab, "
                "keyinroq qayta urinishini bir jumlada ayt."
            )

    async def _speak_text(self, text: str) -> None:
        """Inject a text turn spoken through the normal output path (POC learning
        3) — works in Azure mode too via output_transcription -> chunker."""
        if self._session is None or self._reconnecting:
            return
        from google.genai import types

        await self._session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]),
            turn_complete=True,
        )

    async def inject_context(self, text: str) -> None:
        """Push a hidden context turn into the LIVE session WITHOUT a spoken
        reply (turn_complete=False) — the model absorbs it and uses it only when
        relevant (e.g. the farmer's saved-planting profile when the crop is set).
        """
        if self._session is None or self._reconnecting or not text:
            return
        from google.genai import types

        await self._session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=text)]),
            turn_complete=False,
        )

    async def on_photo(
        self, photo_id: str | None, data: bytes, mime: str,
        target_part: str | None = None,
    ) -> bool:
        """Accept a farmer photo: enforce the size/count limits, keep it for the
        diagnosis call, and (when enabled) stream it into the Live session so the
        model comments on it in real time. Returns True only when the photo was
        actually stored (so the guide counts accepted photos, not rejects).

        ``target_part`` is resolved server-side — the client stopped sending it.
        Most specific first: what the model just asked for, then the part the
        farmer picked in the interview, then the whole plant. The order matters
        because a single session can collect several different shots ("general
        view, then close-up, then the root") while plant_part stays one value.
        """
        target_part = (
            target_part
            or self.last_requested_part
            or self.interview_plant_part
            or "whole_plant"
        )
        if not data:
            await self._send_json(
                {"type": "error", "code": "photo", "message": "empty photo"}
            )
            return False
        if len(data) > self._s.max_photo_bytes:
            await self._send_json(
                {"type": "error", "code": "photo", "message": "photo too large"}
            )
            return False
        if len(self._photos) >= self._s.max_photos_per_session:
            await self._send_json(
                {"type": "error", "code": "photo", "message": "too many photos"}
            )
            return False
        if not photo_id:
            self._photo_seq += 1
            photo_id = f"photo-{self._photo_seq}"

        # Upload-time verification (the anti-keyboard gate): one fast vision
        # call decides whether this is really a plant/tree/fruit and whether it
        # shows the part the conversation asked for. Only verified photos are
        # stored for diagnosis; the Live model hears the VERIFIED result, never
        # our assumption. Best-effort: on error the photo passes unverified.
        verdict = None
        if self._s.verify_photos:
            from app.voice.pipeline.photo_check import PART_UZ, verify_photo

            verdict = await verify_photo(
                self._s, self._auth, data, mime, target_part
            )

        if verdict is not None and not verdict.is_plant:
            # Not a plant: complete the client upload flow, but do NOT store
            # the photo or show it to the Live model — tell it what happened
            # so Alomat asks the farmer to re-shoot.
            await self._send_json({
                "type": "photo.received",
                "photo_id": photo_id,
                "count": len(self._photos),
            })
            await self._speak_text(
                "[TIZIM] Fermer yuborgan rasmda oʻsimlik, daraxt yoki meva "
                "KOʻRINMAYAPTI (boshqa narsa tushgan). Fermerga qisqa ayt: "
                "rasm yaroqsiz — xohlasa kasallangan qismni qayta suratga "
                "olsin, xohlamasa suhbat asosida ham tashxis qoʻyishing "
                "mumkinligini ayt va davom et.]"
            )
            return False

        # §4a — deterministic blur/exposure/dHash checks (advisory, never
        # blocking). Best-effort: any failure -> None, treated as "ok".
        report = None
        if self._s.photo_quality_enabled:
            try:
                report = await asyncio.to_thread(
                    assess_photo_quality, data, self._s
                )
            except Exception:  # noqa: BLE001 — quality checks are advisory only
                logger.warning("photo quality check failed", exc_info=True)
                report = None

        # §4a — near-duplicate detection against earlier accepted photos in
        # this session (insertion order, first match wins). A failed report
        # never registers a hash and never matches.
        duplicate_of: str | None = None
        if report is not None and not report.failed:
            for prev_id, prev_hash in self._photo_hashes.items():
                if dhash_distance(report.dhash, prev_hash) <= self._s.photo_dup_hamming_max:
                    duplicate_of = prev_id
                    break
            self._photo_hashes[photo_id] = report.dhash

        # §4.1 — image_confidence: "low" when either the deterministic checks
        # or the (verified) VLM verdict flag a problem. duplicate_of never
        # forces "low" on its own.
        image_confidence = "ok"
        if report is not None and (report.blurry or report.too_dark or report.too_bright):
            image_confidence = "low"
        elif (
            verdict is not None
            and not verdict.unverified
            and (not verdict.quality_ok or not verdict.symptom_visible or verdict.multiple_plants)
        ):
            image_confidence = "low"

        # Persist accepted photo bytes to disk (fail-open: None on any error,
        # the photo stays in-memory and the flow continues unaffected).
        stored_path = await asyncio.to_thread(
            self._photo_store.save,
            self.photo_user_id or "anon",
            self.photo_chat_id or self._session_id,
            photo_id,
            data,
            mime,
        )

        quality_dict = None
        if report is not None:
            quality_dict = {
                "blur_var": report.blur_var,
                "blurry": report.blurry,
                "too_dark": report.too_dark,
                "too_bright": report.too_bright,
            }
        vlm_flags = None
        if verdict is not None and not verdict.unverified:
            vlm_flags = {
                "symptom_visible": verdict.symptom_visible,
                "multiple_plants": verdict.multiple_plants,
                "quality_ok": verdict.quality_ok,
            }

        self._photos.append(
            PhotoAttachment(
                photo_id=photo_id, data=data, mime=mime, target_part=target_part,
                image_confidence=image_confidence, duplicate_of=duplicate_of,
                stored_path=stored_path, quality=quality_dict, vlm_flags=vlm_flags,
            )
        )
        await self._send_json({
            "type": "photo.received",
            "photo_id": photo_id,
            "count": len(self._photos),
            "image_confidence": image_confidence,
            "duplicate_of": duplicate_of,
        })
        if (self._s.send_photos_to_live and self._session is not None
                and not self._reconnecting):
            from google.genai import types
            from app.voice.pipeline.photo_check import PART_UZ

            # Still images MUST ride the video frame channel (POC learning 2):
            # inline_data crashes the Live path, media= is server-rejected.
            await self._session.send_realtime_input(
                video=types.Blob(data=data, mime_type=mime)
            )
            if image_confidence == "low":
                # §4.1 — REPLACES the normal "Rasm tekshirildi" note.
                await self._speak_text(_LOW_QUALITY_NOTE)
            elif verdict is not None and not verdict.unverified:
                seen_uz = PART_UZ.get(verdict.seen_part, "oʻsimlik")
                if verdict.matches_target:
                    note = f"[Rasm tekshirildi: {seen_uz} koʻrinyapti."
                else:
                    asked_uz = PART_UZ.get(target_part or "", "soʻralgan qism")
                    note = (
                        f"[Rasm tekshirildi: soʻralgan {asked_uz} emas, "
                        f"{seen_uz} koʻrinyapti. Kerak boʻlsa qayta soʻra."
                    )
                await self._speak_text(
                    f"{note} Rasmni koʻrib, qisqa izoh ber va suhbatni "
                    "davom ettir.]"
                )
            else:
                part = target_part or "oʻsimlik"
                await self._speak_text(
                    f"[Rasm yuborildi: {part}. Rasmni koʻrib, qisqa izoh ber "
                    "va suhbatni davom ettir.]"
                )
        elif image_confidence == "low":
            # §4.1 is spoken UNCONDITIONALLY (mirrors the non-plant rejection
            # path above) even when photos aren't streamed into Live — the
            # farmer must still hear the low-quality heads-up.
            await self._speak_text(_LOW_QUALITY_NOTE)
        return True  # photo stored -> the guide may count it

    async def close(self) -> None:
        # closed-by-user signal FIRST: the receive loop checks self._closed
        # before attempting any transparent reconnect, so a user hangup can
        # never race a reconnect.
        self._closed = True
        if self._recv_task:
            self._recv_task.cancel()
        if self._azure_task:
            self._azure_task.cancel()
        if self._case_task:
            self._case_task.cancel()
        if self._stt_tasks:
            # Brief grace so the transcript saved for memory/review carries
            # the last scribe corrections; stragglers are abandoned.
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._stt_tasks, return_exceptions=True),
                    4.0,
                )
            except Exception:  # noqa: BLE001
                pass
        if self._stt is not None:
            try:
                await self._stt.aclose()
            except Exception:  # noqa: BLE001
                pass
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        if self._azure is not None:
            await self._azure.aclose()
        self._session = None
        self._cm = None
