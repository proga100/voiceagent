"""Flushable outbound audio buffer for TTS frames.

Wraps a bounded ``asyncio.Queue``. Two behaviours the pipeline needs that a raw
Queue doesn't give cleanly:

  * ``flush()`` — drop everything pending instantly (barge-in: stop queued audio).
  * bounded with drop-oldest on overflow — a slow client must never block TTS or
    grow memory without bound.

A sentinel (``None``) signals end-of-stream to the sender task.
"""
from __future__ import annotations

import asyncio

_END = None


class AudioOutQueue:
    def __init__(self, maxsize: int = 50) -> None:
        self._q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=maxsize)
        self._dropped = 0

    @property
    def dropped(self) -> int:
        return self._dropped

    async def put(self, frame: bytes) -> None:
        """Enqueue a frame; on overflow drop the oldest to make room."""
        try:
            self._q.put_nowait(frame)
        except asyncio.QueueFull:
            try:
                self._q.get_nowait()
                self._dropped += 1
            except asyncio.QueueEmpty:
                pass
            await self._q.put(frame)

    async def end(self) -> None:
        """Signal end-of-stream to the consumer."""
        await self._q.put(_END)

    async def get(self) -> bytes | None:
        """Get the next frame, or ``None`` for end-of-stream."""
        return await self._q.get()

    def flush(self) -> int:
        """Drop all pending frames (barge-in). Returns the count dropped."""
        count = 0
        while True:
            try:
                self._q.get_nowait()
                count += 1
            except asyncio.QueueEmpty:
                break
        return count

    def qsize(self) -> int:
        return self._q.qsize()
