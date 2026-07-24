"""ChatStore: create/read/list/append round-trips, atomic write, corrupt-file
sidelining, caps, id validation — mirrors test_memory_store.py's style."""
import pytest

from app.config import Settings
from app.voice.chat.models import new_chat_doc
from app.voice.chat.store import ChatStore, valid_chat_id

DEV = "11111111-2222-3333-4444-555555555555"
DEV2 = "99999999-8888-7777-6666-555555555555"


@pytest.fixture
def store(tmp_path):
    return ChatStore(Settings(chats_dir=str(tmp_path / "chats")))


def test_valid_chat_id():
    doc = new_chat_doc(DEV)
    assert valid_chat_id(doc.id)
    assert not valid_chat_id("not-hex-and-wrong-length")
    assert not valid_chat_id("a" * 31)  # too short
    assert not valid_chat_id("A" * 32)  # uppercase not allowed
    assert not valid_chat_id(None)
    assert not valid_chat_id(42)


def test_create_writes_file_and_returns_doc(store, tmp_path):
    doc = store.create(DEV)
    assert doc.user_id == DEV
    assert doc.title == "Yangi suhbat"
    path = tmp_path / "chats" / DEV / f"{doc.id}.json"
    assert path.exists()


def test_read_round_trip(store):
    created = store.create(DEV)
    loaded = store.read(DEV, created.id)
    assert loaded is not None
    assert loaded.id == created.id
    assert loaded.user_id == DEV


def test_read_missing_chat_returns_none(store):
    assert store.read(DEV, "a" * 32) is None


def test_read_rejects_invalid_ids(store):
    assert store.read("../../etc/passwd", "a" * 32) is None
    assert store.read(DEV, "not-a-valid-chat-id") is None


def test_read_owner_mismatch_is_caller_responsibility(store):
    # store.read only keys by (user_id, chat_id) — the owner-mismatch 404
    # check is the caller's job (contract §2.3); reading under a *different*
    # user_id than the one it was created for simply finds nothing.
    doc = store.create(DEV)
    assert store.read(DEV2, doc.id) is None


def test_corrupt_chat_sidelined_as_bad(store, tmp_path):
    doc = store.create(DEV)
    path = tmp_path / "chats" / DEV / f"{doc.id}.json"
    path.write_text("{not json", encoding="utf-8")
    assert store.read(DEV, doc.id) is None
    assert path.with_suffix(".json.bad").exists()
    assert not path.exists()


def test_save_is_atomic_tmp_then_replace(store, tmp_path):
    doc = store.create(DEV)
    doc.title = "Updated title"
    store.save(doc)
    path = tmp_path / "chats" / DEV / f"{doc.id}.json"
    tmp = path.with_suffix(".json.tmp")
    assert path.exists()
    assert not tmp.exists()
    assert store.read(DEV, doc.id).title == "Updated title"


def test_save_caps_title_length(store):
    doc = store.create(DEV)
    doc.title = "x" * 200
    store.save(doc)
    assert len(store.read(DEV, doc.id).title) == 60


def test_list_summaries_empty_for_unknown_user(store):
    assert store.list_summaries(DEV) == []


def test_list_summaries_newest_updated_first(store):
    a = store.create(DEV)
    b = store.create(DEV)
    a.updated_at = "2026-01-01T00:00:00+00:00"
    b.updated_at = "2026-02-01T00:00:00+00:00"
    store.save(a)
    store.save(b)
    out = store.list_summaries(DEV)
    assert [row["id"] for row in out] == [b.id, a.id]


def test_list_summaries_skips_corrupt_files(store, tmp_path):
    good = store.create(DEV)
    bad_path = tmp_path / "chats" / DEV / ("b" * 32 + ".json")
    bad_path.write_text("{not json", encoding="utf-8")
    out = store.list_summaries(DEV)
    assert [row["id"] for row in out] == [good.id]
    assert bad_path.with_suffix(".json.bad").exists()


def test_list_summaries_respects_limit_and_clamps(store):
    for _ in range(5):
        store.create(DEV)
    assert len(store.list_summaries(DEV, limit=2)) == 2
    # Clamped to [1, 100] rather than trusted verbatim.
    assert len(store.list_summaries(DEV, limit=0)) == 1
    assert len(store.list_summaries(DEV, limit=99999)) == 5


def test_list_summaries_rejects_invalid_user_id(store):
    assert store.list_summaries("../../etc/passwd") == []


def test_append_message_updates_title_count_and_timestamp(store):
    doc = store.create(DEV)
    before = doc.updated_at
    store.append_message(doc, "farmer", "text", "Salom")
    assert len(doc.messages) == 1
    assert doc.messages[0].role == "farmer"
    assert doc.messages[0].kind == "text"
    assert doc.messages[0].text == "Salom"
    reloaded = store.read(DEV, doc.id)
    assert len(reloaded.messages) == 1
    assert reloaded.updated_at >= before


def test_append_message_truncates_long_text(store):
    doc = store.create(DEV)
    store.append_message(doc, "farmer", "text", "y" * 3000)
    assert len(doc.messages[-1].text) == 2000


def test_append_message_caps_history_at_500(store):
    doc = store.create(DEV)
    for i in range(510):
        store.append_message(doc, "farmer", "text", f"msg-{i}")
    assert len(doc.messages) == 500
    # Oldest entries dropped, newest kept.
    assert doc.messages[-1].text == "msg-509"
    assert doc.messages[0].text == "msg-10"


def test_append_message_recomputes_title_from_crop_and_query_type(store):
    doc = store.create(DEV)
    doc.crop_name = "Pomidor"
    doc.query_type = "disease_pest"
    store.append_message(doc, "farmer", "answer", "Kasalliklar va zararkunandalar")
    assert doc.title == "Pomidor — kasallik"


def test_multiple_users_do_not_collide(store):
    a = store.create(DEV)
    b = store.create(DEV2)
    assert store.read(DEV, a.id) is not None
    assert store.read(DEV, b.id) is None
    assert store.read(DEV2, b.id) is not None
