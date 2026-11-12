from datetime import date

import pytest

from sazon.agents.riders import FieldName
from sazon.engine import (
    AskField,
    CheckDuplicate,
    ConversationContext,
    ConversationState,
    CountrySource,
    start,
    step,
)
from sazon.engine.country import initial_country, named_country, posting_country
from sazon.engine.models import Intent, Language, MatrixLanguage, Sentiment, TurnExtraction
from sazon.engine.spec import agent, use
from sazon.engine.ui import phone_ui
from sazon.engine.validators import Country, ReasonCode

TODAY = date(2026, 10, 19)


def answer(**fields: object) -> TurnExtraction:
    return TurnExtraction.model_validate(
        {
            "intent": Intent.CONFIRM if not fields else Intent.ANSWER,
            "fields": {name: {"value": value, "confidence": 0.9} for name, value in fields.items()},
            "language": Language.ES,
            "matrix_language": MatrixLanguage.ES,
            "english_ratio": 0.0,
            "code_switched": False,
            "sentiment": Sentiment.NEUTRAL,
        }
    )


def at_phone(context: ConversationContext | None = None) -> ConversationState:
    state, _ = start(context)
    state, _ = step(state, answer(), today=TODAY)
    state, action = step(state, answer(full_name="Jorge Ramírez"), today=TODAY)
    assert isinstance(action, AskField)
    assert action.field == FieldName.PHONE
    return state


def test_default_country_is_spain():
    state, action = start()

    assert (state.country, state.country_source) == ("ES", CountrySource.DEFAULT)
    assert action.country == "ES"


def test_posting_context_wins_over_timezone():
    context = ConversationContext(location_id="es-mad-centro", timezone="America/Mexico_City")

    state, action = start(context)

    assert (state.country, state.country_source) == ("ES", CountrySource.POSTING)
    assert action.country == "ES"


def test_timezone_wins_over_locale():
    context = ConversationContext(timezone="America/Monterrey", locale="es-ES")

    state, _ = start(context)

    assert (state.country, state.country_source) == ("MX", CountrySource.TIMEZONE)


def test_locale_is_used_without_timezone():
    state, _ = start(ConversationContext(locale="es-MX"))

    assert (state.country, state.country_source) == ("MX", CountrySource.LOCALE)


def test_mexican_number_without_prefix_resolves_to_mexico():
    state = at_phone()

    state, action = step(state, answer(phone="33 1234 5678"), today=TODAY)

    assert state.profile.phone == "+523312345678"
    assert (state.country, state.country_source) == ("MX", CountrySource.PHONE)
    assert action.country == "MX"


def test_spanish_number_with_mexican_timezone_hint():
    state = at_phone(ConversationContext(timezone="America/Mexico_City"))
    assert state.country == "MX"

    state, _ = step(state, answer(phone="612 345 678"), today=TODAY)

    assert state.profile.phone == "+34612345678"
    assert (state.country, state.country_source) == ("ES", CountrySource.PHONE)


def test_free_text_phone_does_not_override_the_posting():
    state = at_phone(ConversationContext(country="ES"))

    state, _ = step(state, answer(phone="+52 55 1234 5678"), today=TODAY)

    assert state.profile.phone == "+525512345678"
    assert (state.country, state.country_source) == ("ES", CountrySource.POSTING)


def test_location_overrides_a_locale_guess():
    state = at_phone(ConversationContext(locale="es-MX"))
    state, _ = step(state, answer(phone="+34 612 345 678", email="j@x.com"), today=TODAY)
    state, _ = step(state, answer(has_license=True, city="Coyoacán"), today=TODAY)

    assert state.profile.location_id == "mx-cdmx-coy"
    assert (state.country, state.country_source) == ("MX", CountrySource.LOCATION)
    assert state.profile.phone == "+34612345678"


def test_phone_picker_mexico_with_ten_digits():
    state = at_phone()

    state, action = step(
        state, answer(phone={"country": "MX", "number": "5512345678"}), today=TODAY
    )

    assert state.profile.phone == "+525512345678"
    assert (state.country, state.country_source) == ("MX", CountrySource.PHONE_PICKER)
    assert isinstance(action, CheckDuplicate)
    assert action.phone == "+525512345678"


def test_phone_picker_spain_rejects_a_mexican_number():
    state = at_phone()

    state, action = step(
        state, answer(phone={"country": "ES", "number": "5512345678"}), today=TODAY
    )

    assert state.profile.phone is None
    assert isinstance(action, AskField)
    assert action.field == FieldName.PHONE
    assert action.hint == ReasonCode.PHONE_INVALID


def test_phone_picker_overrides_timezone_but_not_posting():
    from_timezone = at_phone(ConversationContext(timezone="Europe/Madrid"))
    from_posting = at_phone(ConversationContext(country="ES", timezone="Europe/Madrid"))
    picked = answer(phone={"country": "MX", "number": "5512345678"}, email="j@x.com")

    from_timezone, action = step(from_timezone, picked, today=TODAY)
    assert (from_timezone.country, from_timezone.country_source) == (
        "MX",
        CountrySource.PHONE_PICKER,
    )
    assert isinstance(action, CheckDuplicate)

    from_posting, _ = step(from_posting, picked, today=TODAY)
    assert (from_posting.country, from_posting.country_source) == ("ES", CountrySource.POSTING)
    assert from_posting.profile.phone == "+525512345678"


@pytest.mark.parametrize(
    ("question", "own", "named"),
    [
        ("¿Cuánto se paga a los repartidores en México?", "ES", "MX"),
        ("¿Y en México?", "ES", "MX"),
        ("¿Y en Mexico también?", "ES", "MX"),
        ("How much is the pay in Mexico?", "ES", "MX"),
        ("¿Cuánto se paga en España?", "MX", "ES"),
        ("¿Y para España qué contrato es?", "MX", "ES"),
        ("What about in Spain?", "MX", "ES"),
        # its own country, both, or where the candidate comes from: no other country
        ("¿Cuánto se paga en España?", "ES", None),
        ("¿Cuánto se paga en México?", "MX", None),
        ("¿Se paga igual en España que en México?", "ES", None),
        ("Soy de México, ¿puedo trabajar aquí?", "ES", None),
        ("¿Cuánto se paga?", "ES", None),
        ("¿Hay local en Nuevo México?", "ES", None),
    ],
)
def test_a_question_that_names_the_other_country(question: str, own: Country, named: object):
    assert named_country(question, own) == named


def test_an_agent_hiring_in_one_country_never_leaves_it():
    """A school in Madrid: a Mexican browser or phone keeps the conversation in Spain, and the
    phone picker offers only Spain's prefix."""
    with use(agent().model_copy(update={"countries": ("ES",)})):
        context = ConversationContext(timezone="America/Mexico_City", locale="es-MX")
        assert initial_country(context) == ("ES", CountrySource.DEFAULT)
        assert posting_country(ConversationContext(country="MX")) is None
        state, _ = start(context)
        state, _ = step(state, answer(), today=TODAY)
        state, _ = step(state, answer(full_name="Lucía Fernández"), today=TODAY)
        state, _ = step(state, answer(phone="+52 55 1234 5678"), today=TODAY)
        assert state.country == "ES"
        assert [dial.code for dial in phone_ui("ES").countries] == ["ES"]
