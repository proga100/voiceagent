from app.voice.pipeline.tools import (
    TARGET_PARTS,
    PhotoAttachment,
    build_case_tools,
)


def test_two_declarations_in_order():
    tools = build_case_tools()
    assert len(tools) == 1
    names = [d.name for d in tools[0].function_declarations]
    assert names == ["request_photo", "finalize_case"]


def test_request_photo_required_and_enum():
    decls = build_case_tools()[0].function_declarations
    rp = next(d for d in decls if d.name == "request_photo")
    assert set(rp.parameters.required) == {"reason", "target_part"}
    assert rp.parameters.properties["target_part"].enum == TARGET_PARTS


def test_finalize_case_summary_shape():
    decls = build_case_tools()[0].function_declarations
    fc = next(d for d in decls if d.name == "finalize_case")
    assert fc.parameters.required == ["summary"]
    summary = fc.parameters.properties["summary"]
    assert set(summary.required) == {"crop", "symptoms", "farmer_language"}
    # Every interview field the prompt collects is exposed as a STRING property.
    for field in ("growth_stage", "onset_and_spread", "affected_scale",
                  "treatments_tried", "farmer_language"):
        assert field in summary.properties


def test_photo_attachment_defaults():
    p = PhotoAttachment(photo_id="x", data=b"1", mime="image/png")
    assert p.target_part is None
