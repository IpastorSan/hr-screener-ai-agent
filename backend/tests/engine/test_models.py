from datetime import date

import pytest
from pydantic import ValidationError

from sazon.engine.models import (
    Availability,
    CandidateProfile,
    FieldCandidate,
    Intent,
    Language,
    MatrixLanguage,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.service_areas import get_location, match_city


def test_turn_extraction_json_schema_builds():
    schema = TurnExtraction.model_json_schema()

    assert schema["type"] == "object"
    assert {"intent", "fields", "matrix_language", "english_ratio"} <= schema["properties"].keys()
    assert schema["properties"]["intent"]["$ref"]


def test_turn_extraction_parses_llm_output():
    extraction = TurnExtraction.model_validate_json(
        """{
            "intent": "answer",
            "fields": {
                "availability": {"value": ["full time", "weekends"], "confidence": 0.9},
                "has_license": {"value": true, "confidence": 0.95},
                "experience_years": {"value": 0.5, "confidence": 0.8}
            },
            "language": "es",
            "matrix_language": "es",
            "english_ratio": 0.3,
            "explicit_language_request": null,
            "code_switched": true,
            "sentiment": "neutral",
            "question": null
        }"""
    )

    assert extraction.intent is Intent.ANSWER
    assert extraction.matrix_language is MatrixLanguage.ES
    assert extraction.fields["has_license"].value is True
    assert extraction.fields["experience_years"].value == 0.5
    assert extraction.sentiment is Sentiment.NEUTRAL


def test_english_ratio_is_bounded():
    with pytest.raises(ValidationError):
        TurnExtraction(
            intent=Intent.ANSWER,
            language=Language.ES,
            matrix_language=MatrixLanguage.ES,
            english_ratio=1.5,
            code_switched=False,
            sentiment=Sentiment.NEUTRAL,
        )


def test_field_candidate_confidence_is_bounded():
    with pytest.raises(ValidationError):
        FieldCandidate(value="x", confidence=2)


def test_candidate_profile_defaults_to_empty():
    profile = CandidateProfile()

    assert profile.model_dump(exclude_none=True) == {}
    profile = profile.model_copy(
        update={"availability": [Availability.WEEKENDS], "start_date": date(2026, 11, 2)}
    )
    assert CandidateProfile.model_validate(profile.model_dump(mode="json")) == profile


def test_service_area_lookup():
    location = get_location("mx-gdl-zapopan")

    assert location is not None
    assert location.country == "MX"
    assert get_location("nowhere") is None
    assert match_city("Zapopan")[0] == (location, 100.0)


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        ([], []),
        (["¿Cuánto se paga?"], []),
        (["¿Cuánto se paga?", " ", "¿Cuánto se paga?"], []),
        (["¿Pago?", "¿Casco?"], ["¿Pago?", "¿Casco?"]),
        (["a?", "b?", "c?", "d?"], ["a?", "b?", "c?"]),
    ],
)
def test_sub_questions_are_distinct_parts_and_at_most_three(parts: list[str], expected: list[str]):
    extraction = TurnExtraction(
        intent=Intent.QUESTION,
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
        question="?",
        sub_questions=parts,
    )
    assert extraction.sub_questions == expected
