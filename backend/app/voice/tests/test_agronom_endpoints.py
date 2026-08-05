"""POST /chats/{chat_id}/agronom-request and .../agronom-review (contract
addendum P3.3/P3.4) — wire shapes, validation order, fail-open, human-wins
overwrite. Mirrors test_chat_api.py's style (TestClient + settings override)."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app

DEV = "11111111-2222-3333-4444-555555555555"
DEV2 = "99999999-8888-7777-6666-555555555555"
TOKEN = "s3cr3t-agronom-token"


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    def _make(**overrides) -> TestClient:
        get_settings.cache_clear()
        kwargs = dict(chats_dir=str(tmp_path / "chats"))
        kwargs.update(overrides)
        settings = Settings(**kwargs)
        monkeypatch.setattr("app.main.get_settings", lambda: settings)
        # Avoid real network calls from the mock-kickoff seam in these tests
        # (agronom_mock_enabled defaults False anyway, but be explicit).
        app = create_app()
        return TestClient(app)

    return _make


@pytest.fixture
def client(make_client):
    return make_client(agronom_enabled=True)


# ---- POST /chats/{chat_id}/agronom-request (P3.3) --------------------------


def test_request_disabled_flag_is_404(make_client):
    client = make_client(agronom_enabled=False)
    created = client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = client.post(f"/chats/{created['id']}/agronom-request", json={"user_id": DEV})
    assert r.status_code == 404
    assert r.json() == {"detail": "agronom not available"}


def test_request_bad_user_id_is_400(client):
    created = client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = client.post(f"/chats/{created['id']}/agronom-request", json={"user_id": "bad id"})
    assert r.status_code == 400
    assert r.json() == {"detail": "invalid user_id"}


def test_request_wrong_owner_is_404(client):
    created = client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = client.post(f"/chats/{created['id']}/agronom-request", json={"user_id": DEV2})
    assert r.status_code == 404
    assert r.json() == {"detail": "chat not found"}


def test_request_missing_chat_is_404(client):
    r = client.post(f"/chats/{'a' * 32}/agronom-request", json={"user_id": DEV})
    assert r.status_code == 404
    assert r.json() == {"detail": "chat not found"}


def test_request_first_call_sets_pending_and_requested_at(client):
    created = client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = client.post(f"/chats/{created['id']}/agronom-request", json={"user_id": DEV})
    assert r.status_code == 200
    review = r.json()["data"]["agronom_review"]
    assert review == {
        "status": "pending", "requested_at": review["requested_at"],
        "reviewed_at": "", "is_mock": False, "verdict": "",
        "expert_summary": "", "expert_notes": [], "adjusted_preparations": [],
    }
    assert review["requested_at"] != ""


def test_request_is_idempotent_on_repeat(client):
    created = client.post("/chats", json={"user_id": DEV}).json()["data"]
    first = client.post(
        f"/chats/{created['id']}/agronom-request", json={"user_id": DEV}
    ).json()["data"]["agronom_review"]
    second = client.post(
        f"/chats/{created['id']}/agronom-request", json={"user_id": DEV}
    ).json()["data"]["agronom_review"]
    assert first == second


def test_request_does_not_change_a_done_review(make_client):
    client = make_client(agronom_enabled=True, agronom_review_token=TOKEN)
    created = client.post("/chats", json={"user_id": DEV}).json()["data"]
    client.post(f"/chats/{created['id']}/agronom-request", json={"user_id": DEV})
    client.post(
        f"/chats/{created['id']}/agronom-review",
        json={
            "user_id": DEV, "verdict": "confirmed",
            "expert_summary": "Toʻgʻri.", "expert_notes": [],
            "adjusted_preparations": [],
        },
        headers={"X-Agronom-Token": TOKEN},
    )
    before = client.get(f"/chats/{created['id']}", params={"user_id": DEV}).json()[
        "data"
    ]["agronom_review"]
    assert before["status"] == "done"

    # A repeat request must NOT reset a finished review back to pending.
    r = client.post(f"/chats/{created['id']}/agronom-request", json={"user_id": DEV})
    after = r.json()["data"]["agronom_review"]
    assert after == before


def test_response_carries_full_chat_summary_shape(client):
    created = client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = client.post(f"/chats/{created['id']}/agronom-request", json={"user_id": DEV})
    data = r.json()["data"]
    assert set(data.keys()) == {
        "id", "user_id", "title", "query_type", "crop_id", "crop_name",
        "plant_part", "symptom_done", "symptom_summary",
        "crop_context_answers", "general_question",
        "created_at", "updated_at", "finished",
        "message_count", "last_message", "agronom_review",
    }


# ---- POST /chats/{chat_id}/agronom-review (P3.4) ---------------------------


@pytest.fixture
def review_client(make_client):
    return make_client(agronom_enabled=True, agronom_review_token=TOKEN)


def test_review_disabled_flag_is_404(make_client):
    client = make_client(agronom_enabled=False, agronom_review_token=TOKEN)
    created = client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = client.post(
        f"/chats/{created['id']}/agronom-review",
        json={"user_id": DEV, "verdict": "confirmed", "expert_summary": "x"},
        headers={"X-Agronom-Token": TOKEN},
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "agronom not available"}


def test_review_empty_token_setting_is_404(make_client):
    client = make_client(agronom_enabled=True, agronom_review_token="")
    created = client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = client.post(
        f"/chats/{created['id']}/agronom-review",
        json={"user_id": DEV, "verdict": "confirmed", "expert_summary": "x"},
        headers={"X-Agronom-Token": "anything"},
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "agronom not available"}


def test_review_missing_header_is_401(review_client):
    created = review_client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = review_client.post(
        f"/chats/{created['id']}/agronom-review",
        json={"user_id": DEV, "verdict": "confirmed", "expert_summary": "x"},
    )
    assert r.status_code == 401
    assert r.json() == {"detail": "invalid token"}


def test_review_bad_token_is_401(review_client):
    created = review_client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = review_client.post(
        f"/chats/{created['id']}/agronom-review",
        json={"user_id": DEV, "verdict": "confirmed", "expert_summary": "x"},
        headers={"X-Agronom-Token": "wrong-token"},
    )
    assert r.status_code == 401
    assert r.json() == {"detail": "invalid token"}


def test_review_bad_user_id_is_400(review_client):
    created = review_client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = review_client.post(
        f"/chats/{created['id']}/agronom-review",
        json={"user_id": "bad id", "verdict": "confirmed", "expert_summary": "x"},
        headers={"X-Agronom-Token": TOKEN},
    )
    assert r.status_code == 400
    assert r.json() == {"detail": "invalid user_id"}


def test_review_missing_chat_is_404(review_client):
    r = review_client.post(
        f"/chats/{'a' * 32}/agronom-review",
        json={"user_id": DEV, "verdict": "confirmed", "expert_summary": "x"},
        headers={"X-Agronom-Token": TOKEN},
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "chat not found"}


def test_review_wrong_owner_is_404(review_client):
    created = review_client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = review_client.post(
        f"/chats/{created['id']}/agronom-review",
        json={"user_id": DEV2, "verdict": "confirmed", "expert_summary": "x"},
        headers={"X-Agronom-Token": TOKEN},
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "chat not found"}


def test_review_not_requested_is_409(review_client):
    created = review_client.post("/chats", json={"user_id": DEV}).json()["data"]
    r = review_client.post(
        f"/chats/{created['id']}/agronom-review",
        json={"user_id": DEV, "verdict": "confirmed", "expert_summary": "x"},
        headers={"X-Agronom-Token": TOKEN},
    )
    assert r.status_code == 409
    assert r.json() == {"detail": "review not requested"}


def test_review_bad_verdict_is_400(review_client):
    created = review_client.post("/chats", json={"user_id": DEV}).json()["data"]
    review_client.post(
        f"/chats/{created['id']}/agronom-request", json={"user_id": DEV}
    )
    r = review_client.post(
        f"/chats/{created['id']}/agronom-review",
        json={"user_id": DEV, "verdict": "maybe", "expert_summary": "x"},
        headers={"X-Agronom-Token": TOKEN},
    )
    assert r.status_code == 400
    assert r.json() == {"detail": "invalid verdict"}


def test_review_empty_summary_is_400(review_client):
    created = review_client.post("/chats", json={"user_id": DEV}).json()["data"]
    review_client.post(
        f"/chats/{created['id']}/agronom-request", json={"user_id": DEV}
    )
    r = review_client.post(
        f"/chats/{created['id']}/agronom-review",
        json={"user_id": DEV, "verdict": "confirmed", "expert_summary": "   "},
        headers={"X-Agronom-Token": TOKEN},
    )
    assert r.status_code == 400
    assert r.json() == {"detail": "empty expert_summary"}


def test_review_pending_to_done_sets_is_mock_false(review_client):
    created = review_client.post("/chats", json={"user_id": DEV}).json()["data"]
    review_client.post(
        f"/chats/{created['id']}/agronom-request", json={"user_id": DEV}
    )
    r = review_client.post(
        f"/chats/{created['id']}/agronom-review",
        json={
            "user_id": DEV, "verdict": "adjusted",
            "expert_summary": "Doza pasaytirilishi kerak.",
            "expert_notes": ["Ertalab bering."],
            "adjusted_preparations": [
                {"name": "TOPAZ 10% EM.K", "dose_min": 0.3, "dose_max": 0.5,
                 "unit": "l/ga", "type": "disease", "description": ""}
            ],
        },
        headers={"X-Agronom-Token": TOKEN},
    )
    assert r.status_code == 200
    review = r.json()["data"]["agronom_review"]
    assert review["status"] == "done"
    assert review["is_mock"] is False
    assert review["verdict"] == "adjusted"
    assert review["expert_summary"] == "Doza pasaytirilishi kerak."
    assert review["expert_notes"] == ["Ertalab bering."]
    assert review["adjusted_preparations"] == [
        {"name": "TOPAZ 10% EM.K", "dose_min": 0.3, "dose_max": 0.5,
         "unit": "l/ga", "type": "disease", "description": ""}
    ]
    assert review["reviewed_at"] != ""
    assert review["requested_at"] != ""


def test_review_done_overwrites_a_mock_review(review_client, tmp_path, monkeypatch):
    from app.voice.chat.models import AgronomReview
    from app.voice.chat.store import ChatStore
    from app.config import Settings

    created = review_client.post("/chats", json={"user_id": DEV}).json()["data"]
    settings = Settings(
        chats_dir=str(tmp_path / "chats"), agronom_enabled=True,
        agronom_review_token=TOKEN,
    )
    store = ChatStore(settings)
    doc = store.read(DEV, created["id"])
    doc.agronom_review = AgronomReview(
        status="done", requested_at="t0", reviewed_at="t1", is_mock=True,
        verdict="confirmed", expert_summary="AI second opinion.",
    )
    store.save(doc)

    r = review_client.post(
        f"/chats/{created['id']}/agronom-review",
        json={
            "user_id": DEV, "verdict": "adjusted",
            "expert_summary": "Real agronom overrides.",
            "expert_notes": [], "adjusted_preparations": [],
        },
        headers={"X-Agronom-Token": TOKEN},
    )
    assert r.status_code == 200
    review = r.json()["data"]["agronom_review"]
    assert review["is_mock"] is False
    assert review["expert_summary"] == "Real agronom overrides."
    assert review["verdict"] == "adjusted"


def test_review_sanitizer_applied_to_submitted_payload(review_client):
    created = review_client.post("/chats", json={"user_id": DEV}).json()["data"]
    review_client.post(
        f"/chats/{created['id']}/agronom-request", json={"user_id": DEV}
    )
    r = review_client.post(
        f"/chats/{created['id']}/agronom-review",
        json={
            "user_id": DEV, "verdict": "confirmed",
            "expert_summary": "  " + "z" * 700,
            "expert_notes": ["  n1  ", ""] + [f"n{i}" for i in range(10)],
            "adjusted_preparations": [{"name": f"P{i}"} for i in range(10)],
        },
        headers={"X-Agronom-Token": TOKEN},
    )
    review = r.json()["data"]["agronom_review"]
    assert len(review["expert_summary"]) == 600
    assert len(review["expert_notes"]) == 6
    assert review["expert_notes"][0] == "n1"
    assert len(review["adjusted_preparations"]) == 4


def test_review_appends_message_with_agronom_prefix(review_client, tmp_path):
    from app.voice.chat.store import ChatStore
    from app.config import Settings

    created = review_client.post("/chats", json={"user_id": DEV}).json()["data"]
    review_client.post(
        f"/chats/{created['id']}/agronom-request", json={"user_id": DEV}
    )
    review_client.post(
        f"/chats/{created['id']}/agronom-review",
        json={
            "user_id": DEV, "verdict": "confirmed",
            "expert_summary": "Hammasi toʻgʻri.",
            "expert_notes": [], "adjusted_preparations": [],
        },
        headers={"X-Agronom-Token": TOKEN},
    )
    settings = Settings(chats_dir=str(tmp_path / "chats"))
    store = ChatStore(settings)
    doc = store.read(DEV, created["id"])
    assert doc.messages[-1].role == "agronom"
    assert doc.messages[-1].kind == "agronom_review"
    assert doc.messages[-1].text == "Agronom javobi: Hammasi toʻgʻri."
