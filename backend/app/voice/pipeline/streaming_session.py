"""Streaming session — the orchestration heart of the voice agent.

One instance per WebSocket connection. Coordinates the modular Yandex pipeline:

    mic audio --> STT v3 (partials/finals)
              --> on final: YandexGPT stream --> sentence chunker
              --> TTS v3 per sentence --> binary audio out

Concurrency model (asyncio):
  * ``_stt_loop``  drains STT transcripts, emits stt.partial/stt.final, and
    triggers a turn on each final.
  * ``_run_turn``  streams GPT -> chunker -> TTS for one user utterance. It is the
    cancellable unit: barge-in cancels this task and flushes pending audio.

Turn-taking uses SpeechKit's built-in EOU endpointer (final transcripts), so no
separate VAD is needed for endpointing. Barge-in is driven by an explicit
``user.interrupt`` or by fresh speech (a new STT partial) arriving mid-response.

Transport is abstracted to two async callables (``send_json`` / ``send_bytes``)
so a future LiveKit/SIP gateway can reuse this unchanged.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from app.config import Settings
from app.voice.pipeline.chunker import SentenceChunker
from app.voice.pipeline.conversation_state import ConversationState
from app.voice.pipeline.filler import FillerBank
from app.voice.pipeline.latency_tracker import LatencyTracker
from app.voice.pipeline.prompts import (
    AGRICULTURE_SYSTEM_PROMPT_RU,
    AGRICULTURE_SYSTEM_PROMPT_UZ,
)
from app.voice.vad.silero_vad import EnergyVAD, NoiseGate
from app.voice.providers.base import (
    ChatMessage,
    GPTProvider,
    STTProvider,
    TTSProvider,
)

logger = logging.getLogger("voice.session")

SendJson = Callable[[dict], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]


class StreamingSession:
    def __init__(
        self,
        *,
        settings: Settings,
        stt: STTProvider,
        gpt: GPTProvider,
        tts: TTSProvider,
        send_json: SendJson,
        send_bytes: SendBytes,
        translate: object | None = None,
        session_id: str = "default",
    ) -> None:
        self._s = settings
        self._stt = stt
        self._gpt = gpt
        self._tts = tts
        self._translate = translate
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._session_id = session_id

        self._conversation = ConversationState()
        self._state = "idle"
        self._turn_task: asyncio.Task | None = None
        self._stt_task: asyncio.Task | None = None
        self._latency: LatencyTracker | None = None
        self._got_first_mic = False
        self._current_user_text: str | None = None
        self._vad = EnergyVAD()
        self._gate = NoiseGate(
            threshold=settings.voice_input_gate_threshold,
            hangover_frames=max(1, round(settings.voice_input_gate_hangover_ms / 100)),
        )
        self._input_sample_rate = settings.audio_input_sample_rate_hz
        self._filler = FillerBank(tts, settings.tts_voice)
        self._filler_task: asyncio.Task | None = None

    def set_input_sample_rate(self, sample_rate: int | None) -> None:
        """Set the mic sample rate (from the client's session.start) before start()."""
        if sample_rate and 8000 <= sample_rate <= 48000:
            self._input_sample_rate = sample_rate

    @staticmethod
    def _norm(text: str) -> str:
        return " ".join(text.lower().split())

    def _is_refinement_of_current(self, text: str) -> bool:
        """True if `text` is a duplicate/refinement of the in-flight utterance."""
        if not self._current_user_text or self._state not in ("thinking", "speaking"):
            return False
        a, b = self._norm(text), self._norm(self._current_user_text)
        return a == b or a in b or b in a

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        await self._stt.start_stream(self._session_id, sample_rate=self._input_sample_rate)
        self._stt_task = asyncio.create_task(self._stt_loop())
        if self._s.voice_enable_filler:
            # Pre-synthesize fillers in the background so the first turn is instant.
            self._filler_task = asyncio.create_task(self._filler.prewarm())
        self._state = "listening"

    async def on_audio_chunk(self, chunk: bytes) -> None:
        if self._latency is None:
            self._latency = LatencyTracker()
        if not self._got_first_mic:
            self._latency.mark("t0_mic_first_chunk")
            self._got_first_mic = True
        # Noise gate: quiet background noise becomes silence so STT never hears it
        # (and can't trigger barge-in). Speech passes through with a hangover tail.
        if self._s.voice_input_gate_enabled:
            chunk = self._gate.gate(chunk)
        # Mid-speech barge-in: confirmed user energy while the agent is speaking.
        # Endpointing-based barge-in (new final) is handled in _stt_loop; this
        # catches interruptions before STT even produces a final. Runs on the
        # gated audio so room noise can't self-interrupt the agent.
        if (
            self._s.voice_enable_barge_in
            and self._state == "speaking"
            and self._vad.update(chunk)
        ):
            self._latency.mark("t1_vad_speech_start")
            await self._barge_in()
        await self._stt.send_audio_chunk(chunk)

    async def on_user_interrupt(self) -> None:
        await self._barge_in()

    async def close(self) -> None:
        await self._cancel_turn()
        if self._filler_task:
            self._filler_task.cancel()
        if self._stt_task:
            self._stt_task.cancel()
        await self._stt.close()

    # -- STT loop ----------------------------------------------------------

    async def _stt_loop(self) -> None:
        async for t in self._stt.receive_transcripts():
            if t.kind == "partial":
                if self._latency and not self._latency.has("t2_stt_first_partial"):
                    self._latency.mark("t2_stt_first_partial")
                await self._send_json({"type": "stt.partial", "text": t.text})
            elif t.kind == "final":
                # STT emits both `final` and `final_refinement` for one utterance;
                # ignore the refinement of the utterance we're already answering so
                # it doesn't self-barge-in and restart the turn.
                if self._is_refinement_of_current(t.text):
                    await self._send_json({"type": "stt.final", "text": t.text})
                    continue
                if self._latency:
                    self._latency.mark("t3_stt_final")
                await self._send_json({"type": "stt.final", "text": t.text})
                # A genuinely new completed utterance while the agent talks = barge-in.
                # (Mid-speech VAD barge-in is layered on in Step 8; partials alone
                # are too noisy — trailing/echo audio would self-interrupt.)
                if self._state in ("thinking", "speaking"):
                    await self._barge_in()
                self._start_turn(t.text)
            elif t.kind == "error":
                await self._send_json(
                    {"type": "error", "code": "stt", "message": t.error or "stt error"}
                )

    # -- turn execution ----------------------------------------------------

    def _start_turn(self, user_text: str) -> None:
        # Only one turn at a time; a new final supersedes nothing here because
        # finals are sequential. Guard anyway.
        if self._turn_task and not self._turn_task.done():
            return
        self._current_user_text = user_text
        self._turn_task = asyncio.create_task(self._run_turn(user_text))

    @property
    def _bridge_on(self) -> bool:
        return self._s.voice_use_russian_bridge and self._translate is not None

    async def _run_turn(self, user_text: str) -> None:
        lat = self._latency or LatencyTracker()
        self._tts.reset(self._session_id)
        self._state = "thinking"
        chunker = SentenceChunker()
        answer_parts: list[str] = []  # in the model's language (ru if bridged)
        started = {"tts": False}

        async def emit_sentence(model_sentence: str) -> None:
            # Bridge: model speaks Russian -> translate each sentence to Uzbek for
            # TTS, preserving sentence-level pipelining. Direct: speak as-is.
            uz_text = model_sentence
            if self._bridge_on:
                uz_text = await self._translate.one(
                    model_sentence, self._s.bridge_model_lang, self._s.bridge_user_lang
                )
            lat.mark("t6_first_sentence")
            if not started["tts"]:
                if not self._s.voice_enable_filler:
                    await self._send_json({"type": "tts.started"})
                self._state = "speaking"
                started["tts"] = True
            await self._send_json({"type": "llm.token", "token": uz_text + " "})
            await self._speak(uz_text, lat)

        try:
            # Instant filler: acknowledge immediately while we translate/think. This
            # is the user-perceived start of the response, so it owns t9 (TTFA).
            if self._s.voice_enable_filler:
                filler_audio = self._filler.next_audio()
                if filler_audio:
                    await self._send_json({"type": "tts.started"})
                    self._state = "speaking"
                    started["tts"] = True
                    lat.mark("t9_first_audio_to_client")
                    await self._send_bytes(filler_audio)

            # Bridge: translate the user's Uzbek to Russian for the model.
            if self._bridge_on:
                model_user_text = await self._translate.one(
                    user_text, self._s.bridge_user_lang, self._s.bridge_model_lang
                )
                system_prompt = AGRICULTURE_SYSTEM_PROMPT_RU
            else:
                model_user_text = user_text
                system_prompt = AGRICULTURE_SYSTEM_PROMPT_UZ
            self._conversation.add_user(model_user_text)

            lat.mark("t4_gpt_request")
            token_stream = self._gpt.stream_response(
                messages=self._conversation.messages(),
                system_prompt=system_prompt,
            )
            async for token in token_stream:
                if not lat.has("t5_gpt_first_token"):
                    lat.mark("t5_gpt_first_token")
                answer_parts.append(token)
                for sentence in chunker.push(token):
                    await emit_sentence(sentence)
            for sentence in chunker.flush():
                await emit_sentence(sentence)

            self._conversation.add_assistant("".join(answer_parts))
            await self._send_json({"type": "tts.finished"})
            lat.mark("t10_turn_end")
            await self._send_json({"type": "latency.metrics", **lat.snapshot()})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("turn failed")
            await self._send_json(
                {"type": "error", "code": "turn", "message": str(exc)}
            )
        finally:
            self._state = "listening"
            self._latency = None
            self._got_first_mic = False
            self._current_user_text = None

    async def _speak(self, sentence: str, lat: LatencyTracker) -> None:
        if not lat.has("t7_tts_request"):
            lat.mark("t7_tts_request")
        async for frame in self._tts.synthesize_chunk(
            sentence, self._s.tts_voice, self._session_id
        ):
            if not lat.has("t8_tts_first_audio"):
                lat.mark("t8_tts_first_audio")
            if not lat.has("t9_first_audio_to_client"):
                lat.mark("t9_first_audio_to_client")
            await self._send_bytes(frame)

    # -- barge-in ----------------------------------------------------------

    async def _barge_in(self) -> None:
        if self._state not in ("thinking", "speaking"):
            return
        await self._tts.cancel(self._session_id)
        await self._cancel_turn()
        await self._send_json({"type": "agent.interrupted"})
        self._state = "listening"

    async def _cancel_turn(self) -> None:
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
            try:
                await self._turn_task
            except asyncio.CancelledError:
                pass
        self._turn_task = None
