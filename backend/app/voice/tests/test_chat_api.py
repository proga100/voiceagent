"""GET/POST /chats, GET /chats/{id} — REST shape matches
docs/multichat_contract.md §2 exactly (this is the interop surface with the
mobile client, so the JSON shape itself is asserted field-by-field)."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app

DEV = "11111111-2222-3333-4444-555555555555"
DEV2 = "99999999-8888-7777-6666-555555555555"


@pytest.fixture
def client(tmp_path, monkeypatch):
    get_settings.cache_clear()
    settings = Settings(chats_dir=str(tmp_path / "chats"))
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    app = create_app()
    return TestClient(app)


def test_list_rejects_invalid_user_id(client):
    r = client.get("/chats", params={"user_id": "not valid"})
    assert r.status_code == 400
    assert r.json() == {"detail": "invalid user_id"}


def test_list_unknown_user_returns_empty_200(client):
    r = client.get("/chats", params={"user_id": DEV})
    assert r.status_code == 200
    assert r.json() == {"data": []}


def test_create_rejects_invalid_user_id(client):
    r = client.post("/chats", json={"user_id": "nope"})
    assert r.status_code == 400
    assert r.json() == {"detail": "invalid user_id"}


def test_create_returns_summary_shape(client):
    r = client.post("/chats", json={"user_id": DEV})
    assert r.status_code == 200
    data = r.json()["data"]
    assert set(data.keys()) == {
        "id", "user_id", "title", "query_type", "crop_id", "crop_name",
        "plant_part", "symptom_done", "symptom_summary",
        "crop_context_answers", "general_question",
        "created_at", "updated_at", "finished",
        "message_count", "last_message", "agronom_review",
    }
    assert len(data["id"]) == 32
    assert data["user_id"] == DEV
    assert data["title"] == "Yangi suhbat"
    assert data["finished"] is False
    assert data["message_count"] == 0
    assert data["last_message"] is None
    assert data["agronom_review"] is None


def test_create_body_is_actually_read_as_json_not_a_stray_query_param(client):
    # Regression guard: a route param typed as a local/closure-scoped Pydantic
    # model silently stops being parsed as the request body under
    # `from __future__ import annotations` (FastAPI resolves annotations
    # against the function's module globals) -- it would 422 with
    # loc=["query","body"] instead of creating the chat.
    r = client.post("/chats", json={"user_id": DEV})
    assert r.status_code == 200


def test_list_after_create(client):
    created = client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = client.get("/chats", params={"user_id": DEV})
    assert r.status_code == 200
    ids = [row["id"] for row in r.json()["data"]]
    assert created["id"] in ids


def test_list_limit_clamped(client):
    for _ in range(3):
        client.post("/chats", json={"user_id": DEV})
    r = client.get("/chats", params={"user_id": DEV, "limit": 2})
    assert len(r.json()["data"]) == 2


def test_get_chat_rejects_invalid_user_id(client):
    r = client.get("/chats/" + "a" * 32, params={"user_id": "bad id"})
    assert r.status_code == 400
    assert r.json() == {"detail": "invalid user_id"}


def test_get_chat_missing_is_404(client):
    r = client.get("/chats/" + "a" * 32, params={"user_id": DEV})
    assert r.status_code == 404
    assert r.json() == {"detail": "chat not found"}


def test_get_chat_owner_mismatch_is_404_not_403(client):
    created = client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = client.get(f"/chats/{created['id']}", params={"user_id": DEV2})
    assert r.status_code == 404
    assert r.json() == {"detail": "chat not found"}


def test_get_chat_returns_detail_shape_with_messages_and_diagnosis(client):
    created = client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = client.get(f"/chats/{created['id']}", params={"user_id": DEV})
    assert r.status_code == 200
    data = r.json()["data"]
    assert set(data.keys()) == {
        "id", "user_id", "title", "query_type", "crop_id", "crop_name",
        "plant_part", "symptom_done", "symptom_summary",
        "crop_context_answers", "general_question",
        "created_at", "updated_at", "finished",
        "last_diagnosis", "messages", "agronom_review",
    }
    assert data["last_diagnosis"] is None
    assert data["messages"] == []
    assert data["agronom_review"] is None
