"""Live integration check for the §7 AI senior-agronom reviewer.

NOT a unit test (hits real Gemini + Growz) — the pytest suite covers the logic
with fakes. This proves the DEPLOYED capability end to end: a realistic AI
diagnosis in -> a real Uzbek expert second opinion out.

Run:  GEMINI_API_KEY=... GROWZ_API_KEY=... python scripts/live_agronom_review.py
"""
import asyncio

from app.config import Settings
from app.voice.agronom.review import review_diagnosis
from app.voice.enrich.treatments import aclose, get_crop_diseases
from app.voice.providers.factory import build_auth

_REVIEW_INPUT = {
    "crop_name": "Pomidor",
    "plant_part": "leaf",
    "query_type": "disease_pest",
    "symptom_summary": (
        "Barglarda jigarrang va qora dogʻlar, tez tarqalyapti, nam havoda kuchaymoqda"
    ),
    "general_question": "",
    "interview_summary": {
        "crop": "Pomidor",
        "symptoms": "barglarda qora dogʻlar, tez tarqalmoqda",
        "farmer_language": "uz",
    },
    "ai_diagnosis": {
        "likely_disease": "Fitoftoroz (Kechki blight)",
        "confidence": "low",
        "differentials": [{"name": "Alternarioz", "why": "oʻxshash dogʻlar"}],
        "immediate_treatment": ["kasal barglarni yulib tashlash"],
        "prevention": ["namlikni kamaytirish"],
        "spoken_summary": "Bu fitoftoroz boʻlishi mumkin.",
    },
    "ai_preparations": [
        {"name": "Ukvadrio 25% sus.k.", "dose_min": 0.4, "dose_max": 0.6,
         "unit": "l/ga", "type": "disease"},
        {"name": "ADDRESS (Azoksistrobin 25%)", "dose_min": 0.4, "dose_max": 0.6,
         "unit": "l/ga", "type": "disease"},
    ],
}


async def main() -> None:
    s = Settings()
    candidates = await get_crop_diseases(s, "Pomidor", "disease_pest")
    print(f"Growz Pomidor candidates: {len(candidates)}")
    review = await review_diagnosis(s, build_auth(s), _REVIEW_INPUT, candidates)
    print("\n=== AI SENIOR-AGRONOM SECOND OPINION ===")
    print("verdict          :", review.verdict)
    print("keep_preparations:", review.keep_preparations)
    print("adjusted_id      :", review.adjusted_growz_disease_id or "(none)")
    print("expert_summary   :", review.expert_summary)
    print("expert_notes     :")
    for n in review.expert_notes:
        print("   -", n)
    await aclose()


if __name__ == "__main__":
    asyncio.run(main())
