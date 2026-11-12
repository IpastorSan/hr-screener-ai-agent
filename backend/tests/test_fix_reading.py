"""The service's reading of a correction before the engine sees it: the field named (alone at the
summary), "¿por qué pone Cuauhtémoc?", and "La zona es Roma" with no city extracted."""

import pytest

from sazon.agents.riders import FieldName
from sazon.engine.models import (
    CandidateProfile,
    Intent,
    Language,
    MatrixLanguage,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.state import (
    Asking,
    ConversationState,
    InterviewFix,
    Summary,
)
from sazon.service import (
    fix_reading,
    with_named_place,
    with_place_reading,
    without_echoed_credential,
)

AT_SUMMARY = ConversationState(
    phase=Summary(),
    country="MX",
    consented=True,
    profile=CandidateProfile(location_id="mx-cdmx-cuauh", place_said="Roma"),
)
MID_FLOW = AT_SUMMARY.at(Asking(field=FieldName.AVAILABILITY))


@pytest.mark.parametrize(
    ("state", "text", "fix"),
    [
        (AT_SUMMARY, "la zona", FieldName.CITY),
        (MID_FLOW, "la zona", None),
        (MID_FLOW, "quiero cambiar la zona", FieldName.CITY),
        (AT_SUMMARY, "¿Por qué pone Cuauhtémoc?", FieldName.CITY),
        (AT_SUMMARY, "¿Por qué me sale ese local?", FieldName.CITY),
        (AT_SUMMARY, "¿Por qué pone Polanco?", None),
        (AT_SUMMARY, "quiero cambiar la hora de la entrevista", InterviewFix.TIME),
        (AT_SUMMARY, "cambiar la modalidad", InterviewFix.MODE),
        (AT_SUMMARY, "Sí, todo correcto", None),
    ],
)
def test_fix_reading(state: ConversationState, text: str, fix: object):
    assert fix_reading(state, text) == fix


def extraction(**fields: object) -> TurnExtraction:
    return TurnExtraction.model_validate(
        {
            "intent": Intent.UNCLEAR,
            "fields": {k: {"value": v, "confidence": 0.9} for k, v in fields.items()},
            "language": Language.ES,
            "matrix_language": MatrixLanguage.ES,
            "english_ratio": 0.0,
            "code_switched": False,
            "sentiment": Sentiment.NEUTRAL,
        }
    )


def test_a_named_place_with_no_city_extracted_is_the_city_answer():
    read = with_named_place(AT_SUMMARY, extraction(), "La zona es Roma…")
    assert read.intent == Intent.CORRECTION
    assert read.fields["city"].value == "roma"


def test_the_extractor_s_city_stays():
    read = with_named_place(AT_SUMMARY, extraction(city="Roma Sur"), "La zona es Roma Sur")
    assert read.fields["city"].value == "Roma Sur"


ASKING_CITY = AT_SUMMARY.at(Asking(field=FieldName.CITY))


@pytest.mark.parametrize(
    ("text", "city"),
    [("Polanco", "Polanco"), ("Vivo en Polanco", "Vivo en Polanco"), ("DF", None), ("Sí", None)],
)
def test_a_place_that_names_one_location_is_the_city_answer(text: str, city: str | None):
    """Live run, 2026-10-22: "Polanco" after "quiero cambiar la zona" came back empty."""
    read = with_place_reading(ASKING_CITY, extraction(), text)
    assert (read.fields["city"].value if "city" in read.fields else None) == city


def test_a_place_is_read_only_when_the_city_is_asked():
    assert with_place_reading(AT_SUMMARY, extraction(), "Polanco").fields == {}


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("La licencia está mal", False),
        ("Quiero cambiar lo de la licencia", False),
        ("El carnet está mal, lo tengo caducado", True),
        ("No, no tengo licencia, está mal", True),
    ],
)
def test_a_license_request_is_no_license_answer(text: str, kept: bool):
    """Live run, 2026-10-22: "La licencia está mal" was read as a no at 0.9 and closed."""
    read = without_echoed_credential(FieldName.HAS_LICENSE, extraction(has_license=False), text)
    assert ("has_license" in read.fields) is kept
