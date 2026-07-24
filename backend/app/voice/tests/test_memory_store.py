"""MemoryStore: file round-trips, corruption handling, phone linking rules."""
import pytest

from app.config import Settings
from app.voice.pipeline.memory import (
    FarmerProfile,
    MemoryStore,
    OpenIssue,
    normalize_phone,
    valid_device_id,
)

DEV = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def store(tmp_path):
    return MemoryStore(Settings(memory_dir=str(tmp_path / "memory")))


def test_new_device_is_unknown(store):
    key, profile = store.load_for_device(DEV)
    assert key == f"dev:{DEV}"
    assert profile is None


def test_save_and_load_round_trip(store):
    p = FarmerProfile(name="Karim", region="Fargʻona", crops=["pomidor"])
    store.save(f"dev:{DEV}", p)
    store.write_index(DEV, f"dev:{DEV}")
    key, loaded = store.load_for_device(DEV)
    assert key == f"dev:{DEV}"
    assert loaded is not None
    assert loaded.name == "Karim" and loaded.crops == ["pomidor"]


def test_corrupt_profile_sidelined_as_bad(store, tmp_path):
    key = f"dev:{DEV}"
    store.save(key, FarmerProfile(name="Karim"))
    path = tmp_path / "memory" / "profiles" / f"{key}.json"
    path.write_text("{not json", encoding="utf-8")
    assert store.read(key) is None
    assert path.with_suffix(".json.bad").exists()
    assert not path.exists()


def test_link_phone_renames_dev_profile(store, tmp_path):
    old_key = f"dev:{DEV}"
    p = FarmerProfile(name="Karim", phone="998901234567")
    store.save(old_key, p)
    new_key, merged = store.link_phone(DEV, old_key, p)
    assert new_key == "phone:998901234567"
    assert merged.name == "Karim"
    # dev-keyed file removed, phone-keyed exists, index repointed
    assert not (tmp_path / "memory" / "profiles" / f"{old_key}.json").exists()
    key, loaded = store.load_for_device(DEV)
    assert key == new_key and loaded is not None and loaded.name == "Karim"


def test_link_phone_merges_into_existing_reinstall(store):
    """Reinstall: existing phone profile wins identity scalars; lists union."""
    phone = "998901234567"
    existing = FarmerProfile(
        name="Karim", region="Fargʻona", phone=phone,
        crops=["pomidor"], sessions_count=7, last_seen="2026-07-01T10:00:00+00:00",
        open_issue=OpenIssue(problem="fitoftoroz", date="2026-07-01"),
    )
    store.save(f"phone:{phone}", existing)

    fresh = FarmerProfile(
        name="Boshqa Ism", region="Toshkent", phone=phone,
        crops=["olma"], sessions_count=1, last_seen="2026-07-04T10:00:00+00:00",
        open_issue=OpenIssue(problem="shira", date="2026-07-04"),
    )
    new_key, merged = store.link_phone(DEV, f"dev:{DEV}", fresh)
    assert new_key == f"phone:{phone}"
    assert merged.name == "Karim"            # existing identity wins
    assert merged.region == "Fargʻona"
    assert merged.crops == ["pomidor", "olma"]  # union, existing first
    assert merged.sessions_count == 8            # summed
    assert merged.last_seen == "2026-07-04T10:00:00+00:00"  # max
    assert merged.open_issue.problem == "shira"  # newest date wins


def test_valid_device_id():
    assert valid_device_id(DEV)
    assert valid_device_id("a" * 8)
    assert not valid_device_id("../../etc/passwd")
    assert not valid_device_id("short")
    assert not valid_device_id("has space here")
    assert not valid_device_id("a" * 65)
    assert not valid_device_id(None)
    assert not valid_device_id(42)


def test_normalize_phone():
    assert normalize_phone("91 312 45 67") == "998913124567"
    assert normalize_phone("998913124567") == "998913124567"
    assert normalize_phone("+998 91 312-45-67") == "998913124567"
    assert normalize_phone("12345") is None
    assert normalize_phone("") is None
    assert normalize_phone(None) is None
