"""Exhaustive check of the Uzbek Latin→Cyrillic label transliterator."""
from app.voice.chat.models import UZ
from app.voice.chat.translit import to_uz_cyrillic as t

# Every guided-flow button label / prompt the farmer can see, pinned to its
# expected Cyrillic form. If a UZ label changes, this test must be updated
# deliberately — the mapping is not trusted blind.
EXPECTED = {
    "Nima boʻyicha maslahat kerak?": "Нима бўйича маслаҳат керак?",
    "Kasalliklar va zararkunandalar": "Касалликлар ва зараркунандалар",
    "Begona oʻt": "Бегона ўт",
    "Umumiy savol berish": "Умумий савол бериш",
    "Bu ekin profilingizda bormi?": "Бу экин профилингизда борми?",
    "Ekinlar": "Экинлар",
    "Ha": "Ҳа",
    "Yoʻq": "Йўқ",
    "Muammo oʻsimlikning qaysi qismida?": "Муаммо ўсимликнинг қайси қисмида?",
    "Bargida": "Баргида",
    "Poyasida": "Поясида",
    "Mevasida": "Мевасида",
    "Gulida": "Гулида",
    "Ildizida": "Илдизида",
    "Shoxida": "Шохида",
    "Poʻstlogʻida": "Пўстлоғида",
    "Butun oʻsimlik": "Бутун ўсимлик",
    "Tuproqda": "Тупроқда",
    "Belgilar haqida gapirib bering": "Белгилар ҳақида гапириб беринг",
    "Rasmga oʻtish": "Расмга ўтиш",
    "Ekin haqida bir-ikki savol": "Экин ҳақида бир-икки савол",
    "Belgilarga oʻtish": "Белгиларга ўтиш",
    "Kasallangan qismning rasmini yuboring": "Касалланган қисмнинг расмини юборинг",
    "Rasm tanlash": "Расм танлаш",
    "Tayyor": "Тайёр",
    "Savolingizni bemalol ayting": "Саволингизни бемалол айтинг",
    "Aniqlash jarayonini boshlaymizmi?": "Аниқлаш жараёнини бошлаймизми?",
    "Ha, aniqlaymiz": "Ҳа, аниқлаймиз",
    "Yoʻq, davom etamiz": "Йўқ, давом этамиз",
}


def test_all_button_labels_transliterate_exactly():
    for latin, cyrillic in EXPECTED.items():
        assert t(latin) == cyrillic, latin


def test_every_uz_label_is_pinned():
    """Guardrail: every option/prompt UZ label used as a button is covered
    above, so a new frozen label can't silently ship un-verified Cyrillic."""
    button_keys = {
        "qQueryType", "optDiseasePest", "optWeed", "optGeneral", "qCrop",
        "optCrops", "optCropYes", "optCropNo", "qPlantPart", "partLeaf",
        "partStem", "partFruit", "partFlower", "partRoot", "partBranch",
        "partBark", "partWhole", "partSoil", "qSymptom", "optToPhoto",
        "qCropContext", "optToSymptom", "qPhoto", "optTakePhoto",
        "optDonePhotos", "qGeneral", "qDiagOffer", "optSwitchDiag",
        "optStayGeneral",
    }
    for key in button_keys:
        assert UZ[key] in EXPECTED, f"UZ[{key!r}]={UZ[key]!r} not pinned"


def test_ascii_and_punctuation_pass_through():
    assert t("📷 2024 — «»?!") == "📷 2024 — «»?!"


def test_latin_is_identity_when_no_uzbek_letters():
    assert t("") == ""
    assert t("123-456") == "123-456"
