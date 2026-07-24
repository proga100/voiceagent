"""POC gate: prove gemini-3.1-flash-live-preview supports the case-tool plumbing.

Phase 0 of the plant-disease assistant plan. Verifies, against the REAL API:

  (a) ``tools`` accepted in ``LiveConnectConfig`` and a ``tool_call`` fires
      from an Uzbek prompt that needs a photo;
  (b) ``send_tool_response`` ack -> the model continues the turn;
  (c) an image via ``send_client_content(inline_data)`` mid-session is
      accepted and the model references it;
  (d) a plain text turn injected via ``send_client_content`` is spoken
      (the mechanism the diagnosis summary uses in Azure mode).

FAILS LOUDLY per stage — a red stage means activating the plan's fallback
(other live model for tools / SEND_PHOTOS_TO_LIVE=false for images).

Run (needs real creds in .env):
    python -m app.voice.benchmark.poc_live_tools

Results (2026-07-02, gemini-3.1-flash-live-preview): ALL PASS.
  (a) tools accepted; request_photo fired with Uzbek reason + target_part.
  (b) model kept speaking after the ack.
  (c) images MUST go via ``send_realtime_input(video=Blob)`` —
      ``send_client_content(inline_data)`` crashes on a google-genai 1.21.1
      JSON bug (raw bytes not base64'd on the Live path) and
      ``send_realtime_input(media=...)`` is server-rejected as deprecated
      (close 1007). Via the video channel the model referenced the image.
  (d) injected text turn is spoken (diagnosis-summary mechanism OK).
"""
from __future__ import annotations

import asyncio
import struct
import zlib

from app.config import get_settings
from app.voice.providers.gemini_live import _live_config
from app.voice.providers.google_auth import GoogleAuth

SYSTEM_PROMPT = (
    "Sen o'zbek fermerlariga yordam beradigan qishloq xo'jaligi yordamchisisan. "
    "Doim o'zbek tilida, 1-2 qisqa jumla bilan gapir. "
    "Kasallikni aniqlash uchun rasm kerak bo'lsa, DARHOL request_photo "
    "funksiyasini chaqir (reason va target_part bilan)."
)

USER_COMPLAINT = "Pomidorim kasal bo'lyapti, barglarida qandaydir dog'lar bor."

STAGE_TIMEOUT_S = 30.0


def _tools():
    """Inline copies of the planned request_photo / finalize_case declarations."""
    from google.genai import types

    target_parts = [
        "leaf", "stem", "fruit", "flower", "root",
        "branch", "bark", "whole_plant", "soil",
    ]
    return [
        types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name="request_photo",
                description=(
                    "Fermerdan o'simlikning kasallangan qismi rasmini so'rash. "
                    "Kamera ochiladi va fermer rasm oladi."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "reason": types.Schema(type=types.Type.STRING),
                        "target_part": types.Schema(
                            type=types.Type.STRING, enum=target_parts
                        ),
                    },
                    required=["reason", "target_part"],
                ),
            ),
            types.FunctionDeclaration(
                name="finalize_case",
                description=(
                    "Intervyu tugagach, yig'ilgan ma'lumotlarni tashxis uchun yuborish."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "summary": types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "crop": types.Schema(type=types.Type.STRING),
                                "symptoms": types.Schema(type=types.Type.STRING),
                                "farmer_language": types.Schema(type=types.Type.STRING),
                            },
                            required=["crop", "symptoms", "farmer_language"],
                        ),
                    },
                    required=["summary"],
                ),
            ),
        ])
    ]


def _green_png(size: int = 64) -> bytes:
    """A valid solid-green PNG generated without any imaging library."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB
    row = b"\x00" + b"\x2e\x7d\x32" * size  # filter 0 + green pixels
    idat = zlib.compress(row * size)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    )


async def _collect(session, *, label: str) -> dict:
    """Drain one receive() pass: gather transcription text, audio bytes,
    tool calls. Stops on tool_call or turn_complete."""
    out = {"text": "", "audio": 0, "tool_calls": []}

    async def _run() -> None:
        # google-genai's receive() generator ends at each turn_complete and
        # must be re-entered; one pass == at most one model turn.
        async for response in session.receive():
            tc = getattr(response, "tool_call", None)
            if tc is not None:
                out["tool_calls"].extend(tc.function_calls or [])
                return
            data = getattr(response, "data", None)
            if data:
                out["audio"] += len(data)
            sc = getattr(response, "server_content", None)
            if sc is None:
                continue
            ot = getattr(sc, "output_transcription", None)
            if ot is not None and ot.text:
                out["text"] += ot.text
            if getattr(sc, "turn_complete", False):
                return

    try:
        await asyncio.wait_for(_run(), timeout=STAGE_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise SystemExit(f"FAIL [{label}]: no complete model turn within "
                         f"{STAGE_TIMEOUT_S}s (got so far: {out})")
    return out


async def main() -> None:
    from google.genai import types

    settings = get_settings()
    auth = GoogleAuth(settings)
    model = settings.gemini_live_text_model  # the Azure-mode "brain"
    print(f"Model: {model}")

    client = auth.genai_client()
    config = _live_config(
        settings, system_prompt=SYSTEM_PROMPT, with_transcription=True
    )
    config.tools = _tools()  # (a) tools attach point

    async with client.aio.live.connect(model=model, config=config) as session:
        print("connect OK: tools accepted in LiveConnectConfig  [a1 PASS]")

        # ---- (a) Uzbek complaint should trigger request_photo -------------
        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=USER_COMPLAINT)]),
            turn_complete=True,
        )
        turn = await _collect(session, label="a: tool_call")
        calls = turn["tool_calls"]
        if not calls:
            raise SystemExit(
                f"FAIL [a]: model answered without calling a tool "
                f"(text={turn['text']!r}). Consider stronger tool instructions "
                f"or the fallback live model."
            )
        fc = calls[0]
        print(f"tool_call fired: {fc.name}({dict(fc.args or {})})  [a2 PASS]")

        # ---- (b) ack the tool call; model should keep talking -------------
        await session.send_tool_response(function_responses=types.FunctionResponse(
            id=fc.id, name=fc.name,
            response={"status": "camera_opened",
                      "note": "Fermerga rasmni qanday olishni bir jumlada ayt."},
        ))
        turn = await _collect(session, label="b: post-ack turn")
        if not (turn["text"] or turn["audio"]):
            raise SystemExit("FAIL [b]: model went silent after send_tool_response")
        print(f"model continued after ack: text={turn['text'][:80]!r} "
              f"audio={turn['audio']}B  [b PASS]")

        # ---- (d) injected text turn (diagnosis-summary mechanism) ---------
        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=(
                "[TIZIM] Tashxis tayyor. Fermerga shu xulosani qisqa o'qib ber: "
                "Bu fitoftoroz bo'lishi mumkin, mis kuporosi bilan ishlov bering."
            ))]),
            turn_complete=True,
        )
        turn = await _collect(session, label="d: injected summary")
        if not (turn["text"] or turn["audio"]):
            raise SystemExit("FAIL [d]: injected text turn produced no speech")
        print(f"injected summary spoken: {turn['text'][:100]!r}  [d PASS]")

        # ---- (c) image via send_client_content mid-session (LAST: it may
        # kill the socket; guarded so a/b/d verdicts survive) ---------------
        image_ok = False
        try:
            # send_client_content(inline_data) hits a google-genai JSON bug
            # (raw bytes not base64'd on the Live path) and `media=` is
            # server-deprecated — still photos ride the `video` frame channel.
            await session.send_realtime_input(
                video=types.Blob(data=_green_png(), mime_type="image/png")
            )
            await session.send_client_content(
                turns=types.Content(role="user", parts=[
                    types.Part(text="[Rasm yuborildi: barg] Rasmda nima ko'rinyapti?"),
                ]),
                turn_complete=True,
            )
            turn = await _collect(session, label="c: image turn")
            image_ok = bool(turn["text"] or turn["audio"])
            if image_ok:
                print(f"image accepted, model replied: {turn['text'][:100]!r}  [c PASS]")
        except SystemExit:
            raise
        except BaseException as e:  # surface the real close reason
            chain, seen = [], e
            while seen is not None:
                chain.append(f"{type(seen).__name__}: {seen}")
                seen = seen.__cause__ or seen.__context__
            print("image stage blew up; exception chain:")
            for line in chain[:6]:
                print(f"  {line}")
        if not image_ok:
            print("[c FAIL] -> activate fallback SEND_PHOTOS_TO_LIVE=false "
                  "(photos go only to the diagnosis call; Live sees a text placeholder)")

    print("\nVERDICT: a/b/d PASS" + (", c PASS" if image_ok else ", c FAIL (use fallback)"))


if __name__ == "__main__":
    asyncio.run(main())
