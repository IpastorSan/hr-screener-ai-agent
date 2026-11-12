"""What the extractor is asked for, generated from the agent spec: one typed attribute per field
with its options, a prompt line per field, and its output read back as the engine reads it."""

from typing import Any

from riders import SPEC
from sazon.agents.riders import FieldName, Step
from sazon.engine.models import FieldCandidate, PhoneInput, TurnExtraction
from sazon.engine.spec import AgentSpec, Channel, FieldSpec, FieldType
from sazon.llm.extraction import output_model, parse_output
from sazon.llm.prompting import fill, prompt

FRAME = {
    "intent": "answer",
    "language": "es",
    "matrix_language": "es",
    "english_ratio": 0,
    "code_switched": False,
    "sentiment": "neutral",
}


def _value_schema(spec: AgentSpec, field: str) -> dict[str, Any]:
    schema = output_model(spec).model_json_schema()
    return schema["$defs"][f"{field}_value"]["properties"]["value"]


def test_every_field_is_an_optional_attribute_of_the_output():
    schema = output_model(SPEC).model_json_schema()
    fields = schema["$defs"]["Fields"]["properties"]
    assert list(fields) == list(SPEC.ids)
    assert not schema["$defs"]["Fields"].get("required")


def test_the_options_are_an_enum_and_the_types_follow_the_field():
    platforms = _value_schema(SPEC, FieldName.PLATFORMS)
    listed = platforms["anyOf"][0]["items"]["anyOf"][0]["enum"]
    assert listed == [*SPEC.field(FieldName.PLATFORMS).options, "other"]
    schedule = _value_schema(SPEC, FieldName.SCHEDULE)
    assert schedule["anyOf"][0]["items"]["anyOf"][0]["enum"] == [
        "morning",
        "afternoon",
        "evening",
        "flexible",
    ]
    license_types = {kind.get("type") for kind in _value_schema(SPEC, "has_license")["anyOf"]}
    assert license_types == {"boolean", "string"}
    assert _value_schema(SPEC, FieldName.FULL_NAME) == {"title": "Value", "type": "string"}


def test_the_output_reads_back_as_the_fields_stated_by_id():
    extraction = parse_output(
        {
            **FRAME,
            "fields": {
                "has_license": {"value": True, "confidence": 0.9},
                "experience_years": {"value": 2, "confidence": 0.8},
                "phone": {"value": {"country": "ES", "number": "612345678"}, "confidence": 1},
                "schedule": {"value": "morning", "confidence": 0.7},
                "city": None,
                "location": {"value": "Triana", "confidence": 1},
            },
        }
    )
    assert type(extraction) is TurnExtraction
    assert extraction.fields == {
        "has_license": FieldCandidate(value=True, confidence=0.9),
        "experience_years": FieldCandidate(value=2.0, confidence=0.8),
        "phone": FieldCandidate(value=PhoneInput(country="ES", number="612345678"), confidence=1),
        "schedule": FieldCandidate(value="morning", confidence=0.7),
    }


def test_the_prompt_has_a_line_per_field_in_order():
    text = prompt("extract.md", SPEC)
    assert "{fields}" not in text
    positions = [text.index(f"\n- `{field.id}`: {field.extract}\n") for field in SPEC.fields]
    assert positions == sorted(positions)


def test_another_spec_gets_its_own_fields():
    school = AgentSpec(
        agent="school",
        fields=(
            FieldSpec(
                id="email",
                type=FieldType.CONTACT,
                channel=Channel.EMAIL,
                funnel_step=Step.CONTACT,
                extract="lowercased.",
            ),
            FieldSpec(
                id="workload",
                type=FieldType.ENUM,
                funnel_step=Step.AVAILABILITY,
                options=("completa", "media"),
                extract="the workload they want.",
            ),
        ),
    )
    assert list(output_model(school).model_json_schema()["$defs"]["Fields"]["properties"]) == [
        "email",
        "workload",
    ]
    assert _value_schema(school, "workload")["anyOf"][0]["enum"] == ["completa", "media"]
    assert "- `workload`: the workload they want." in fill(school, "{fields}")
    fields = {"workload": {"value": "media", "confidence": 1}, "city": {"value": "x"}}
    assert parse_output({**FRAME, "fields": fields}, school).fields == {
        "workload": FieldCandidate(value="media", confidence=1)
    }
