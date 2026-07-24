from app.config import Settings
from app.voice.pipeline.diagnosis import DiagnosisResult, Differential, diagnose
from app.voice.pipeline.tools import PhotoAttachment

# A valid structured verdict the mocked client "returns" as JSON.
_RESULT = DiagnosisResult(
    is_plant=True,
    likely_disease="Fitoftoroz",
    confidence="high",
    differentials=[Differential(name="Alternarioz", why="dogʻlar shakli boshqacha")],
    immediate_treatment=["mis kuporosi bilan ishlov bering"],
    prevention=["nam sharoitdan saqlaning"],
    spoken_summary="Bu fitoftoroz. Mis kuporosi bilan dorilang.",
    language="uz",
)


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _Models:
    """Records the generate_content call so tests can inspect model + parts."""

    def __init__(self, text: str, capture: dict) -> None:
        self._text = text
        self._capture = capture

    async def generate_content(self, *, model, contents, config):
        self._capture["model"] = model
        self._capture["contents"] = contents
        self._capture["config"] = config
        return _Resp(self._text)


class _Auth:
    def __init__(self, models: _Models) -> None:
        self._models = models

    def genai_client(self):
        aio = type("Aio", (), {"models": self._models})()
        return type("Client", (), {"aio": aio})()


def _photos() -> list[PhotoAttachment]:
    return [
        PhotoAttachment(photo_id="p1", data=b"\x89PNG", mime="image/png"),
        PhotoAttachment(photo_id="p2", data=b"\xff\xd8", mime="image/jpeg"),
    ]


async def test_diagnose_parses_result():
    capture: dict = {}
    auth = _Auth(_Models(_RESULT.model_dump_json(), capture))
    settings = Settings()
    summary = {"crop": "pomidor", "symptoms": "barglarda dogʻlar", "farmer_language": "uz"}
    result = await diagnose(settings, auth, summary, _photos())
    assert isinstance(result, DiagnosisResult)
    assert result.likely_disease == "Fitoftoroz"
    assert result.confidence == "high"
    assert result.spoken_summary.startswith("Bu fitoftoroz")


async def test_diagnose_includes_summary_and_photo_parts():
    capture: dict = {}
    auth = _Auth(_Models(_RESULT.model_dump_json(), capture))
    settings = Settings()
    summary = {"crop": "pomidor", "symptoms": "dogʻlar", "farmer_language": "uz"}
    await diagnose(settings, auth, summary, _photos())
    contents = capture["contents"]
    assert len(contents) == 1
    parts = contents[0].parts
    # One text part (the summary JSON) + one part per photo.
    assert len(parts) == 3
    assert parts[0].text is not None
    assert parts[1].inline_data is not None and parts[2].inline_data is not None


async def test_diagnose_uses_configured_model():
    capture: dict = {}
    auth = _Auth(_Models(_RESULT.model_dump_json(), capture))
    settings = Settings(diagnosis_model="gemini-3.1-pro-preview")
    await diagnose(settings, auth, {"crop": "x", "symptoms": "y", "farmer_language": "uz"}, [])
    assert capture["model"] == "gemini-3.1-pro-preview"


async def test_diagnose_falls_back_to_gemini_model_when_blank():
    capture: dict = {}
    auth = _Auth(_Models(_RESULT.model_dump_json(), capture))
    settings = Settings(diagnosis_model="")
    await diagnose(settings, auth, {"crop": "x", "symptoms": "y", "farmer_language": "uz"}, [])
    assert capture["model"] == settings.gemini_model


# ---- §5.4 per-image analyses block ------------------------------------------


async def test_diagnose_inserts_per_image_analysis_part_with_photo_index():
    capture: dict = {}
    auth = _Auth(_Models(_RESULT.model_dump_json(), capture))
    analyses = [
        {"organ": "leaf", "confidence": "high"},
        None,  # second photo's analysis failed -> omitted from the block
    ]
    await diagnose(
        Settings(), auth,
        {"crop": "pomidor", "farmer_language": "uz"}, _photos(),
        per_image_analyses=analyses,
    )
    parts = capture["contents"][0].parts
    # summary + ONE analysis JSON part + 2 photo parts.
    assert len(parts) == 4
    import json

    block = json.loads(parts[1].text)
    assert block == {"per_image_analysis": [{"photo_index": 0, "organ": "leaf",
                                             "confidence": "high"}]}
    assert parts[2].inline_data is not None and parts[3].inline_data is not None


async def test_diagnose_no_analysis_part_when_all_none():
    capture: dict = {}
    auth = _Auth(_Models(_RESULT.model_dump_json(), capture))
    await diagnose(
        Settings(), auth, {"crop": "pomidor", "farmer_language": "uz"}, _photos(),
        per_image_analyses=[None, None],
    )
    parts = capture["contents"][0].parts
    # Byte-identical to the no-analyses path: summary + 2 photo parts only.
    assert len(parts) == 3
    assert parts[0].text is not None
    assert parts[1].inline_data is not None and parts[2].inline_data is not None


async def test_diagnose_none_analyses_is_identical_to_default():
    capture: dict = {}
    auth = _Auth(_Models(_RESULT.model_dump_json(), capture))
    await diagnose(
        Settings(), auth, {"crop": "pomidor", "farmer_language": "uz"}, _photos(),
        per_image_analyses=None,
    )
    assert len(capture["contents"][0].parts) == 3


def test_diagnosis_prompt_documents_the_per_image_block():
    from app.voice.pipeline.diagnosis import DIAGNOSIS_SYSTEM_PROMPT

    assert "per_image_analysis" in DIAGNOSIS_SYSTEM_PROMPT
