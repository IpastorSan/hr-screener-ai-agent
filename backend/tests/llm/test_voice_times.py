"""Interview times in words in every voice message that carries one (SAZ-48): offers, booking
confirmations, the status of a closed application. The day of the month stays in digits
("jueves 22", SAZ-50). The web keeps "09:00"."""

import re
from datetime import date

import pytest

from sazon.agents.riders import FieldName
from sazon.engine.actions import (
    AnswerQuestion,
    ApplicationStatus,
    AskField,
    ConfirmBooking,
    OfferSlots,
)
from sazon.engine.models import InterviewMode, Language
from sazon.engine.slots import Booked, OfferReason, Slot
from sazon.engine.spoken import Register, spoken_when
from sazon.engine.texts import REGISTERS
from sazon.engine.validators import Country
from sazon.llm import Channel, ReplyContext
from sazon.llm.fallback_templates import (
    fallback_reply,
    spoken_action,
)
from sazon.llm.pydantic_ai_impl import respond_prompt
from sazon.llm.reply_checks import reply_problem, spoken_digits

TODAY = date(2026, 10, 20)
COUNTRY_OF: dict[Register, tuple[Language, Country]] = {
    "es-ES": (Language.ES, "ES"),
    "es-MX": (Language.ES, "MX"),
    "en": (Language.EN, "ES"),
}
# Call 13b77d6e: Wednesday 21 and Thursday 22 at 09:00, Madrid.
WEDNESDAY = Slot(
    id="s1", startsAt="2026-10-21T09:00:00+02:00", label="miércoles 21 a las 09:00", location="X"
)
THURSDAY = Slot(
    id="s2", startsAt="2026-10-22T09:00:00+02:00", label="jueves 22 a las 09:00", location="X"
)
LATER = Slot(
    id="s3", startsAt="2026-10-22T16:30:00+02:00", label="jueves 22 a las 16:30", location="X"
)
BOOKED = Booked(
    slotId="s2",
    startsAt="2026-10-22T09:00:00+02:00",
    label="jueves 22 de octubre a las 09:00",
    location="Madrid Centro",
    address="Calle Mayor 1",
)


def meta(register: Register) -> dict[str, object]:
    language, country = COUNTRY_OF[register]
    return {"language": language, "country": country}


def ctx(register: Register, channel: Channel) -> ReplyContext:
    language, country = COUNTRY_OF[register]
    return ReplyContext(channel=channel, language=language, country=country, today=TODAY)


def offer(register: Register, *slots: Slot, **extra: object) -> OfferSlots:
    return OfferSlots(location_id="es-mad-centro", slots=slots, **extra, **meta(register))  # pyright: ignore[reportArgumentType]


SHARED_TIME = {
    "es-ES": "¿Te va bien la entrevista el miércoles 21 o el jueves 22, a las nueve de la mañana?",
    "es-MX": "¿Le acomoda la entrevista el miércoles 21 o el jueves 22, a las nueve de la mañana?",
    "en": "Does an interview on Wednesday the 21st or Thursday the 22nd, at "
    "nine a.m. work for you?",
}


@pytest.mark.parametrize("register", REGISTERS)
def test_voice_offers_say_a_shared_time_once_in_words(register: Register):
    text = fallback_reply(offer(register, WEDNESDAY, THURSDAY), ctx(register, Channel.VOICE))
    assert text == SHARED_TIME[register]


@pytest.mark.parametrize("register", REGISTERS)
def test_voice_offers_say_each_time_when_they_differ(register: Register):
    """The sentence is pinned by the shared-time test above; here, each time in words."""
    action = offer(register, WEDNESDAY, LATER, THURSDAY)  # only the first two are read
    text = fallback_reply(action, ctx(register, Channel.VOICE))
    for slot in (WEDNESDAY, LATER):
        assert spoken_when(slot.starts_at, register) in text, text
    assert spoken_digits(text) is None, text
    assert spoken_when(THURSDAY.starts_at, register) not in text, text


SAME_DAY = {
    "es-ES": "¿Te va bien la entrevista el jueves 22, a las nueve de la mañana o a las cuatro y "
    "media de la tarde?",
    "es-MX": "¿Le acomoda la entrevista el jueves 22, a las nueve de la mañana o a las cuatro y "
    "media de la tarde?",
    "en": "Does an interview on Thursday the 22nd, at nine a.m. or at four thirty p.m. work for "
    "you?",
}


@pytest.mark.parametrize("register", REGISTERS)
def test_voice_offers_say_one_day_once(register: Register):
    """Two times on one day ("¿puede ser el martes?" gets the nearest one's): the day once."""
    text = fallback_reply(offer(register, THURSDAY, LATER), ctx(register, Channel.VOICE))
    assert text == SAME_DAY[register]


@pytest.mark.parametrize("register", REGISTERS)
def test_the_web_keeps_the_digits(register: Register):
    text = fallback_reply(offer(register, WEDNESDAY, THURSDAY), ctx(register, Channel.WEB))
    assert "miércoles 21 a las 09:00" in text
    booking = ConfirmBooking(booking=BOOKED, **meta(register))  # pyright: ignore[reportArgumentType]
    assert "jueves 22 de octubre a las 09:00" in fallback_reply(booking, ctx(register, Channel.WEB))


PICK = {
    ("es-ES", Channel.WEB): "Elige en los botones: ¿cuál te viene mejor?",
    ("es-MX", Channel.WEB): "Elija en los botones: ¿cuál le acomoda mejor?",
    ("en", Channel.WEB): "Pick one of the buttons: which suits you best?",
    ("es-ES", Channel.VOICE): "¿Cuál prefieres: el miércoles 21 o el jueves 22?",
    ("es-MX", Channel.VOICE): "¿Cuál prefiere: el miércoles 21 o el jueves 22?",
    ("en", Channel.VOICE): "Which do you prefer: Wednesday the 21st or Thursday the 22nd?",
}


@pytest.mark.parametrize("channel", [Channel.WEB, Channel.VOICE])
@pytest.mark.parametrize("register", REGISTERS)
def test_words_that_fit_several_again_get_a_one_line_ask(register: Register, channel: Channel):
    """Never "Hay más de uno así" twice: by web the buttons carry the times, by voice the days
    alone, since the time is the same."""
    action = offer(register, WEDNESDAY, THURSDAY, retry=1, pick=True)
    assert fallback_reply(action, ctx(register, channel)) == PICK[register, channel]


WHEN = {
    "es-ES": "jueves 22 a las nueve de la mañana",
    "es-MX": "jueves 22 a las nueve de la mañana",
    "en": "Thursday the 22nd at nine a.m.",
}


@pytest.mark.parametrize("register", REGISTERS)
@pytest.mark.parametrize(
    "booking",
    [
        {},
        {"rescheduled": True},
        {"kept": True},
        {"mode": InterviewMode.VIDEO},
        {"mode": InterviewMode.VIDEO, "rescheduled": True},
        {"mode": InterviewMode.VIDEO, "kept": True},
    ],
)
def test_voice_booking_confirmations_say_the_time_in_words(
    register: Register, booking: dict[str, object]
):
    mode = booking.pop("mode", None)
    booked = BOOKED.model_copy(update={"mode": mode})
    action = ConfirmBooking(booking=booked, has_email=True, **booking, **meta(register))  # pyright: ignore[reportArgumentType]
    text = fallback_reply(action, ctx(register, Channel.VOICE))
    assert WHEN[register] in text, text
    assert spoken_digits(text) is None, text


@pytest.mark.parametrize("register", REGISTERS)
@pytest.mark.parametrize("mode", [None, InterviewMode.VIDEO])
def test_the_status_of_a_booked_application_says_the_time_in_words(
    register: Register, mode: InterviewMode | None
):
    booked = BOOKED.model_copy(update={"mode": mode})
    action = ApplicationStatus(booking=booked, **meta(register))  # pyright: ignore[reportArgumentType]
    text = fallback_reply(action, ctx(register, Channel.VOICE))
    assert WHEN[register] in text, text
    assert spoken_digits(text) is None, text
    assert "09:00" in fallback_reply(action, ctx(register, Channel.WEB))


@pytest.mark.parametrize("register", REGISTERS)
def test_an_offer_after_an_answer_is_in_words_too(register: Register):
    """Call 13b77d6e: the offer came again after an answer ("No tengo esa información...")."""
    follow_up = offer(register, WEDNESDAY, THURSDAY)
    action = AnswerQuestion(question="¿Hay parking?", follow_up=follow_up, **meta(register))  # pyright: ignore[reportArgumentType]
    text = fallback_reply(action, ctx(register, Channel.VOICE))
    assert text.endswith(SHARED_TIME[register]), text


def test_spoken_action_rewrites_only_the_labels_said():
    action = offer("es-ES", WEDNESDAY, THURSDAY, reason=OfferReason.QUALIFIED)
    spoken = spoken_action(action, "es-ES")
    assert [slot.label for slot in spoken.slots] == [
        "miércoles 21 a las nueve de la mañana",
        "jueves 22 a las nueve de la mañana",
    ]
    assert all(slot.time is None for slot in spoken.slots)
    assert [slot.starts_at for slot in spoken.slots] == [WEDNESDAY.starts_at, THURSDAY.starts_at]
    assert spoken.ui == action.ui  # the page's picker keeps "09:00"
    assert action.slots == (WEDNESDAY, THURSDAY)  # the action itself is left alone
    ask = AskField(field=FieldName.FULL_NAME, language=Language.ES, country="ES")
    assert spoken_action(ask, "es-ES") is ask
    status = ApplicationStatus(language=Language.ES, country="ES")  # saved, no booking
    assert spoken_action(status, "es-ES") is status


@pytest.mark.parametrize("register", REGISTERS)
def test_the_voice_responder_is_given_the_words(register: Register):
    """The model gets the label it is to say; it never converts a time itself."""
    action = offer(register, WEDNESDAY, THURSDAY)
    voice = respond_prompt(action, ctx(register, Channel.VOICE), "ref")
    assert WHEN[register] in voice, voice
    assert not re.search(r"\d\d:\d\d|T09", voice), voice  # neither the label nor startsAt
    web = respond_prompt(action, ctx(register, Channel.WEB), "ref")
    assert "jueves 22 a las 09:00" in web
    booking = ConfirmBooking(booking=BOOKED, **meta(register))  # pyright: ignore[reportArgumentType]
    assert WHEN[register] in respond_prompt(booking, ctx(register, Channel.VOICE), "ref")


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("¿Te va bien el miércoles 21 a las 09:00 o el jueves 22 a las 09:00?", "09:00"),
        ("¿Te va bien el miércoles o el jueves a las 09:00?", "09:00"),
        ("Done, it's on Thursday the 22nd at 9 am.", "9 am"),
        ("Your interview is at 9am.", "9am"),
        ("A las 16.30, en Gràcia.", "16.30"),
        # The day of the month stays in digits (SAZ-50).
        ("¿Te va bien el miércoles 21 a las nueve de la mañana?", None),
        ("Listo, tu videollamada es el 22 de octubre.", None),
        ("Done, it's on the 22nd.", None),
        ("Thursday the 22nd at nine a.m.", None),
        ("¿Te va bien el miércoles veintiuno a las nueve de la mañana?", None),
        ("Son unos 3 minutos y pagamos 1.200 euros al mes.", None),
    ],
)
def test_spoken_digits(text: str, found: str | None):
    assert spoken_digits(text) == found


def test_voice_replies_with_a_time_in_digits_are_rejected():
    """The responder may turn the words back into "09:00": the reply is retried, then the
    template in words is sent."""
    action = offer("es-ES", WEDNESDAY, THURSDAY)
    digits = "¿Te va bien el miércoles 21 o el jueves 22 a las 09:00?"
    problem = reply_problem(digits, Language.ES, country="ES", action=action, voice=True)
    assert problem is not None
    assert problem.startswith("a time in digits")
    assert reply_problem(digits, Language.ES, country="ES", action=action) is None  # web
    words = SHARED_TIME["es-ES"]
    assert reply_problem(words, Language.ES, country="ES", action=action, voice=True) is None


HOURS_SNIPPET = "Q: Horario de entrevistas\nA: Entre las 10:00 y las 12:00, de lunes a viernes."


def test_a_voice_faq_answer_may_quote_the_document_times():
    """A document gives hours in digits: quoting them is not a voice time to rewrite, or every
    answer from it would fall back to "I don't know" and be logged as an FAQ gap."""
    follow_up = AskField(field=FieldName.EMAIL, language=Language.ES, country="ES")
    action = AnswerQuestion(
        question="¿A qué hora son las entrevistas?",
        follow_up=follow_up,
        language=Language.ES,
        country="ES",
    )
    reply = "Son entre las 10:00 y las 12:00, de lunes a viernes. ¿Cuál es tu correo?"
    check = {"country": "ES", "action": action, "voice": True}
    assert reply_problem(reply, Language.ES, **check, quotes=(HOURS_SNIPPET,)) is None
    other = reply.replace("12:00", "13:00")
    assert reply_problem(other, Language.ES, **check, quotes=(HOURS_SNIPPET,)) is not None
    assert reply_problem(reply, Language.ES, **check) is not None


# --- a moved or kept booking by voice (SAZ-49) ---

MOVED_VOICE = {
    "es-ES": "Hecho, tu entrevista con la persona encargada del local de Madrid Centro pasa "
    "al jueves 22 a las nueve de la mañana.",
    "es-MX": "Hecho, su entrevista con la gerencia del local de Madrid Centro pasa al jueves "
    "22 a las nueve de la mañana.",
    "en": "Done, your interview with the manager of the Madrid Centro location is now on "
    "Thursday the 22nd at nine a.m.",
}
KEPT_VOICE = {
    "es-ES": "Entendido, mantengo tu entrevista del jueves 22 a las nueve de la mañana.",
    "es-MX": "Entendido, mantengo su entrevista del jueves 22 a las nueve de la mañana.",
    "en": "Understood, I'll keep your interview on Thursday the 22nd at nine a.m.",
}
WEB_ONLY = re.compile(r"Calle Mayor|escrib|reply here|write", re.IGNORECASE)
"""The address and "write here to change it" belong to the web."""


@pytest.mark.parametrize("register", REGISTERS)
@pytest.mark.parametrize(("flag", "expected"), [("rescheduled", MOVED_VOICE), ("kept", KEPT_VOICE)])
def test_a_moved_or_kept_booking_is_said_short(
    register: Register, flag: str, expected: dict[str, str]
):
    action = ConfirmBooking(booking=BOOKED, **{flag: True}, **meta(register))  # pyright: ignore[reportArgumentType]
    text = fallback_reply(action, ctx(register, Channel.VOICE))
    assert text == expected[register]
    assert not WEB_ONLY.search(text), text
    language, country = COUNTRY_OF[register]
    assert reply_problem(text, language, country=country, action=action, voice=True) is None
    web = fallback_reply(action, ctx(register, Channel.WEB))
    assert "Calle Mayor 1" in web or flag == "kept", web
    assert "09:00" in web


@pytest.mark.parametrize("mode", [None, InterviewMode.VIDEO])
@pytest.mark.parametrize("flag", ["", "rescheduled", "kept"])
def test_a_time_at_the_end_of_a_sentence_keeps_one_full_stop(mode: InterviewMode | None, flag: str):
    booked = BOOKED.model_copy(update={"mode": mode})
    flags = {flag: True} if flag else {}
    action = ConfirmBooking(booking=booked, has_email=False, **flags, **meta("en"))  # pyright: ignore[reportArgumentType]
    text = fallback_reply(action, ctx("en", Channel.VOICE))
    assert "a.m.." not in text, text
