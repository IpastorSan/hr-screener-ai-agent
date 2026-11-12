"""No reply promises to contact or notify the candidate later (SAZ-42).

The first real call ended with "¿Quiere que le avisemos cuando abran nuevas fechas?": nobody
follows up from the chat, so a reply that says someone will is rejected, retried once with the
problem named, then replaced by the template.
"""

from datetime import date
from typing import Any

import pytest
from test_llm import make_llm
from test_reply_checks import responder
from test_respond_stream import make_llm as make_stream_llm

from sazon.agents.riders import Close
from sazon.engine.actions import ApplicationSaved, Disqualify
from sazon.engine.models import Language
from sazon.engine.spoken import Register
from sazon.engine.texts import REGISTERS
from sazon.llm import Channel, ReplyContext
from sazon.llm.fallback_templates import fallback_reply
from sazon.llm.reply_checks import follow_up_promise, reply_problem

TODAY = date(2026, 10, 19)
LANGUAGE_OF: dict[Register, tuple[Language, Any]] = {
    "es-ES": (Language.ES, "ES"),
    "es-MX": (Language.ES, "MX"),
    "en": (Language.EN, "ES"),
}


@pytest.mark.parametrize(
    "text",
    [
        "Por ahora no hay horarios. ¿Quiere que le avisemos cuando abran nuevas fechas?",
        "Le avisaremos cuando haya horarios.",
        "Te avisamos en cuanto abramos cerca.",
        "Le contactaremos pronto.",
        "Te escribiremos por correo.",
        "Nos pondremos en contacto con usted.",
        "El equipo se pondrá en contacto contigo.",
        "Podemos avisarte si abrimos un local.",
        "Recibirás noticias nuestras pronto.",
        "Le llamaremos la semana que viene.",
    ],
)
def test_spanish_promises_are_found(text):
    assert follow_up_promise(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Would you like us to let you know when new dates open?",
        "We'll let you know if we open nearby.",
        "We will contact you soon.",
        "We'll be in touch.",
        "You'll hear from us shortly.",
        "I'll keep you posted.",
        "We will notify you when there are times.",
        "Someone will call you tomorrow.",
    ],
)
def test_english_promises_are_found(text):
    assert follow_up_promise(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Sus datos de contacto quedan guardados.",
        "¿Me da un teléfono de contacto?",
        "Para el contacto sobre privacidad, escriba a privacidad@gruposazon.example.",
        "Your contact details are saved.",
        "Contact us at privacidad@gruposazon.example to delete your data.",
        "I'll email you the link.",
        "Le envío el enlace por correo.",
        "Si no puede asistir, escriba aquí y la cambiamos.",
        "Su solicitud queda guardada.",
        "¿Cómo te llamas?",
    ],
)
def test_contact_without_a_promise_passes(text):
    assert follow_up_promise(text) is None


@pytest.mark.parametrize("register", REGISTERS)
@pytest.mark.parametrize("channel", list(Channel))
def test_no_template_promises_anything(register: Register, channel: Channel):
    language, country = LANGUAGE_OF[register]
    meta: dict[str, Any] = {"language": language, "country": country}
    actions = [
        ApplicationSaved(**meta),
        ApplicationSaved(none_free=True, **meta),
        Disqualify(
            reason=Close.OUT_OF_AREA,
            invite_reapply=False,
            talent_pool_offer=True,
            **meta,
        ),
    ]
    for action in actions:
        ctx = ReplyContext.for_action(action, today=TODAY, channel=channel)
        assert follow_up_promise(fallback_reply(action, ctx)) is None


def test_the_saved_application_asks_nothing():
    action = ApplicationSaved(none_free=True, language=Language.ES, country="MX")
    text = fallback_reply(action, ReplyContext.for_action(action, today=TODAY))
    assert "?" not in text
    assert text.endswith("Su solicitud queda guardada.")


@pytest.mark.parametrize(
    ("register", "text"),
    [
        ("es-MX", "Por ahora no hay horarios libres. Le avisaremos cuando haya nuevas fechas."),
        ("es-ES", "No hay horarios libres ahora. Te avisamos cuando abran nuevas fechas."),
        ("en", "There are no times free right now. We'll let you know when new dates open."),
    ],
)
def test_reply_problem_names_the_promise(register: Register, text: str):
    language, country = LANGUAGE_OF[register]
    problem = reply_problem(text, language, country=country)
    assert problem is not None
    assert problem.startswith("promises to follow up")


async def test_a_promise_is_retried_then_replaced_by_the_template():
    bodies, handler = responder("Por ahora no hay horarios. Le avisaremos cuando haya.")
    action = ApplicationSaved(none_free=True, language=Language.ES, country="MX")
    ctx = ReplyContext.for_action(action, today=TODAY)

    result = await make_llm(handler).respond(action, ctx)

    assert len(bodies) == 2
    assert "promises to follow up" in bodies[1]["messages"][-1]["content"]
    assert result.reply.text == fallback_reply(action, ctx)
    assert result.call.fallback_used


async def test_a_retry_without_the_promise_is_sent():
    bodies, handler = responder(
        "Por ahora no hay horarios. Le avisaremos cuando haya.",
        "Por ahora no hay horarios libres en su sucursal. Su solicitud queda guardada.",
    )
    action = ApplicationSaved(none_free=True, language=Language.ES, country="MX")
    ctx = ReplyContext.for_action(action, today=TODAY)

    result = await make_llm(handler).respond(action, ctx)

    assert len(bodies) == 2
    assert result.reply.text.endswith("Su solicitud queda guardada.")
    assert not result.call.fallback_used


async def test_a_spoken_promise_is_never_said():
    llm, _ = make_stream_llm("Por ahora no hay horarios. ", "Le avisaremos cuando haya.")
    action = ApplicationSaved(none_free=True, language=Language.ES, country="MX")
    ctx = ReplyContext.for_action(action, today=TODAY, channel=Channel.VOICE)
    said: list[str] = []

    result = await llm.respond_stream(action, ctx, said.append)

    assert all(follow_up_promise(piece) is None for piece in said)
    assert follow_up_promise(result.reply.text) is None
