"""/openapi.json contract: every REST route carries tags + a documented 200
schema, and the /ws/voice control-plane models (app/schemas.py) are injected
into components — so a future endpoint or event can't silently ship
undocumented, and /docs keeps rendering the full protocol."""
import pytest
from fastapi.testclient import TestClient

from app.api_schemas import WS_CLIENT_EVENTS, WS_SERVER_EVENTS
from app.config import Settings, get_settings
from app.main import create_app

REST_PATHS = {
    "/health": {"get"},
    "/crops": {"get"},
    "/chats": {"get", "post"},
    "/chats/{chat_id}": {"get"},
    "/chats/{chat_id}/agronom-request": {"post"},
    "/chats/{chat_id}/agronom-review": {"post"},
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    get_settings.cache_clear()
    settings = Settings(chats_dir=str(tmp_path / "chats"))
    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    return TestClient(create_app())


@pytest.fixture
def spec(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    return r.json()


def test_every_rest_route_is_documented(spec):
    assert set(spec["paths"].keys()) == set(REST_PATHS.keys())
    for path, methods in REST_PATHS.items():
        assert set(spec["paths"][path].keys()) == methods


def test_every_operation_has_tags_and_a_200_schema(spec):
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            assert op.get("tags"), f"{method.upper()} {path} has no tag"
            ok = op["responses"].get("200")
            assert ok, f"{method.upper()} {path} has no 200 response"
            schema = ok["content"]["application/json"]["schema"]
            assert "$ref" in schema, f"{method.upper()} {path} 200 lacks a model"


def test_documented_errors_match_the_handlers(spec):
    review = spec["paths"]["/chats/{chat_id}/agronom-review"]["post"]["responses"]
    assert {"200", "400", "401", "404", "409"} <= set(review.keys())
    detail = spec["paths"]["/chats/{chat_id}"]["get"]["responses"]
    assert {"200", "400", "404"} <= set(detail.keys())


def test_ws_event_models_are_injected(spec):
    schemas = spec["components"]["schemas"]
    for model in (*WS_CLIENT_EVENTS, *WS_SERVER_EVENTS):
        assert model.__name__ in schemas, f"{model.__name__} missing from components"
    # Nested models referenced by events ride along.
    assert "ChatOption" in schemas
    # Spot-check one shape survived the ref rewrite intact.
    session = schemas["SessionStart"]
    assert {"type", "user_id", "chat_id", "crop_id"} <= set(session["properties"])


def test_description_covers_the_ws_protocol(spec):
    desc = spec["info"]["description"]
    assert "/ws/voice" in desc
    assert "4401" in desc  # the app-level unauthorized close code
    assert "PCM16" in desc


def test_swagger_ui_serves(client):
    r = client.get("/docs")
    assert r.status_code == 200
    assert "swagger" in r.text.lower()
