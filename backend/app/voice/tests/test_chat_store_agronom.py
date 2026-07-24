"""ChatStore.save() agronom_review preservation rule (contract addendum
P3.1): a writer that never saw a review can never erase one."""
import pytest

from app.config import Settings
from app.voice.chat.models import AgronomReview
from app.voice.chat.store import ChatStore

DEV = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def store(tmp_path):
    return ChatStore(Settings(chats_dir=str(tmp_path / "chats")))


def test_review_less_save_preserves_on_disk_pending_review(store):
    doc = store.create(DEV)
    # A concurrent writer sets a pending review directly on disk.
    doc.agronom_review = AgronomReview(status="pending", requested_at="t1")
    store.save(doc)

    # A different, review-less in-memory doc (e.g. the live session's guide)
    # saves next — it must NOT erase the pending review.
    stale = store.read(DEV, doc.id)
    stale.agronom_review = None
    stale.title = "Updated by guide"
    store.save(stale)

    reloaded = store.read(DEV, doc.id)
    assert reloaded.title == "Updated by guide"
    assert reloaded.agronom_review is not None
    assert reloaded.agronom_review.status == "pending"
    assert reloaded.agronom_review.requested_at == "t1"


def test_review_less_save_preserves_on_disk_done_review(store):
    doc = store.create(DEV)
    doc.agronom_review = AgronomReview(
        status="done", requested_at="t1", reviewed_at="t2", verdict="confirmed",
        expert_summary="Tashxis toʻgʻri.",
    )
    store.save(doc)

    stale = store.read(DEV, doc.id)
    stale.agronom_review = None
    store.save(stale)

    reloaded = store.read(DEV, doc.id)
    assert reloaded.agronom_review.status == "done"
    assert reloaded.agronom_review.verdict == "confirmed"


def test_doc_with_review_writes_it_through_unchanged(store):
    doc = store.create(DEV)
    doc.agronom_review = AgronomReview(status="pending", requested_at="t1")
    store.save(doc)

    fresh = store.read(DEV, doc.id)
    fresh.agronom_review.status = "done"
    fresh.agronom_review.verdict = "adjusted"
    store.save(fresh)

    reloaded = store.read(DEV, doc.id)
    assert reloaded.agronom_review.status == "done"
    assert reloaded.agronom_review.verdict == "adjusted"


def test_no_on_disk_file_yet_review_less_save_is_a_plain_write(store):
    # No file exists yet (first save): the preservation branch's
    # `path.exists()` guard means this is just the plain first write.
    doc = store.create(DEV)
    assert doc.agronom_review is None
    reloaded = store.read(DEV, doc.id)
    assert reloaded.agronom_review is None


def test_on_disk_review_less_review_less_save_stays_none(store):
    doc = store.create(DEV)  # agronom_review None, saved once already
    doc.title = "Second write"
    store.save(doc)
    reloaded = store.read(DEV, doc.id)
    assert reloaded.agronom_review is None
    assert reloaded.title == "Second write"


def test_corrupt_on_disk_file_falls_back_to_plain_write(store, tmp_path):
    doc = store.create(DEV)
    path = tmp_path / "chats" / DEV / f"{doc.id}.json"
    path.write_text("{not json", encoding="utf-8")

    # A review-less save over a corrupt on-disk file must not raise; the
    # merge attempt fails silently and the plain write proceeds.
    doc.title = "Recovered"
    store.save(doc)

    reloaded = store.read(DEV, doc.id)
    assert reloaded is not None
    assert reloaded.title == "Recovered"
    assert reloaded.agronom_review is None
