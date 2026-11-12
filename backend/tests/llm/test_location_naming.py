"""How Ana asks about and names a location (SAZ-42, from the first real voice call).

"En México, DF" got two of the eight alcaldías read aloud, and "Benito Juárez" got "¿Se
refiere a Sazón CDMX Benito Juárez?" twice. A city alone asks for the zone; a location is
"la sucursal de Benito Juárez", never its internal name.
"""

from datetime import date

import pytest
from test_llm import make_llm

from sazon.agents.riders import FieldName
from sazon.engine.actions import AnswerQuestion, Clarify, ConfirmValue
from sazon.engine.models import Language
from sazon.engine.service_areas import branch_name, load_locations, shared_city
from sazon.engine.spoken import Register
from sazon.engine.validators import Country
from sazon.llm import Channel, ReplyContext
from sazon.llm.fallback_templates import fallback_reply, is_fixed
from sazon.llm.reply_checks import internal_location_name, meta_talk, reply_problem

TODAY = date(2026, 10, 20)
CDMX = (
    "mx-cdmx-ao",
    "mx-cdmx-bju",
    "mx-cdmx-coy",
    "mx-cdmx-cuauh",
    "mx-cdmx-gam",
    "mx-cdmx-izt",
    "mx-cdmx-mh",
    "mx-cdmx-tlp",
)
MADRID = ("es-mad-centro", "es-mad-este", "es-mad-norte", "es-mad-sur")
COUNTRY_OF: dict[Register, tuple[Language, Country]] = {
    "es-ES": (Language.ES, "ES"),
    "es-MX": (Language.ES, "MX"),
    "en": (Language.EN, "ES"),
}


def reply(action: Clarify | ConfirmValue, channel: Channel) -> str:
    ctx = ReplyContext(
        channel=channel, language=action.language, country=action.country, today=TODAY
    )
    return fallback_reply(action, ctx)


def clarify(options: tuple[str, ...], register: Register = "es-MX") -> Clarify:
    language, country = COUNTRY_OF[register]
    return Clarify(
        field=FieldName.CITY,
        options=options,
        city=shared_city(options) if len(options) > 2 else None,  # as the engine sets it
        language=language,
        country=country,
    )


@pytest.mark.parametrize("channel", list(Channel))
def test_the_city_alone_asks_for_the_zone_on_both_channels(channel):
    text = reply(clarify(CDMX), channel)
    assert text == "¿En qué alcaldía o colonia de la Ciudad de México vive?"


@pytest.mark.parametrize(
    ("register", "expected"),
    [
        ("es-ES", "¿En qué barrio de Madrid vives?"),
        ("es-MX", "¿En qué alcaldía o colonia de Madrid vive?"),
        ("en", "Which part of Madrid do you live in?"),
    ],
)
def test_the_zone_question_in_every_register(register: Register, expected):
    assert reply(clarify(MADRID, register), Channel.VOICE) == expected


def test_english_keeps_the_city_name_as_it_is():
    assert reply(clarify(CDMX, "en"), Channel.WEB) == (
        "Which part of Ciudad de México do you live in?"
    )


def test_voice_never_reads_more_than_two_options():
    options = ("mx-cdmx-bju", "mx-mty-sanpedro", "mx-gdl-centro")
    assert reply(clarify(options), Channel.VOICE) == (
        "¿Me da un poco más de detalle, por ejemplo su colonia o alcaldía?"
    )
    web = reply(clarify(options), Channel.WEB)
    assert web == (
        "¿Se refiere a la sucursal de Benito Juárez, San Pedro Garza García o Guadalajara?"
    )


@pytest.mark.parametrize("channel", list(Channel))
def test_two_options_are_named_without_the_brand(channel):
    text = reply(clarify(("es-vlc-centro", "es-vlc-norte"), "es-ES"), channel)
    assert text == "¿Te refieres al local de Valencia Centro o Valencia Benimaclet?"


@pytest.mark.parametrize(
    ("register", "expected"),
    [
        ("es-ES", "¿Te refieres al local de Zapopan?"),
        ("es-MX", "¿Se refiere a la sucursal de Zapopan?"),
        ("en", "Do you mean our Zapopan location?"),
    ],
)
def test_a_loose_match_is_asked_about_by_its_branch(register: Register, expected):
    language, country = COUNTRY_OF[register]
    action = ConfirmValue(
        field=FieldName.CITY,
        value="mx-gdl-zapopan",
        location_id="mx-gdl-zapopan",
        language=language,
        country=country,
    )
    assert reply(action, Channel.VOICE) == expected


def test_a_place_outside_the_areas_is_still_read_back_as_named():
    action = ConfirmValue(field=FieldName.CITY, value="Oviedo", language=Language.ES, country="ES")
    assert reply(action, Channel.WEB) == "Para asegurarme: ¿vives ahora en Oviedo?"


def test_branch_names_never_carry_the_brand():
    for location_id in load_locations():
        assert not branch_name(location_id).startswith("Sazón")
    assert branch_name("mx-cdmx-bju") == "Benito Juárez"
    assert branch_name("es-mad-centro") == "Madrid Centro"
    assert branch_name("unknown") == "unknown"


def test_shared_city():
    assert shared_city(CDMX) == "Ciudad de México"
    assert shared_city(("mx-cdmx-bju", "mx-mty-sanpedro")) is None
    assert shared_city(()) is None


@pytest.mark.parametrize(
    "text",
    [
        "¿Se refiere a Sazón CDMX Benito Juárez?",
        "¿Se refiere a sazon cdmx benito juarez?",
        "¿Se refiere a Sazón Madrid Centro o a Sazón Madrid Norte?",
    ],
)
def test_a_reply_with_an_internal_name_is_rejected(text):
    action = clarify(("mx-cdmx-bju", "mx-cdmx-coy"))
    problem = reply_problem(text, Language.ES, country="MX", action=action, reference=text)
    assert problem is not None
    assert "Sazón" in problem


def test_the_branch_name_passes():
    action = clarify(("mx-cdmx-bju", "mx-cdmx-coy"))
    text = "¿Se refiere a la sucursal de Benito Juárez o a la de Coyoacán?"
    assert reply_problem(text, Language.ES, country="MX", action=action, reference=text) is None
    assert internal_location_name(text) is None


def test_an_faq_answer_may_quote_the_documents():
    action = AnswerQuestion(
        question="¿Dónde está?", follow_up=None, language=Language.ES, country="ES"
    )
    text = "Sazón Madrid Centro está en Calle de Fuencarral 112."
    assert reply_problem(text, Language.ES, country="ES", action=action, reference=text) is None


# --- the zone question is sent as written (SAZ-49) ---
# Call 90cd0d40: phrased by the model, Ana said "Con más de dos opciones en Madrid, pregunto por
# el barrio: ¿en qué barrio de Madrid vives?", her own instruction read aloud.

LEAKED = (
    "Con más de dos opciones en Madrid, pregunto por el barrio: ¿en qué barrio de Madrid vives?"
)


@pytest.mark.parametrize("register", ["es-ES", "es-MX", "en"])
def test_the_zone_question_is_a_fixed_reply(register: Register):
    assert is_fixed(clarify(MADRID, register))
    assert not is_fixed(clarify(("mx-cdmx-bju", "mx-cdmx-coy"), register))


@pytest.mark.parametrize("channel", list(Channel))
async def test_the_zone_question_never_reaches_the_model(channel: Channel):
    calls: list[object] = []

    def handler(request: object) -> object:
        calls.append(request)
        raise AssertionError("the zone question must not call the model")

    action = clarify(MADRID, "es-ES")
    ctx = ReplyContext(channel=channel, language=Language.ES, country="ES", today=TODAY)
    llm = make_llm(handler)
    result = await llm.respond(action, ctx)
    streamed: list[str] = []
    streamed_result = await llm.respond_stream(action, ctx, streamed.append)

    assert result.reply.text == "¿En qué barrio de Madrid vives?"
    assert streamed == [result.reply.text]
    assert streamed_result.reply.text == result.reply.text
    assert not calls


@pytest.mark.parametrize(
    ("text", "language", "country", "found"),
    [
        (LEAKED, Language.ES, "ES", "opciones"),
        ("Pregunto por la colonia: ¿en qué colonia vive?", Language.ES, "MX", "pregunto por"),
        ("With more than two options, I ask for the area. Where do you live?", Language.EN, "ES",
         "options"),
        ("I ask which part: where in Madrid do you live?", Language.EN, "ES", "i ask"),
        ("Según mis instrucciones, ¿en qué barrio vives?", Language.ES, "ES", "instrucciones"),
    ],
)  # fmt: skip
def test_a_reply_that_talks_about_its_options_is_rejected(text, language, country, found):
    action = clarify(("mx-cdmx-bju", "mx-cdmx-coy"))
    reference = "¿Se refiere a la sucursal de Benito Juárez o a la de Coyoacán?"
    problem = reply_problem(text, language, country=country, action=action, reference=reference)
    assert problem is not None
    assert found in problem
    assert meta_talk(text) == found


@pytest.mark.parametrize(
    "text",
    [
        "May I ask your full name?",
        "Can I ask where you live?",
        "¿En qué barrio de Madrid vives?",
        "¿En qué alcaldía o colonia de la Ciudad de México vive?",
    ],
)
def test_plain_questions_are_not_meta_talk(text):
    assert meta_talk(text) is None


def test_a_word_the_reference_uses_is_allowed():
    reference = "Tenemos varias opciones de horario. ¿Cuál le acomoda?"
    assert meta_talk("Hay varias opciones de horario. ¿Cuál le acomoda?", reference) is None
