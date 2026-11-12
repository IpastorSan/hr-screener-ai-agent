from datetime import date, time

import pytest

from sazon.engine.models import (
    Intent,
    Language,
    MatrixLanguage,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.slots import CHANGE, NONE_WORK, Slot, SlotQuery, TimeRequest
from sazon.engine.state import ConversationState, Interview, Offering
from sazon.scheduling.resolve import Reading, read_choice, resolve_choice
from sazon.service import slot_reading, with_slot_choice

TODAY = date(2026, 10, 19)  # Monday


def slot(slot_id: str, starts_at: str) -> Slot:
    return Slot(id=slot_id, startsAt=starts_at, label=slot_id, location="Sazón Gràcia")


# Tuesday morning, Wednesday afternoon, Thursday morning (Madrid, CEST).
OFFERED = (
    slot("tue", "2026-10-20T10:00:00+02:00"),
    slot("wed", "2026-10-21T16:30:00+02:00"),
    slot("thu", "2026-10-22T11:00:00+02:00"),
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("el primero", "tue"),
        ("La primera, gracias", "tue"),
        ("the first one", "tue"),
        ("the second one please", "wed"),
        ("la de en medio", "wed"),
        ("el último", "thu"),
        ("the last one", "thu"),
        ("2", "wed"),
        ("la 3", "thu"),
        ("opción 1", "tue"),
        ("el martes por la mañana", "tue"),
        ("el jueves", "thu"),
        ("Thursday works", "thu"),
        ("el miércoles por la tarde", "wed"),
        ("wednesday afternoon", "wed"),
        ("mañana", "tue"),
        ("mañana por la mañana", "tue"),
        ("pasado mañana", "wed"),
        ("a las 16:30", "wed"),
        ("a las 4 y media", "wed"),
        ("the 4:30 one", "wed"),
        ("at 11am", "thu"),
        ("el 22", "thu"),
        ("el jueves 22 a las 11:00", "thu"),
        ("por la tarde", "wed"),
    ],
)
def test_a_pick_in_words_resolves_to_the_offered_slot(text, expected):
    assert resolve_choice(text, OFFERED, today=TODAY) == expected


@pytest.mark.parametrize(
    "text",
    [
        "ninguno me viene bien",
        "no me va ninguna",
        "none of those work",
        "¿no tienes otro día?",
        "no puedo el martes",
        "I can't do any of them",
    ],
)
def test_turning_them_all_down_is_none(text):
    assert resolve_choice(text, OFFERED, today=TODAY) == NONE_WORK


@pytest.mark.parametrize(
    "text",
    [
        "por la mañana",  # two morning slots
        "el sábado",  # not offered
        "a las 9",
        "vale",
        "¿dónde está el local?",
        "",
        "el 5",  # no such option or day
    ],
)
def test_anything_unclear_resolves_to_nothing(text):
    assert resolve_choice(text, OFFERED, today=TODAY) is None


def test_relative_days_need_today():
    assert resolve_choice("mañana", OFFERED) is None


@pytest.mark.parametrize(
    "text",
    ["quiero cambiar la entrevista", "no voy a poder ir", "can I reschedule?", "no puedo"],
)
def test_after_booking_a_change_request_is_change(text):
    assert resolve_choice(text, (), booked=True, today=TODAY) == CHANGE


def test_after_booking_other_messages_are_nothing():
    assert resolve_choice("gracias, ahí estaré", (), booked=True, today=TODAY) is None


def _picked(text: str, hint: str | None) -> str | None:
    state = ConversationState(phase=Interview(sub=Offering(slots=OFFERED)), consented=True)
    extraction = TurnExtraction(
        intent=Intent.ANSWER,
        fields={},
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
        slot=hint,
    )
    return with_slot_choice(state, extraction, text, TODAY).slot


def test_our_reading_of_the_pick_wins_over_the_extractor_hint():
    assert _picked("el jueves", "wed") == "thu"


def test_the_extractor_hint_is_used_when_our_reading_is_unsure():
    assert _picked("ese me va bien", "wed") == "wed"
    assert _picked("ese me va bien", "not-offered") is None


# --- the 15-minute grid, typed times, several matches, questions (QA batch 2) ---

# Tuesday 10:15, Wednesday 10:45, Thursday 16:30 (Madrid, CEST).
QUARTERS = (
    slot("tue", "2026-10-20T10:15:00+02:00"),
    slot("wed", "2026-10-21T10:45:00+02:00"),
    slot("thu", "2026-10-22T16:30:00+02:00"),
)
# Three times at 16:00 on three days.
FOUR_PM = (
    slot("tue", "2026-10-20T16:00:00-06:00"),
    slot("wed", "2026-10-21T16:00:00-06:00"),
    slot("thu", "2026-10-22T16:00:00-06:00"),
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("10:45", "wed"),
        ("a las 10:15", "tue"),
        ("a las diez y cuarto", "tue"),
        ("las once menos cuarto", "wed"),
        ("a las once menos cuarto", "wed"),
        ("a un cuarto para las once", "wed"),
        ("16.30", "thu"),
        ("a las 4 y media", "thu"),
        ("a las cuatro y media", "thu"),
        ("la de las 16:30", "thu"),
    ],
)
def test_typed_times_on_the_quarter_hour_pick_that_slot(text, expected):
    assert resolve_choice(text, QUARTERS, today=TODAY) == expected


@pytest.mark.parametrize("text", ["10:40", "16:20", "a las 10:50"])
def test_a_time_off_the_grid_books_nothing_and_says_so(text):
    reading = read_choice(text, QUARTERS, today=TODAY)
    assert reading.choice is None
    assert reading.request == TimeRequest(unmatched_time=True)


@pytest.mark.parametrize(
    ("text", "at"),
    [
        ("a las diez", (time(10), time(22))),
        ("a las 11:00", (time(11), time(23))),
        ("las 17:45", (time(17, 45),)),
        ("¿podría ser a las 12?", (time(12),)),
    ],
)
def test_a_time_on_the_grid_none_of_the_offers_has_is_looked_up(text, at):
    """Call of 2026-10-22: "¿podría ser a las 12?" was answered by the model. A time on the
    grid is looked up: offered if free, else the nearest, never booked unasked."""
    reading = read_choice(text, QUARTERS, today=TODAY)
    assert reading.choice is None
    assert reading.request == TimeRequest(query=SlotQuery(at=at))


# The call of 2026-10-22: three times on Tuesday 27 on offer.
ON_THE_27TH = (
    slot("a", "2026-10-27T10:15:00+01:00"),
    slot("b", "2026-10-27T10:30:00+01:00"),
    slot("c", "2026-10-27T10:45:00+01:00"),
)


def test_a_day_with_a_time_asks_for_that_time_that_day():
    """ "El martes 27 puedo, pero ¿podría ser a las 12 de la mañana?": the 12:00 was dropped."""
    reading = read_choice(
        "El martes 27 puedo, pero ¿podría ser a las 12 de la mañana?", ON_THE_27TH, today=THURSDAY
    )
    assert reading.choice is None
    assert reading.request == TimeRequest(
        query=SlotQuery(weekdays=(1,), days=(date(2026, 10, 27),), part="morning", at=(time(12),))
    )


def test_a_time_alone_is_on_the_day_of_the_times_on_offer():
    """ "No, pregunto que si a las 12 puede ser.": went to the model, which said it wasn't free."""
    reading = read_choice("No, pregunto que si a las 12 puede ser.", ON_THE_27TH, today=THURSDAY)
    assert reading.choice is None
    assert reading.request == TimeRequest(
        query=SlotQuery(days=(date(2026, 10, 27),), at=(time(12),))
    )


def test_a_time_on_offer_is_still_a_pick():
    assert resolve_choice("¿puede ser a las 10:30?", ON_THE_27TH, today=THURSDAY) == "b"


@pytest.mark.parametrize(
    "text",
    ["¿Y otras opciones?", "¿Qué otros huecos hay?", "dame más opciones", "any other options?"],
)
def test_asking_for_other_options_is_a_query_for_any_other_time(text):
    """ "¿Y otras opciones?" got the FAQ answer ("puedes cambiar la hora volviendo al chat")."""
    reading = read_choice(text, ON_THE_27TH, today=THURSDAY)
    assert reading.choice is None
    assert reading.request == TimeRequest(query=SlotQuery(other=True))
    assert SlotQuery(other=True).unconstrained


def test_other_times_on_a_day_ask_for_that_day():
    reading = read_choice(
        "¿Qué otros huecos disponibles tienes el martes 27?", ON_THE_27TH, today=THURSDAY
    )
    assert reading.request == TimeRequest(
        query=SlotQuery(weekdays=(1,), days=(date(2026, 10, 27),), other=True)
    )


def test_a_time_several_slots_share_asks_which():
    for text in ("a las cuatro", "A las 4", "a las 16:00", "las 4 pm"):
        reading = read_choice(text, FOUR_PM, today=TODAY)
        assert reading.choice is None, text
        assert reading.request == TimeRequest(matches=("tue", "wed", "thu")), text
    assert resolve_choice("el miércoles a las cuatro", FOUR_PM, today=TODAY) == "wed"


def test_the_option_number_is_not_a_time():
    assert resolve_choice("la 2", QUARTERS, today=TODAY) == "wed"
    assert read_choice("la 2 por favor", QUARTERS, today=TODAY).request is None


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("¿Tienes algo por la tarde?", SlotQuery(part="afternoon")),
        ("¿Y el viernes?", SlotQuery(weekdays=(4,))),
        ("¿y el viernes tienes algo?", SlotQuery(weekdays=(4,))),
        ("¿algo la semana que viene?", SlotQuery(week_of=date(2026, 10, 26))),
        ("¿Hay algo por la mañana?", SlotQuery(part="morning")),
        ("¿no tienes nada más tarde?", SlotQuery(later_than=time(16, 30))),
        ("el sábado", SlotQuery(weekdays=(5,))),
        ("¿algo el finde?", SlotQuery(weekdays=(5, 6))),
        ("¿Y el 30?", SlotQuery(days=(date(2026, 10, 30),))),
        ("¿puede ser mañana?", SlotQuery(days=(date(2026, 10, 20),))),
    ],
)
def test_a_question_about_other_times_is_a_query(text, query):
    reading = read_choice(text, OFFERED, today=TODAY)
    assert reading.choice is None
    assert reading.request == TimeRequest(query=query)


def test_none_with_a_question_turns_them_down_and_asks():
    reading = read_choice("Ninguno me va, ¿tienes algo por la tarde?", OFFERED, today=TODAY)
    assert reading.choice == NONE_WORK
    assert reading.request == TimeRequest(query=SlotQuery(part="afternoon"))


def test_a_day_turned_down_is_not_asked_about():
    reading = read_choice("no puedo el martes", OFFERED, today=TODAY)
    assert reading.choice == NONE_WORK
    assert reading.request is None


def test_moving_a_booking_to_a_day_carries_the_query():
    reading = read_choice("¿La puedo cambiar al viernes?", (), booked=True, today=TODAY)
    assert reading.choice == CHANGE
    assert reading.request == TimeRequest(query=SlotQuery(weekdays=(4,)))


def _reading(text: str, hint: str | None, offered=FOUR_PM):
    state = ConversationState(phase=Interview(sub=Offering(slots=offered)), consented=True)
    extraction = TurnExtraction(
        intent=Intent.ANSWER,
        fields={},
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
        slot=hint,
    )
    found, request = slot_reading(state, extraction, text, TODAY)
    return found.slot, request


def test_the_extractor_hint_never_books_what_our_reading_finds_ambiguous():
    """ "A las cuatro" with three times at 16:00: the extractor's guess is not a pick."""
    choice, request = _reading("A las cuatro", "wed")
    assert choice is None
    assert request == TimeRequest(matches=("tue", "wed", "thu"))


def test_the_extractor_hint_never_books_a_time_nobody_named():
    choice, request = _reading("10:40", "tue", QUARTERS)
    assert choice is None
    assert request == TimeRequest(unmatched_time=True)


def test_the_extractor_hint_never_books_on_a_question():
    choice, request = _reading("¿Y el viernes?", "tue", QUARTERS)
    assert choice is None
    assert request == TimeRequest(query=SlotQuery(weekdays=(4,)))


# --- two offers on the same weekday at the same time (the loop seen in production) ---

TWO_TUESDAYS = (
    slot("t27", "2026-10-27T10:00:00+01:00"),
    slot("t3", "2026-11-03T10:00:00+01:00"),
)
THURSDAY = date(2026, 10, 22)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("El martes 27 está bien.", "t27"),
        ("El martes 27.", "t27"),
        ("martes 27", "t27"),
        ("el 27", "t27"),
        ("el día 27", "t27"),
        ("27 de octubre", "t27"),
        ("el 27 de octubre a las diez", "t27"),
        ("el veintisiete", "t27"),
        ("el martes veintisiete", "t27"),
        ("el de octubre", "t27"),
        ("el 3", "t3"),
        ("el tres", "t3"),
        ("el martes 3", "t3"),
        ("el martes 3 a las diez", "t3"),
        ("el 3 de noviembre", "t3"),
        ("3 noviembre", "t3"),
        ("el de noviembre", "t3"),
        ("Tuesday the 3rd", "t3"),
        ("November 3rd", "t3"),
        ("the 3rd may work", "t3"),
    ],
)
def test_the_day_of_the_month_tells_two_tuesdays_apart(text, expected):
    assert read_choice(text, TWO_TUESDAYS, today=THURSDAY) == Reading(choice=expected)


@pytest.mark.parametrize("text", ["el martes", "el martes a las diez", "a las diez"])
def test_two_tuesdays_named_by_weekday_alone_ask_which(text):
    reading = read_choice(text, TWO_TUESDAYS, today=THURSDAY)
    assert reading.request == TimeRequest(matches=("t27", "t3"))


NOVEMBER_FIRST = (
    slot("fri", "2026-10-30T10:00:00+01:00"),
    slot("sun1", "2026-11-01T10:00:00+01:00"),
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("el uno", "sun1"),
        ("el día primero", "sun1"),
        ("el primero de noviembre", "sun1"),
        ("el domingo uno", "sun1"),
        ("el 30", "fri"),
        ("el treinta", "fri"),
    ],
)
def test_the_first_of_the_month_in_words(text, expected):
    assert resolve_choice(text, NOVEMBER_FIRST, today=THURSDAY) == expected


def test_el_primero_with_an_offer_on_the_first_that_is_not_first_asks_which():
    reading = read_choice("el primero", NOVEMBER_FIRST, today=THURSDAY)
    assert reading.request == TimeRequest(matches=("fri", "sun1"))


def test_el_primero_with_no_offer_on_the_first_is_the_first_option():
    assert resolve_choice("el primero", TWO_TUESDAYS, today=THURSDAY) == "t27"


def test_a_bare_number_that_is_no_offered_day_is_still_the_option():
    assert resolve_choice("el 2", TWO_TUESDAYS, today=THURSDAY) == "t3"


def test_a_date_with_its_month_asked_about_is_looked_up_in_that_month():
    reading = read_choice("¿tienes algo el 5 de noviembre?", TWO_TUESDAYS, today=THURSDAY)
    assert reading.request == TimeRequest(query=SlotQuery(days=(date(2026, 11, 5),)))


def test_an_impossible_date_is_not_looked_up():
    reading = read_choice("¿y el 31 de noviembre?", TWO_TUESDAYS, today=THURSDAY)
    assert reading.request is None


@pytest.mark.parametrize("hint", [None, "t3"])
@pytest.mark.parametrize("text", ["El martes 27 está bien.", "El martes 27."])
def test_the_production_transcript_books_the_27th(text, hint):
    """ "Pueees, ¿no podría ser el martes?" gave two Tuesdays at 10:00; "El martes 27 está
    bien." books the 27th, whatever the extractor guessed."""
    assert _reading(text, hint, TWO_TUESDAYS) == ("t27", None)
