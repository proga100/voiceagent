"""build_memory_context: onboarding vs the three greeting tiers."""
from datetime import datetime, timedelta, timezone

from app.voice.pipeline.memory import (
    FarmerProfile,
    OpenIssue,
    build_memory_context,
)

NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)


def _seen(delta: timedelta) -> str:
    return (NOW - delta).isoformat(timespec="seconds")


def test_new_farmer_gets_onboarding():
    block, kickoff = build_memory_context(None, NOW)
    assert "[YANGI FERMER]" in block
    assert "telefon raqamini" in block          # phone ask with confirm
    assert "agronom" in kickoff.lower()
    assert "Rais" not in kickoff
    assert "Growz" not in kickoff
    assert "ismini soʻra" in kickoff


def test_recent_reconnect_is_brief():
    p = FarmerProfile(name="Karim", last_seen=_seen(timedelta(minutes=5)))
    block, kickoff = build_memory_context(p, NOW)
    assert "[FERMER PROFILI]" in block and "Karim" in block
    assert "QAYTA tanishtirma" in kickoff
    assert "Eshitaman" in kickoff


def test_days_gap_greets_with_followup():
    p = FarmerProfile(
        name="Karim",
        crops=["pomidor"],
        last_seen=_seen(timedelta(days=2)),
        open_issue=OpenIssue(problem="fitoftoroz", status="davolanmoqda"),
    )
    _, kickoff = build_memory_context(p, NOW)
    assert "fitoftoroz" in kickoff              # follow-up on the open issue
    assert "qayta tanishtirma" in kickoff.lower()


def test_long_gap_mentions_it():
    p = FarmerProfile(name="Karim", crops=["olma"], last_seen=_seen(timedelta(days=30)))
    _, kickoff = build_memory_context(p, NOW)
    assert "anchadan beri" in kickoff


def test_resolved_issue_falls_back_to_crops_followup():
    p = FarmerProfile(
        name="Karim", crops=["olma"],
        last_seen=_seen(timedelta(days=2)),
        open_issue=OpenIssue(problem="shira", status="hal_bolgan"),
    )
    _, kickoff = build_memory_context(p, NOW)
    assert "shira" not in kickoff
    assert "Olma" in kickoff


def test_phone_ask_gating():
    young = FarmerProfile(name="K", sessions_count=2, last_seen=_seen(timedelta(days=1)))
    block, _ = build_memory_context(young, NOW)
    assert "Telefon raqami hali yoʻq" in block

    has_phone = FarmerProfile(
        name="K", phone="998901234567", sessions_count=2,
        last_seen=_seen(timedelta(days=1)),
    )
    block, _ = build_memory_context(has_phone, NOW)
    assert "Telefon raqami hali yoʻq" not in block

    old_user = FarmerProfile(name="K", sessions_count=9, last_seen=_seen(timedelta(days=1)))
    block, _ = build_memory_context(old_user, NOW)
    assert "Telefon raqami hali yoʻq" not in block


def test_unparseable_last_seen_treated_as_long_gap():
    p = FarmerProfile(name="Karim", last_seen="not-a-date")
    _, kickoff = build_memory_context(p, NOW)
    assert "anchadan beri" in kickoff


def test_block_stays_compact():
    p = FarmerProfile(
        name="X" * 60, region="Y" * 60,
        crops=[f"ekin{i}" for i in range(15)],
        notes=[f"eslatma {i} " * 8 for i in range(10)],
        open_issue=OpenIssue(problem="uzun muammo " * 5, diagnosis="tashxis"),
        last_seen=_seen(timedelta(days=2)),
    )
    block, _ = build_memory_context(p, NOW)
    # crops shown capped at 8, notes at 5 — block must stay prompt-friendly
    assert len(block) < 1600
