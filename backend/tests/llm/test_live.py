"""Smoke tests against OpenRouter. Run with `uv run pytest -m live -s`."""

from datetime import date

import pytest

from sazon.agents.riders import FieldName, Step
from sazon.config import get_settings
from sazon.engine.actions import Greet
from sazon.engine.models import (
    Availability,
    CandidateProfile,
    Language,
    MatrixLanguage,
    Platform,
    QuestionTopic,
    Schedule,
)
from sazon.engine.state import ConversationState
from sazon.engine.ui import yes_no_ui
from sazon.llm import ChatTurn, ReplyContext
from sazon.llm.pydantic_ai_impl import PydanticAILLM

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not get_settings().openrouter_api_key.get_secret_value(), reason="OPENROUTER_API_KEY unset"
    ),
]

TODAY = date.today()


def name_stage(country: str) -> ConversationState:
    return ConversationState.model_validate(
        {
            "stage": Step.NAME,
            "awaiting": FieldName.FULL_NAME,
            "consented": True,
            "country": country,
        }
    )


async def test_extracts_several_fields_at_the_name_stage():
    result = await PydanticAILLM().extract(
        [], name_stage("MX"), "Soy Ana, tengo carnet y vivo en Zapopan", today=TODAY
    )
    print(result.model_dump_json(indent=1))
    fields = result.extraction.fields
    assert not result.call.fallback_used
    assert fields["full_name"].value == "Ana"
    assert fields["has_license"].value is True
    assert "zapopan" in str(fields["city"].value).lower()
    assert result.call.provider


async def test_spanglish_keeps_a_spanish_frame():
    result = await PydanticAILLM().extract(
        [],
        name_stage("MX").model_copy(
            update={"stage": Step.AVAILABILITY, "awaiting": FieldName.AVAILABILITY}
        ),
        "Puedo full time pero solo weekends en la mañana",
        today=TODAY,
    )
    print(result.model_dump_json(indent=1))
    assert not result.call.fallback_used
    assert result.extraction.matrix_language == MatrixLanguage.ES
    assert result.extraction.code_switched
    assert result.extraction.explicit_language_request is None


async def test_mexican_greeting_uses_usted_and_discloses_ai():
    greet = Greet(language=Language.ES, country="MX", ui=yes_no_ui(Language.ES))
    result = await PydanticAILLM().respond(greet, ReplyContext.for_action(greet, today=TODAY))
    print(result.model_dump_json(indent=1))
    text = result.reply.text.lower()
    assert not result.call.fallback_used
    assert "ana" in text
    assert any(word in text for word in (" ia ", "inteligencia artificial"))
    assert any(word in text for word in (" su ", " sus ", "usted", " le ", "acepta "))
    assert not any(word in f" {text} " for word in (" tu ", " tus ", " te ", "aceptas"))
    assert len(result.reply.text) <= 300


async def test_are_you_an_ai_next_to_an_answer_is_tagged():
    state = name_stage("ES").model_copy(
        update={
            "stage": Step.AVAILABILITY,
            "awaiting": FieldName.AVAILABILITY,
            "language": Language.EN,
        }
    )
    result = await PydanticAILLM().extract(
        [], state, "Part time, weekends too. Are you an AI?", today=TODAY
    )
    print(result.model_dump_json(indent=1))
    extraction = result.extraction
    assert not result.call.fallback_used
    assert extraction.question_topic == QuestionTopic.AI_IDENTITY
    assert extraction.question
    assert "availability" in extraction.fields


async def test_mx_summary_does_not_flag_a_monday_start_as_a_mismatch():
    monday = date(2026, 10, 26)
    profile = CandidateProfile(
        full_name="Jorge Ramírez Torres",
        has_license=True,
        location_id="mx-gdl-zapopan",
        availability=[Availability.FULL_TIME],
        schedule=[Schedule.MORNING],
        experience_years=2,
        platforms=[Platform.RAPPI, Platform.DIDI_FOOD],
        start_date=monday,
    )
    transcript = [
        ChatTurn(role="assistant", text="¿Cuándo podría empezar?"),
        ChatTurn(role="candidate", text="¿Cuánto se paga a la semana? Podría empezar el lunes"),
        ChatTurn(role="assistant", text="Revise sus datos. ¿Está todo correcto?"),
        ChatTurn(role="candidate", text="Sí, todo correcto"),
    ]
    result = await PydanticAILLM().summarize(
        transcript, profile, language=Language.ES, country="MX", today=date(2026, 10, 19)
    )
    print(result.model_dump_json(indent=1))
    summary = result.summary
    assert not result.call.fallback_used
    assert "Licencia de conducir" in summary.key_fields
    notes = " ".join((*summary.lines, *summary.flags)).lower()
    for word in ("diferencia", "discrepan", "no coincide", "inconsisten", "confirmar la fecha"):
        assert word not in notes
    assert "carnet" not in notes
