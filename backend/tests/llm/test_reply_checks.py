"""Reply checks: length, sentences, question last, register, one language, read-back (SAZ-20)."""

import json
from datetime import date
from typing import Any

import httpx2
import pytest
from test_llm import COUNTRY_OF, completion, ctx_for, make_llm, sample_actions

from sazon.agents.riders import FieldName
from sazon.engine.actions import (
    Action,
    AnswerQuestion,
    AskField,
    AskMode,
    Clarify,
    ConfirmBooking,
    ConfirmSummary,
    ConfirmValue,
    DeEscalate,
    End,
    EndReason,
    Greet,
    OfferPause,
    OfferSlots,
)
from sazon.engine.models import (
    Availability,
    CandidateProfile,
    InterviewMode,
    Language,
    Schedule,
)
from sazon.engine.slots import Booked, Miss, OfferReason, Slot
from sazon.engine.spec import agent, use
from sazon.engine.spoken import Register
from sazon.engine.text import fold
from sazon.engine.texts import REGISTERS
from sazon.engine.validators import Country, ReasonCode
from sazon.llm import Channel, ReplyContext
from sazon.llm.fallback_templates import (
    UNKNOWN_ANSWER,
    fallback_reply,
    is_fixed,
    pack,
)
from sazon.llm.reply_checks import (
    RECRUITER_TERMS,
    count_sentences,
    interviewer_terms,
    reply_problem,
)

TODAY = date(2026, 10, 19)


def problem(text: str, action: Action, ctx: ReplyContext | None = None) -> str | None:
    ctx = ctx or ReplyContext.for_action(action, today=TODAY)
    return reply_problem(
        text,
        ctx.language,
        country=ctx.country,
        action=action,
        reference=fallback_reply(action, ctx),
        voice=ctx.channel == Channel.VOICE,
    )


def ask(field: FieldName, language: Language = Language.ES, country: Country = "ES") -> AskField:
    return AskField(field=field, language=language, country=country)


def summary(language: Language = Language.ES, country: Country = "ES") -> ConfirmSummary:
    profile = CandidateProfile(
        full_name="Diego Morales Vega",
        phone="+34612345605",
        location_id="es-sev-centro",
        availability=[Availability.FULL_TIME],
        schedule=[Schedule.EVENING],
        start_date=date(2026, 11, 2),
    )
    return ConfirmSummary(profile=profile, language=language, country=country)


@pytest.mark.parametrize("register", REGISTERS)
@pytest.mark.parametrize("channel", list(Channel))
def test_every_template_passes_its_own_checks(register: Register, channel: Channel):
    """A reply phrased like the template is never thrown away."""
    language, country = COUNTRY_OF[register]
    ctx = ctx_for(register, channel)
    for action in sample_actions(language, country):
        text = fallback_reply(action, ctx)
        assert problem(text, action, ctx) is None, (type(action).__name__, text)


@pytest.mark.parametrize("register", REGISTERS)
@pytest.mark.parametrize("channel", list(Channel))
def test_every_template_passes_its_own_checks_for_a_frustrated_candidate(
    register: Register, channel: Channel
):
    """`simplify` holds the template to one sentence too (QA batch 4: the pause offer and the
    abuse close ran to two), however the engine got there."""
    language, country = COUNTRY_OF[register]
    ctx = ctx_for(register, channel)
    ends = [End(reason=reason, language=language, country=country) for reason in EndReason]
    cancelled = End(
        reason=EndReason.ABUSE, booking_cancelled=True, language=language, country=country
    )
    for action in [*sample_actions(language, country), *ends, cancelled]:
        simplified = action.model_copy(update={"simplify": True})
        text = fallback_reply(simplified, ctx)
        assert problem(text, simplified, ctx) is None, (type(action).__name__, text)


@pytest.mark.parametrize("register", REGISTERS)
def test_the_pause_offer_and_the_abuse_close_are_one_sentence(register: Register):
    language, country = COUNTRY_OF[register]
    ctx = ctx_for(register, Channel.WEB)
    for action in (
        OfferPause(language=language, country=country, simplify=True),
        End(reason=EndReason.ABUSE, language=language, country=country),
        End(reason=EndReason.ABUSE, booking_cancelled=True, language=language, country=country),
    ):
        assert count_sentences(fallback_reply(action, ctx)) == 1


@pytest.mark.parametrize(
    ("text", "action"),
    [
        (
            "¿Cuánta experiencia tienes haciendo reparto, Diego? Puede ser de días, meses o años.",
            ask(FieldName.EXPERIENCE_YEARS),
        ),
        (
            "What's your mobile number, Daniel? I'll add it to your application.",
            ask(FieldName.PHONE, Language.EN),
        ),
        (
            "Luis, ¿se refiere a turno de tarde o de noche? Dígame cuál le acomoda mejor.",
            Clarify(
                field=FieldName.SCHEDULE,
                options=("afternoon", "evening"),
                language=Language.ES,
                country="MX",
            ),
        ),
    ],
)
def test_the_question_must_be_the_last_thing(text: str, action: Action):
    assert problem(text, action) == "the question is not the last thing"


def test_too_many_sentences_or_characters_are_rejected():
    action = ask(FieldName.SCHEDULE)
    three = "Genial, gracias por todo, Lucía. Ya casi estamos. ¿Qué horario prefieres?"
    assert problem(three, action) == "3 sentences (at most 2)"
    long = "Genial, Lucía, " + "x" * 300 + ". ¿Qué horario prefieres?"
    assert problem(long, action) == "over 300 characters"


@pytest.mark.parametrize(
    ("text", "action"),
    [
        # Journey gates.lic_es_unsure_no_reasked: right after a valid email (QA batch 4).
        (
            "Ese dato no era el que necesito, así que te lo pregunto de nuevo: "
            "¿tienes carnet de conducir en vigor?",
            ask(FieldName.HAS_LICENSE),
        ),
        (
            "Ese dato no era el que necesitaba. ¿Tienes carnet de conducir en vigor?",
            ask(FieldName.HAS_LICENSE),
        ),
        (
            "Disculpe, no le entendí. ¿Tiene licencia de manejo vigente?",
            ask(FieldName.HAS_LICENSE, country="MX"),
        ),
        (
            "That wasn't what I needed. Do you have a valid driving licence?",
            ask(FieldName.HAS_LICENSE, Language.EN),
        ),
        (
            "Te lo pregunto otra vez: ¿prefieres en persona o por videollamada?",
            AskMode(
                options=(InterviewMode.ONSITE, InterviewMode.VIDEO),
                language=Language.ES,
                country="ES",
            ),
        ),
    ],
)
def test_a_first_question_never_treats_the_last_answer_as_wrong(text: str, action: Action):
    found = problem(text, action)
    assert found is not None
    assert found.startswith("treats the last answer as wrong")


@pytest.mark.parametrize(
    "action",
    [
        AskField(field=FieldName.HAS_LICENSE, retry=1, language=Language.ES, country="ES"),
        AskField(field=FieldName.EMAIL, again=True, language=Language.ES, country="ES"),
        AskField(
            field=FieldName.HAS_LICENSE,
            retry=1,
            hint=ReasonCode.LICENSE_INVALID,
            language=Language.ES,
            country="ES",
        ),
    ],
)
def test_a_retry_may_say_it_did_not_understand(action: Action):
    text = "Perdona, no lo he entendido. ¿Tienes carnet de conducir en vigor?"
    if isinstance(action, AskField) and action.field == FieldName.EMAIL:
        text = "Perdona, no lo he entendido. ¿Cuál es tu correo electrónico?"
    assert problem(text, action) is None


def test_a_reply_without_the_pending_question_is_rejected():
    found = problem("Perfecto, Lucía.", ask(FieldName.SCHEDULE))
    assert found is not None
    assert found.startswith("no question at the end")


@pytest.mark.parametrize(
    "text",
    [
        "Gracias, Brandon. Ya tengo tu correo. ¿Tiene licencia de manejo vigente?",
        "Perfecto. ¿Tienes licencia de manejo vigente?",
        "Listo, dime su colonia. ¿En qué ciudad vive?",
        # verb endings only the second person has (QA batch 4)
        "Perfecto, si buscas media jornada hay turnos. ¿Tiene licencia de manejo vigente?",
        "¿Cuentas con licencia de manejo vigente?",
        "Gracias. ¿Trabajaste antes de repartidor?",
        "Recibirás la invitación por correo. ¿Tiene licencia de manejo vigente?",
        "Indícame si tiene licencia de manejo vigente.",
        "Escríbeme si tiene licencia de manejo vigente.",
        "¿Ya entregas pedidos con DiDi?",
        "¿Manejabas para Uber antes?",
    ],
)
def test_tu_in_mexico_is_rejected(text: str):
    action = ask(FieldName.HAS_LICENSE, country="MX")
    found = problem(text, action)
    assert found is not None
    assert found.startswith("wrong register")


@pytest.mark.parametrize(
    "text",
    [
        "Indíqueme su correo, por favor. ¿Tiene licencia de manejo vigente?",
        "Las entregas se hacen en moto. ¿Tiene licencia de manejo vigente?",
        "Escríbame aquí: ¿tiene licencia de manejo vigente?",
        "Dígame, ¿tiene licencia de manejo vigente?",
        "Recibirá la invitación por correo. ¿Tiene licencia de manejo vigente?",
        "La hora se elige en el mismo chat. ¿Tiene licencia de manejo vigente?",
        'Usted dijo "busco medio tiempo". ¿Tiene licencia de manejo vigente?',
        "Hay sucursal cerca de Los Ángeles. ¿Tiene licencia de manejo vigente?",
        "Existe un turno de noche. ¿Tiene licencia de manejo vigente?",
        "Ángela, ¿tiene licencia de manejo vigente?",
        "Acompáñeme con unas preguntas: ¿tiene licencia de manejo vigente?",
        "Puede decírmelo cuando guste: ¿tiene licencia de manejo vigente?",
    ],
)
def test_usted_third_person_quotes_and_places_in_mexico_pass(text: str):
    assert problem(text, ask(FieldName.HAS_LICENSE, country="MX")) is None


@pytest.mark.parametrize(
    "text",
    [
        "Perfecto. ¿Tiene carnet de conducir en vigor?",
        "Gracias. ¿En qué barrio vive?",
        "¿Le va bien el jueves a las 10:00?",
        "Escríbame si tiene carnet de conducir.",
        "Confírmeme si tiene carnet de conducir.",
        # usted imperatives with two pronouns attached
        "Dígamelo cuando pueda. ¿Tiene carnet de conducir?",
        "Indíquemelo, por favor. ¿Tiene carnet de conducir?",
        "Confírmemelo cuando pueda. ¿Tiene carnet de conducir?",
        "Envíemelo por aquí. ¿Tiene carnet de conducir?",
    ],
)
def test_usted_verbs_in_spain_are_rejected(text: str):
    found = problem(text, ask(FieldName.HAS_LICENSE))
    assert found is not None
    assert found.startswith("wrong register")


def test_usted_in_spain_is_rejected():
    action = ask(FieldName.HAS_LICENSE)
    found = problem("Perfecto. ¿Tiene usted carnet de conducir?", action)
    assert found is not None
    assert found.startswith("wrong register")


@pytest.mark.parametrize(
    "text",
    [
        # Live test 4f64892c (SAZ-72): the tú reference, rewritten by the model.
        "No tengo esa información concreta, pero puede preguntarla en la entrevista. "
        "¿Me indica su correo electrónico?",
        "No tengo esa información. ¿Cuál es su correo electrónico?",
        "Perfecto. ¿Me confirma su móvil?",
        "Gracias. Indíqueme su nombre completo, por favor.",
    ],
)
def test_usted_addressing_the_candidate_in_spain_is_rejected(text: str):
    found = problem(text, ask(FieldName.EMAIL))
    assert found is not None
    assert found.startswith("wrong register")


@pytest.mark.parametrize(
    "text",
    [
        "No tengo esa información; puedes preguntarlo en la entrevista. ¿Me indicas tu correo?",
        "El local tiene parking y su dirección está en la tarjeta. ¿Me confirmas tu móvil?",
        "La entrevista es con la persona encargada del local. ¿Me dices tu correo?",
        "Se puede cambiarla respondiendo aquí. ¿Te va bien el jueves?",
        "El uniforme lo da la empresa. ¿Cuál es tu correo electrónico?",
        "Escríbeme aquí: ¿cuál es tu correo electrónico?",
        "Dímelo cuando puedas: ¿cuál es tu correo electrónico?",
        # E2E 2026-10-22: an infinitive with two pronouns is not usted
        "Si prefieres no decírmelo, no pasa nada. ¿Cuál es tu correo electrónico?",
        "Puedes enviármelo luego. ¿Cuál es tu correo electrónico?",
        "Voy explicándotelo todo. ¿Cuál es tu correo electrónico?",
        'El documento dice "puede preguntarlo en la entrevista". ¿Cuál es tu correo electrónico?',
        "El encargado te atiende en el local. ¿Cuál es tu correo electrónico?",
    ],
)
def test_tu_and_third_person_in_spain_pass(text: str):
    assert problem(text, ask(FieldName.EMAIL)) is None


def test_mexican_usted_and_spanish_tu_pass():
    assert (
        problem(
            "Muy bien. ¿Tiene licencia de manejo vigente?", ask(FieldName.HAS_LICENSE, country="MX")
        )
        is None
    )
    assert problem("Genial. ¿Tienes carnet de conducir?", ask(FieldName.HAS_LICENSE)) is None


@pytest.mark.parametrize(
    ("address", "location"),
    [
        ("Av. Pablo Livas 2300, Col. Rincón de la Sierra, 67190 Guadalupe", "Guadalupe"),
        ("Av. Patria 1200, Col. Jardines Universidad, 45110 Zapopan", "Zapopan"),
        ("Rambla de la Marina 210, 08907 L'Hospitalet de Llobregat", "L'Hospitalet"),
    ],
)
def test_spanish_place_names_in_an_english_reply_are_not_mixing(address: str, location: str):
    booking = Booked(
        slotId="s1",
        startsAt="2026-10-20T16:00-06:00",
        label="Tuesday 20 October at 16:00",
        location=location,
        address=address,
    )
    action = ConfirmBooking(booking=booking, language=Language.EN, country="MX")
    text = (
        f"You're booked for Tuesday 20 October at 16:00 with the manager at {location}, {address}. "
        "If anything changes, just reply here and we'll move it."
    )
    assert problem(text, action) is None


def test_spanish_in_an_english_reply_is_still_rejected():
    action = ask(FieldName.PHONE, Language.EN)
    assert problem("Gracias, Daniel. ¿Cuál es tu número de móvil?", action) == "mixed languages"


@pytest.mark.parametrize(
    "text",
    [
        "Diego, revisa estos datos: tiempo completo, por la tarde, desde el 2 de noviembre. "
        "¿Está todo bien?",
        "Revisa que todo esté bien, sobre todo el horario de noche. ¿Es correcto?",
        "¿Está bien todo, incluido Sevilla?",
        "Revisa tu teléfono +34 612 345 605. ¿Está todo bien?",
    ],
)
def test_the_read_back_does_not_restate_values(text: str):
    found = problem(text, summary())
    assert found is not None
    assert found.startswith("read-back restates a value")


@pytest.mark.parametrize(
    "text",
    ["Diego, échale un vistazo a la tarjeta. ¿Está todo bien?", "¿Está todo correcto?"],
)
def test_a_short_read_back_passes(text: str):
    assert problem(text, summary()) is None


def test_an_answer_may_take_two_sentences_before_the_question():
    action = AnswerQuestion(
        question="¿Cuánto pagan?",
        follow_up=ask(FieldName.PHONE, country="MX"),
        language=Language.ES,
        country="MX",
    )
    ctx = ReplyContext.for_action(
        action, today=TODAY, faq_snippets=("Q: ¿Cuánto se paga?\nA: De 2,100 a 2,600 pesos.",)
    )
    text = (
        "El sueldo va de 2,100 a 2,600 pesos a la semana. Las propinas van aparte. "
        "¿Cuál es su número de celular?"
    )
    assert problem(text, action, ctx) is None


def test_count_sentences_ignores_greetings_and_address_abbreviations():
    assert count_sentences("¡Hola! Soy Ana. ¿Empezamos?") == 2
    assert count_sentences("Entiendo, Diego. Podemos parar aquí. ¿Le parece?") == 2
    assert count_sentences("Gracias por contestar, Lucía. No podemos seguir. Vuelve luego.") == 3
    assert count_sentences("Te espero en Av. Patria 1200, Col. Centro. ¿Te va bien?") == 2


def test_clarify_with_many_options_asks_for_the_zone_instead_of_listing_them():
    options = tuple(f"mx-cdmx-{n}" for n in range(8))
    action = Clarify(field=FieldName.CITY, options=options, language=Language.ES, country="MX")
    text = fallback_reply(action, ReplyContext.for_action(action, today=TODAY))
    assert "colonia" in text
    assert "mx-cdmx" not in text


# --- retry and FAQ answers through the real implementation, no network ---


def responder(*replies: str) -> tuple[list[dict[str, Any]], Any]:
    bodies: list[dict[str, Any]] = []
    queue = list(replies)

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        text = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx2.Response(200, json=completion({"role": "assistant", "content": text}))

    return bodies, handler


async def test_a_rejected_reply_is_retried_once_naming_the_problem():
    bodies, handler = responder(
        "¿Cuánta experiencia tienes? Puede ser de meses o años.",
        "¿Cuánta experiencia tienes repartiendo?",
    )
    action = ask(FieldName.EXPERIENCE_YEARS)

    result = await make_llm(handler).respond(action, ReplyContext.for_action(action, today=TODAY))

    assert result.reply.text == "¿Cuánta experiencia tienes repartiendo?"
    assert not result.call.fallback_used
    assert len(bodies) == 2
    retry = bodies[1]["messages"][-1]["content"]
    assert "the question is not the last thing" in retry


async def test_tu_in_mexico_twice_falls_back_to_the_template():
    bodies, handler = responder("Ya tengo tu correo. ¿Tiene licencia de manejo vigente?")
    action = ask(FieldName.HAS_LICENSE, country="MX")
    ctx = ReplyContext.for_action(action, today=TODAY)

    result = await make_llm(handler).respond(action, ctx)

    assert len(bodies) == 2
    assert result.reply.text == fallback_reply(action, ctx)
    assert result.call.fallback_used
    assert result.call.error is not None
    assert "wrong register" in result.call.error


def faq_action() -> AnswerQuestion:
    return AnswerQuestion(
        question="¿Hay gimnasio para empleados?",
        follow_up=ask(FieldName.EMAIL, country="MX"),
        language=Language.ES,
        country="MX",
    )


@pytest.mark.parametrize("answered", [True, False])
async def test_an_faq_answer_says_whether_the_snippets_answered(answered: bool):
    text = "No tengo esa información; puede preguntarlo en la entrevista. ¿Y su correo electrónico?"
    _, handler = responder(json.dumps({"answered": answered, "text": text}))
    action = faq_action()
    ctx = ReplyContext.for_action(
        action, today=TODAY, faq_snippets=("Q: Capacitación\nA: Un turno pagado.",)
    )

    result = await make_llm(handler).respond(action, ctx)

    assert result.reply.text == text
    assert result.answered is answered
    assert not result.call.fallback_used


async def test_a_voice_faq_answer_may_quote_the_hours_of_a_snippet():
    text = "Son entre las 10:00 y las 12:00. ¿Y su correo electrónico?"
    _, handler = responder(json.dumps({"answered": True, "text": text}))
    action = faq_action()
    ctx = ReplyContext.for_action(
        action,
        today=TODAY,
        channel=Channel.VOICE,
        faq_snippets=("Q: Horario de entrevistas\nA: Entre las 10:00 y las 12:00.",),
    )

    result = await make_llm(handler).respond(action, ctx)

    assert result.reply.text == text
    assert result.answered is True
    assert not result.call.fallback_used


async def test_an_faq_answer_that_falls_back_does_not_claim_an_answer():
    _, handler = responder("not json at all")
    action = faq_action()
    ctx = ReplyContext.for_action(
        action,
        today=TODAY,
        faq_snippets=("Q: Capacitación\nA: Un turno pagado.",),
        faq_send_as_written=True,
    )

    result = await make_llm(handler).respond(action, ctx)

    assert result.call.fallback_used
    assert result.answered is None
    assert result.reply.text.startswith("Un turno pagado.")


async def test_a_fallback_never_sends_a_passage_that_is_not_a_close_faq_entry():
    """A near-topic passage sent as written would answer another question: the fallback says
    it doesn't know, and `answered` False makes the service log the FAQ miss."""
    _, handler = responder("not json at all")
    action = faq_action()
    ctx = ReplyContext.for_action(
        action, today=TODAY, faq_snippets=("Q: Capacitación\nA: Un turno pagado.",)
    )

    result = await make_llm(handler).respond(action, ctx)

    assert result.call.fallback_used
    assert result.answered is False
    assert result.reply.text.startswith(UNKNOWN_ANSWER["es-MX"])
    assert "turno pagado" not in result.reply.text


def pause_offer() -> Action:
    return OfferPause(language=Language.ES, country="MX", simplify=True)


def simplified(action: AskField) -> AskField:
    return action.model_copy(update={"simplify": True})


@pytest.mark.parametrize("register", REGISTERS)
@pytest.mark.parametrize("reason", [None, *OfferReason])
def test_a_simplified_slot_offer_template_passes_its_own_checks(
    register: Register, reason: OfferReason | None
):
    """Turning the times down with frustration: the question alone, the times on the buttons."""
    language, country = COUNTRY_OF[register]
    slot = Slot(id="s1", startsAt="2026-10-22T11:00", label="jueves 22, 11:00", location="Centro")
    action = OfferSlots(
        location_id=None,
        slots=(slot,),
        reason=reason,
        simplify=True,
        language=language,
        country=country,
    )
    ctx = ctx_for(register)

    text = fallback_reply(action, ctx)

    assert problem(text, action, ctx) is None, text
    assert "11:00" not in text
    assert text.endswith("?")


def test_a_simplified_pause_offer_is_one_sentence():
    text = (
        "Entiendo, Diego, son muchas preguntas. Si quiere, paramos aquí y seguimos después "
        "desde donde nos quedamos. ¿Le parece?"
    )
    assert problem(text, pause_offer()) == "3 sentences (at most 1)"


def test_a_simplified_reply_may_open_with_a_short_acknowledgement():
    text = "Entiendo, Diego. ¿Lo dejamos aquí y seguimos después donde nos quedamos?"
    assert problem(text, pause_offer()) is None


def test_a_simplified_question_is_one_sentence():
    """Rosa's availability question after her complaint: two sentences, so it goes back."""
    text = (
        "Disculpe la espera, Rosa. ¿Qué disponibilidad tiene: tiempo completo, medio tiempo o "
        "fines de semana?"
    )
    action = ask(FieldName.AVAILABILITY, country="MX")

    assert problem(text, action) is None
    assert problem(text, simplified(action)) == "2 sentences (at most 1)"
    assert problem("¿Qué disponibilidad tiene, Rosa?", simplified(action)) is None


def test_a_simplified_answer_may_add_the_pending_question():
    follow_up = simplified(ask(FieldName.AVAILABILITY, country="MX"))
    action = AnswerQuestion(
        question="¿Cuánto pagan?",
        follow_up=follow_up,
        language=Language.ES,
        country="MX",
        simplify=True,
    )
    text = "El pago se lo explican en la entrevista. ¿Qué disponibilidad tiene?"

    assert problem(text, action) is None
    assert problem(f"Es una pregunta muy común, Rosa. {text}", action) == "3 sentences (at most 2)"


def test_a_simplified_de_escalation_may_add_the_pending_question():
    follow_up = simplified(ask(FieldName.AVAILABILITY))
    action = DeEscalate(follow_up=follow_up, language=Language.ES, country="ES", simplify=True)
    text = "Entiendo que se haga largo, vamos rápido. ¿Qué disponibilidad tienes?"

    assert problem(text, action) is None
    assert problem(f"Lo siento mucho, de verdad. {text}", action) == "3 sentences (at most 2)"


@pytest.mark.parametrize("register", REGISTERS)
def test_a_simplified_retry_template_is_the_question_alone(register: Register):
    """A complaint counts as a failed answer, but "I didn't understand" is the wrong reply to it."""
    language, country = COUNTRY_OF[register]
    plain = ask(FieldName.AVAILABILITY, language, country)
    retry = plain.model_copy(update={"retry": 1})
    ctx = ctx_for(register)

    text = fallback_reply(simplified(retry), ctx)

    assert text == fallback_reply(plain, ctx)
    assert count_sentences(text) == 1
    assert fallback_reply(retry, ctx) != text


@pytest.mark.parametrize("register", REGISTERS)
def test_a_simplified_retry_template_keeps_the_validator_hint(register: Register):
    """An invalid value still gets told what was wrong, in the one sentence a frustrated
    candidate gets."""
    language, country = COUNTRY_OF[register]
    retry = ask(FieldName.SCHEDULE, language, country).model_copy(
        update={"retry": 1, "hint": ReasonCode.SCHEDULE_INVALID}
    )
    ctx = ctx_for(register)

    text = fallback_reply(simplified(retry), ctx)
    hint = pack(register).hints[ReasonCode.SCHEDULE_INVALID].rstrip(".")
    assert text.startswith(hint), text
    assert count_sentences(text) == 1
    assert fallback_reply(retry, ctx) != fallback_reply(retry.model_copy(update={"retry": 0}), ctx)


async def test_a_simplified_reply_over_one_sentence_is_retried_then_templated():
    bodies, handler = responder(
        "Disculpe la espera, Rosa. ¿Qué disponibilidad tiene: tiempo completo o medio tiempo?"
    )
    action = simplified(ask(FieldName.AVAILABILITY, country="MX"))
    ctx = ReplyContext.for_action(action, today=TODAY)

    result = await make_llm(handler).respond(action, ctx)

    assert len(bodies) == 2
    assert "2 sentences (at most 1)" in bodies[1]["messages"][-1]["content"]
    assert result.reply.text == fallback_reply(action, ctx)
    assert result.call.fallback_used


async def test_a_simplified_reply_fixed_on_the_retry_is_sent():
    bodies, handler = responder(
        "Disculpe la espera, Rosa. ¿Qué disponibilidad tiene?",
        "¿Qué disponibilidad tiene, Rosa?",
    )
    action = simplified(ask(FieldName.AVAILABILITY, country="MX"))

    result = await make_llm(handler).respond(action, ReplyContext.for_action(action, today=TODAY))

    assert len(bodies) == 2
    assert result.reply.text == "¿Qué disponibilidad tiene, Rosa?"
    assert not result.call.fallback_used


AI_TERM = {"es-ES": "asistente de IA", "es-MX": "asistente de IA", "en": "AI"}


@pytest.mark.parametrize("channel", [Channel.WEB, Channel.VOICE])
@pytest.mark.parametrize("register", REGISTERS)
def test_every_greeting_template_says_ana_is_an_ai(register: Register, channel: Channel):
    language, country = COUNTRY_OF[register]
    greet = Greet(language=language, country=country)

    assert AI_TERM[register] in fallback_reply(greet, ctx_for(register, channel))
    assert AI_TERM[register] in pack(register).busy


@pytest.mark.parametrize(
    ("text", "language", "country"),
    [
        ("¡Hola! Soy Ana, la asistente virtual de Grupo Sazón. ¿Empezamos?", Language.ES, "ES"),
        ("Hi! I'm Ana, Grupo Sazón's virtual assistant. Shall we start?", Language.EN, "ES"),
    ],
)
def test_a_greeting_without_the_ai_term_is_rejected(
    text: str, language: Language, country: Country
):
    greet = Greet(language=language, country=country)

    assert problem(text, greet) == "no AI disclosure (say 'asistente de IA' or 'AI assistant')"


def test_a_greeting_retry_need_not_introduce_ana_again():
    greet = Greet(language=Language.ES, country="ES", retry=1)

    assert problem("Para empezar necesito que aceptes el aviso. ¿Empezamos?", greet) is None


def test_a_streamed_greeting_is_checked_once_it_reaches_its_question():
    greet = Greet(language=Language.ES, country="ES")
    ctx = ReplyContext.for_action(greet, today=TODAY)

    def partial(text: str) -> str | None:
        return reply_problem(
            text,
            ctx.language,
            country=ctx.country,
            action=greet,
            reference=fallback_reply(greet, ctx),
            partial=True,
        )

    assert partial("¡Hola! Soy Ana.") is None
    assert partial("¡Hola! Soy Ana. ¿Aceptas el aviso de privacidad?") is not None


async def test_a_greeting_without_the_ai_term_twice_falls_back_to_the_template():
    bodies, handler = responder("¡Hola! Soy Ana, la asistente virtual de Grupo Sazón. ¿Empezamos?")
    greet = Greet(language=Language.ES, country="ES")
    ctx = ReplyContext.for_action(greet, today=TODAY)

    result = await make_llm(handler).respond(greet, ctx)

    assert len(bodies) == 2
    assert "no AI disclosure" in bodies[1]["messages"][-1]["content"]
    assert result.reply.text == fallback_reply(greet, ctx)
    assert "asistente de IA" in result.reply.text
    assert result.call.fallback_used


# --- interview mode (SAZ-39) ---

MEET = "https://meet.google.com/abc-defg-hij"


def booking_action(
    mode: InterviewMode, *, meet_url: str | None = None, has_email: bool = True, **extra: Any
) -> ConfirmBooking:
    booking = Booked(
        slotId="s1",
        startsAt="2026-10-22T11:00:00-06:00",
        label="jueves 22 de octubre a las 11:00",
        location="Zapopan",
        address="Av. Patria 1200",
        mode=mode,
        meetUrl=meet_url,
    )
    return ConfirmBooking(
        booking=booking, has_email=has_email, language=Language.ES, country="MX", **extra
    )


@pytest.mark.parametrize(
    "text",
    [
        f"Listo, su videollamada es el jueves 22 de octubre a las 11:00. Entre en {MEET}.",
        "Listo, su videollamada es el jueves 22 a las 11:00. El enlace es meet.google.com/abc.",
        "Listo, su videollamada es el jueves 22 a las 11:00. Entre en www.sazon.mx/entrevista.",
    ],
)
def test_a_video_confirmation_never_writes_the_link(text: str):
    assert problem(text, booking_action(InterviewMode.VIDEO, meet_url=MEET)) is not None


def test_no_interview_reply_writes_a_link():
    ask_mode = AskMode(
        options=(InterviewMode.ONSITE, InterviewMode.VIDEO), language=Language.EN, country="ES"
    )
    text = "Would you like it in person or on https://meet.google.com?"
    assert problem(text, ask_mode) is not None
    assert problem("Would you like it in person or by video call?", ask_mode) is None


def test_a_video_confirmation_says_where_the_link_comes():
    action = booking_action(InterviewMode.VIDEO, meet_url=MEET)
    silent = "Listo, su videollamada es el jueves 22 de octubre a las 11:00. Nos vemos."
    told = (
        "Listo, su videollamada con el equipo de selección es el jueves 22 de octubre a las "
        "11:00. Le llegará la invitación de Google Meet a su correo."
    )
    assert problem(silent, action) is not None
    assert problem(told, action) is None


def test_a_streamed_video_confirmation_is_not_cut_before_the_link_line():
    action = booking_action(InterviewMode.VIDEO)
    first = "Listo, su videollamada es el jueves 22 de octubre a las 11:00."
    reference = fallback_reply(action, ctx_for("es-MX", Channel.VOICE))
    assert (
        reply_problem(
            first, Language.ES, country="MX", action=action, reference=reference, partial=True
        )
        is None
    )


def test_a_video_confirmation_gives_no_address():
    text = (
        "Listo, su videollamada es el jueves 22 de octubre a las 11:00 en Av. Patria 1200. "
        "Le llegará el enlace a su correo."
    )
    assert problem(text, booking_action(InterviewMode.VIDEO)) is not None


def test_an_in_person_confirmation_never_mentions_a_video_call():
    action = booking_action(InterviewMode.ONSITE)
    text = (
        "Listo, su entrevista es el jueves 22 de octubre a las 11:00 por videollamada. "
        "Si no puede asistir, escriba aquí y la cambiamos."
    )
    assert problem(text, action) is not None
    assert problem(fallback_reply(action, ctx_for("es-MX")), action) is None


def test_meeting_the_manager_in_person_is_not_a_video_call():
    action = booking_action(InterviewMode.ONSITE)
    text = (
        "Listo, su entrevista es el jueves 22 de octubre a las 11:00 en Zapopan, Av. Patria 1200, "
        "donde conocerá al gerente. Si no puede asistir, escriba aquí y la cambiamos."
    )
    english = text.replace("donde conocerá al gerente", "where you'll meet the manager")
    assert problem(english, action) is None
    google = text.replace("donde conocerá al gerente", "por Google Meet")
    assert problem(google, action) is not None


@pytest.mark.parametrize(
    "text",
    [
        # QA batch 4: a video booking reply.
        "Listo, su entrevista por videollamada con la gerencia es el jueves 22 de octubre a las "
        "11:00. Le llegará una invitación con el enlace de Google Meet a su correo.",
        "Listo, su videollamada con el encargado del local es el jueves 22 de octubre a las "
        "11:00. Le llegará una invitación con el enlace de Google Meet a su correo.",
    ],
)
def test_a_video_interview_is_never_with_the_manager(text: str):
    found = problem(text, booking_action(InterviewMode.VIDEO))
    assert found is not None
    assert found.startswith("video interview with the location's manager")


@pytest.mark.parametrize(
    "text",
    [
        "Las 12 no tengo disponible, pero tengo el martes 27 a las 10:15. ¿Te va bien?",
        "No hay hueco a las 12. ¿Te va bien el martes 27 a las 10:15?",
        "Ese hueco está ocupado. ¿Te va bien el martes 27 a las 10:15?",
    ],
)
def test_an_offer_never_says_by_itself_what_is_free(text: str):
    """Call of 2026-10-22: "Las 12 no tengo disponible" came from the model, not the lookup."""
    slot = Slot(id="s1", startsAt="2026-10-27T10:15", label="martes 27 a las 10:15", location="C")
    action = OfferSlots(
        location_id=None, slots=(slot,), retry=1, language=Language.ES, country="ES"
    )
    found = problem(text, action)
    assert found is not None
    assert found.startswith("says what is free or not")


def test_an_offer_may_repeat_what_its_template_says_is_free():
    slot = Slot(id="s1", startsAt="2026-10-27T10:15", label="martes 27 a las 10:15", location="C")
    action = OfferSlots(
        location_id=None,
        slots=(slot,),
        reason=OfferReason.ASKED,
        matched=False,
        miss=Miss.TIME,
        language=Language.ES,
        country="ES",
    )
    assert (
        problem(fallback_reply(action, ReplyContext.for_action(action, today=TODAY)), action)
        is None
    )


def test_video_times_are_never_offered_with_the_manager():
    slot = Slot(id="s1", startsAt="2026-10-22T11:00", label="jueves 22, 11:00", location="Zapopan")
    action = OfferSlots(
        location_id=None,
        slots=(slot,),
        mode=InterviewMode.VIDEO,
        language=Language.ES,
        country="MX",
    )
    text = "Tengo este horario para la videollamada con la gerencia: jueves 22, 11:00. ¿Le acomoda?"
    found = problem(text, action)
    assert found is not None
    assert found.startswith("video interview with the location's manager")


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (
            "Listo, su entrevista es el jueves 22 de octubre a las 11:00 con el equipo de "
            "reclutamiento en Zapopan, Av. Patria 1200. Si no puede asistir, escriba aquí.",
            "in-person interview with a recruiter",
        ),
        (
            "Listo, su entrevista es el jueves 22 de octubre a las 11:00 en Zapopan, Av. Patria "
            "1200. Si no puede asistir, escriba aquí y la cambiamos.",
            "in-person interview without the location's manager",
        ),
        (
            "Listo, su entrevista es el jueves 22 de octubre a las 11:00 con la gerencia del local "
            "de Zapopan. Si no puede asistir, escriba aquí y la cambiamos.",
            "in-person interview without the location's address",
        ),
    ],
)
def test_an_in_person_interview_is_with_the_manager_at_the_address(text: str, why: str):
    found = problem(text, booking_action(InterviewMode.ONSITE))
    assert found is not None
    assert found.startswith(why)


def test_with_no_email_and_no_link_the_video_confirmation_needs_no_link_line():
    action = booking_action(InterviewMode.VIDEO, has_email=False)
    text = (
        "Listo, su videollamada con el equipo de selección es el jueves 22 de octubre a las "
        "11:00. Escriba aquí para cambiarla."
    )
    assert problem(text, action) is None


@pytest.mark.parametrize(
    "text",
    [
        "Listo, su videollamada con el equipo de selección es el jueves 22 de octubre a las "
        "11:00. Le llegará una invitación con el enlace de Google Meet a su correo.",
        "Listo, su videollamada con un reclutador es el jueves 22 de octubre a las 11:00. "
        "Le llegará una invitación con el enlace de Google Meet a su correo.",
    ],
)
def test_a_video_confirmation_names_the_recruiting_team(text: str):
    assert problem(text, booking_action(InterviewMode.VIDEO)) is None


def test_a_video_confirmation_without_the_recruiting_team_is_rejected():
    """E2E 2026-10-22: video confirmations never said who the call was with."""
    text = (
        "Listo, su videollamada es el jueves 22 de octubre a las 11:00. "
        "Le llegará una invitación con el enlace de Google Meet a su correo."
    )
    found = problem(text, booking_action(InterviewMode.VIDEO))
    assert found is not None
    assert found.startswith("video interview without the recruiting team")
    kept = booking_action(InterviewMode.VIDEO, kept=True)
    assert problem(text, kept) is None


@pytest.mark.parametrize("register", ["es-ES", "es-MX", "en"])
@pytest.mark.parametrize("channel", [Channel.WEB, Channel.VOICE])
@pytest.mark.parametrize("rescheduled", [False, True])
def test_every_video_confirmation_template_names_the_recruiting_team(
    register: Register, channel: Channel, rescheduled: bool
):
    action = booking_action(InterviewMode.VIDEO, rescheduled=rescheduled)
    assert RECRUITER_TERMS.search(fold(fallback_reply(action, ctx_for(register, channel))))


@pytest.mark.parametrize(
    ("register", "expected"),
    [
        ("es-ES", "Hoy es 21 de octubre, ¿te refieres al 1 de octubre de 2027?"),
        ("es-MX", "Hoy es 21 de octubre, ¿se refiere al 1 de octubre de 2027?"),
        ("en", "Today is 21 October, so do you mean 1 October 2027?"),
    ],
)
def test_a_date_rolled_to_next_year_is_read_back_with_today(register: Register, expected: str):
    """D6, sent as written."""
    language, country = COUNTRY_OF[register]
    action = ConfirmValue(
        field=FieldName.START_DATE,
        value="2027-10-01",
        today=date(2026, 10, 21),
        language=language,
        country=country,
    )
    assert fallback_reply(action, ctx_for(register)) == expected
    assert is_fixed(action)


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Entendido, Lucía. No guardamos nada de lo que nos has contado. ¡Suerte!", Language.ES),
        ("Vale, borramos tus datos. Gracias por tu tiempo.", Language.ES),
        ("No problem, we won't keep any of your details. Take care!", Language.EN),
        ("Sure, nothing is stored. All the best!", Language.EN),
    ],
)
def test_a_goodbye_after_consent_never_says_nothing_is_kept(text: str, language: Language):
    """After consent the application is stored: "no guardamos nada" is false (QA batch 3).
    Only the goodbye to a declined privacy notice may say it."""
    for reason in (EndReason.OPT_OUT, EndReason.CLOSED):
        found = problem(text, End(reason=reason, language=language, country="ES"))
        assert (found or "").startswith("says nothing is kept"), found
    declined = End(reason=EndReason.DECLINED_CONSENT, language=language, country="ES")
    assert problem(text, declined) is None


def test_an_agent_names_its_own_interviewers():
    """A school's head of studies in person and head of department by video, not the riders'
    manager and recruiting team."""
    school = agent().model_copy(
        update={
            "interviewers": {
                "onsite": ("jefatura de estudios",),
                "video": ("jefatura de departamento",),
            }
        }
    )
    with use(school):
        assert interviewer_terms(InterviewMode.ONSITE).search(
            "con la jefatura de estudios de aravaca"
        )
        assert not interviewer_terms(InterviewMode.VIDEO).search("con el equipo de seleccion")
    assert interviewer_terms(InterviewMode.VIDEO) is RECRUITER_TERMS
