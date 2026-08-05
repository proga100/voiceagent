"""`_download_photo` accepts ANY http(s) photo URL (team decision 2026-08-05:
the photo may be stored by the main Growz backend, any bucket, any CDN). Our
own /photos route is short-circuited to a disk read — no HTTP, no traversal."""
import pytest

from app.config import Settings
from app.voice.pipeline.voice_agent import _download_photo, _local_photo_path

DEV = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def settings(tmp_path):
    return Settings(photos_dir=str(tmp_path / "photos"))


def _store(settings, user=DEV, chat="c1", name="p1.jpg", data=b"\xff\xd8jpeg"):
    d = __import__("pathlib").Path(settings.photos_dir) / user / chat
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)
    return f"http://192.168.1.99:5055/voice/photos/{user}/{chat}/{name}"


async def test_own_photos_route_is_read_from_disk_no_http(settings):
    url = _store(settings)
    got = await _download_photo(url, settings)
    assert got == (b"\xff\xd8jpeg", "image/jpeg")


async def test_local_route_rejects_traversal(settings):
    _store(settings)
    evil = "http://x/photos/../../etc/passwd"
    assert _local_photo_path(evil, settings) is None
    assert await _download_photo(evil, settings) is None


async def test_any_host_is_accepted(settings, monkeypatch):
    # No allowlist: a main-backend CDN, an S3 bucket, a LAN dev box — all fine.
    seen = []

    class _Resp:
        content = b"\x89PNG"
        headers = {"content-type": "image/png"}

        def raise_for_status(self):
            pass

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            seen.append(url)
            return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    for url in (
        "https://cdn.growz.io/a.png",
        "https://bucket.s3.amazonaws.com/b.png",
        "http://10.0.0.5:9000/minio/c.png",
    ):
        assert await _download_photo(url, settings) == (b"\x89PNG", "image/png")
    assert len(seen) == 3


async def test_non_http_urls_are_still_refused(settings):
    for url in ("file:///etc/passwd", "ftp://x/a.jpg", "", "not-a-url"):
        assert await _download_photo(url, settings) is None
