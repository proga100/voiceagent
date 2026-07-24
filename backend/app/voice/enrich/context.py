"""Compose the per-session enrichment block appended to the system prompt.

Mirrors ``build_memory_context`` (memory.py): returns a compact Uzbek
``[BUGUNGI SHAROIT]`` string so Rais opens the call already knowing the day, the
local weather, and which crop the farmer came to ask about. Every part is
optional — missing weather or crop simply drops that sentence, and the date is
always available (computed in Asia/Tashkent, not the container's UTC clock).
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.voice.enrich.tenant_crops import build_planting_profile, get_tenant_planting
from app.voice.enrich.weather import fetch_weather

_TASHKENT = ZoneInfo("Asia/Tashkent")

_UZ_MONTHS = [
    "", "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
]
_UZ_WEEKDAYS = [
    "dushanba", "seshanba", "chorshanba", "payshanba",
    "juma", "shanba", "yakshanba",
]


def _uz_date(d: date) -> str:
    return (
        f"{d.day}-{_UZ_MONTHS[d.month]} {d.year}-yil, "
        f"{_UZ_WEEKDAYS[d.weekday()]}"
    )


def _profile_facts(profile: dict | None) -> str:
    """One compact Uzbek sentence of the farmer's saved-planting facts, or ''."""
    if not profile:
        return ""
    facts: list[str] = []
    if profile.get("planted_date"):
        d = profile["planted_date"]
        days = profile.get("days_since_planting")
        facts.append(f"ekilgan sana {d}" + (f" ({days} kun oldin)" if days is not None else ""))
    if profile.get("growth_period_days"):
        facts.append(f"oʻsish davri {profile['growth_period_days']} kun")
    if profile.get("region"):
        facts.append(f"hudud {profile['region']}")
    fld = profile.get("field") or {}
    if fld.get("name"):
        f = f"dala «{fld['name']}»"
        if fld.get("area"):
            f += f" {fld['area']} {fld.get('unit', '')}".rstrip()
        facts.append(f)
    if profile.get("current_agrotech_task"):
        facts.append(f"joriy agrotexnika — {profile['current_agrotech_task']}")
    if not facts:
        return ""
    return "Fermer profili (Growz): " + ", ".join(facts) + "."


def build_enrichment_context(
    crop_name: str | None,
    weather: dict | None,
    today: date,
    profile: dict | None = None,
) -> str:
    """Return the ``[BUGUNGI SHAROIT]`` system-prompt block for this session."""
    parts = [f"[BUGUNGI SHAROIT] Bugun {_uz_date(today)}."]

    if weather and weather.get("temp_c") is not None:
        w = f"Ob-havo: {round(weather['temp_c'])}°C"
        if weather.get("sky"):
            w += f", {weather['sky']}"
        if weather.get("humidity") is not None:
            w += f", namlik {weather['humidity']}%"
        if weather.get("wind_kmh") is not None:
            w += f", shamol {round(weather['wind_kmh'])} km/soat"
        parts.append(w + ".")

    if crop_name:
        parts.append(
            f"Fermer bugun «{crop_name}» ekini haqida maslahat olgani keldi — "
            "suhbatni shu ekin atrofida yurit."
        )

    profile_line = _profile_facts(profile)
    if profile_line:
        parts.append(profile_line)

    parts.append(
        "Bu maʼlumotlardan tabiiy foydalan: sugʻorish, dorilash yoki "
        "issiq/sovuqdan himoya boʻyicha maslahat berayotganda bugungi ob-havo va "
        "faslga tayan. Fermer soʻramasa, sana yoki ob-havoni quruq takrorlama."
    )
    return " ".join(parts)


async def build_session_enrichment(
    settings: Settings,
    crop_name: str | None,
    lat: float | None,
    lon: float | None,
) -> str:
    """Fetch weather (fail-open) and compose the enrichment block for a session.

    The single entry point the WS handler calls at ``session.start``. Weather uses
    the phone GPS when given, else the configured Tashkent fallback; the date is
    always the current Asia/Tashkent day.
    """
    today = datetime.now(_TASHKENT).date()
    # The farmer's saved-planting profile (crop known at start = resumed chat).
    # Fail-open: a missing profile just drops those facts.
    profile = None
    if crop_name:
        try:
            planting = await get_tenant_planting(settings, crop_name)
            if planting:
                profile = build_planting_profile(planting, today)
        except Exception:  # noqa: BLE001
            profile = None
    weather = None
    if settings.enrich_weather_enabled:
        ploc = (profile or {}).get("location") or {}
        # Weather location: phone GPS > the planting's GPS > Tashkent fallback.
        use_lat = lat if lat is not None else ploc.get("lat", settings.weather_default_lat)
        use_lon = lon if lon is not None else ploc.get("lon", settings.weather_default_lon)
        weather = await fetch_weather(settings, use_lat, use_lon)
    return build_enrichment_context(crop_name, weather, today, profile)
