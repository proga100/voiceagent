import asyncio

from app.voice.pipeline.audio_queue import AudioOutQueue


async def test_put_and_get_roundtrip():
    q = AudioOutQueue()
    await q.put(b"a")
    await q.put(b"b")
    assert await q.get() == b"a"
    assert await q.get() == b"b"


async def test_end_sentinel():
    q = AudioOutQueue()
    await q.put(b"a")
    await q.end()
    assert await q.get() == b"a"
    assert await q.get() is None


async def test_flush_drops_pending():
    q = AudioOutQueue()
    for i in range(5):
        await q.put(bytes([i]))
    dropped = q.flush()
    assert dropped == 5
    assert q.qsize() == 0


async def test_overflow_drops_oldest():
    q = AudioOutQueue(maxsize=2)
    await q.put(b"1")
    await q.put(b"2")
    await q.put(b"3")  # overflow -> drops "1"
    assert q.dropped == 1
    remaining = [await q.get(), await q.get()]
    assert remaining == [b"2", b"3"]


def test_runs_under_asyncio():
    asyncio.run(test_put_and_get_roundtrip())
