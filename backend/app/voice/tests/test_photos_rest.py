"""POST /photos (2026-08-05): the photo bytes travel over REST; the WS
`photo.upload` event carries only the returned URL. Spaces is not configured
under tests, so the store falls back to local disk and the URL points at the
GET serving route."""
import base64

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app

DEV = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def client(tmp_path, monkeypatch):
    get_settings.cache_clear()
    settings = Settings(
        chats_dir=str(tmp_path / "chats"), photos_dir=str(tmp_path / "photos"),
    )
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    return TestClient(create_app())


def _post(client, **over):
    body = {
        "user_id": DEV, "chat_id": "chat-1", "photo_id": "p1",
        "mime": "image/jpeg", "data": base64.b64encode(b"\xff\xd8jpeg").decode(),
    }
    body.update(over)
    return client.post("/photos", json=body)


def test_upload_returns_a_fetchable_url(client):
    r = _post(client)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["photo_id"] == "p1"
    # Disk fallback -> served by the GET route on the same host.
    assert "/photos/" in data["url"]
    path = data["url"].split("/photos/", 1)[1]
    got = client.get(f"/photos/{path}")
    assert got.status_code == 200
    assert got.content == b"\xff\xd8jpeg"


def test_upload_generates_photo_id_when_absent(client):
    r = _post(client, photo_id="")
    assert r.status_code == 200
    assert len(r.json()["data"]["photo_id"]) == 32


def test_upload_rejects_bad_user_id_base64_and_oversize(client):
    assert _post(client, user_id="nope").status_code == 400
    assert _post(client, data="!!!not-base64!!!").status_code == 400
    big = base64.b64encode(b"x" * 2_000_001).decode()
    assert _post(client, data=big).status_code == 413


def test_get_route_rejects_traversal(client):
    assert client.get("/photos/../../etc/passwd").status_code in (404, 400)
    assert client.get("/photos/u/c/..%2F..%2Fsecret").status_code in (404, 400)
