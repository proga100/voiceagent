"""SSRF hardening for `_download_photo` (photo.upload carries a CLIENT url):
our own /photos route is read from disk (no HTTP), remote fetches are locked
to the Spaces CDN host over https with redirects refused."""
import pytest

from app.config import Settings
from app.voice.pipeline.voice_agent import (
    _allowed_photo_hosts, _download_photo, _local_photo_path,
)

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


async def test_remote_url_rejected_without_spaces_allowlist(settings):
    # Spaces off -> no remote origin is allowed at all: the classic SSRF
    # probes must die before any socket is opened.
    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "https://evil.example/p.jpg",
        "http://127.0.0.1:8080/admin",
        "file:///etc/passwd",
    ):
        assert await _download_photo(url, settings) is None


async def test_remote_url_must_match_an_allowlisted_host(tmp_path):
    s = Settings(
        photos_dir=str(tmp_path / "photos"),
        do_spaces_public_base="https://bucket.fra1.cdn.digitaloceanspaces.com",
    )
    assert _allowed_photo_hosts(s) == {"bucket.fra1.cdn.digitaloceanspaces.com"}
    # Wrong host and http-scheme on the right host both refused pre-connect.
    assert await _download_photo("https://evil.example/p.jpg", s) is None
    assert await _download_photo(
        "http://bucket.fra1.cdn.digitaloceanspaces.com/p.jpg", s
    ) is None


async def test_main_backend_cdn_is_allowed_via_settings(tmp_path):
    # The photo will normally be stored by the MAIN Growz backend in ITS bucket,
    # so that host is configured explicitly (PHOTO_URL_ALLOWED_HOSTS) — without
    # it a legitimate main-backend URL would be refused as SSRF.
    s = Settings(
        photos_dir=str(tmp_path / "photos"),
        do_spaces_public_base="https://ours.fra1.cdn.digitaloceanspaces.com",
        photo_url_allowed_hosts="cdn.growz.io, growz-media.fra1.digitaloceanspaces.com",
    )
    assert _allowed_photo_hosts(s) == {
        "ours.fra1.cdn.digitaloceanspaces.com",
        "cdn.growz.io",
        "growz-media.fra1.digitaloceanspaces.com",
    }
    # Still nothing else: an unlisted host dies before any socket is opened.
    assert await _download_photo("https://evil.example/p.jpg", s) is None
