"""Every field correctable by name, the place shown as the candidate said it, and the same place
explained rather than applied again (production, MX voice, 2026-10-22: "Vivo en Roma, en Ciudad
de México" showed "Zona: Cuauhtémoc", and "La zona es Roma" changed nothing)."""

from datetime import date

import pytest

from sazon.agents.riders import FieldName
from sazon.engine import (
    AnswerQuestion,
    AskField,
    BookSlot,
    ConfirmBooking,
    ConfirmMove,
    ConfirmSummary,
    ConfirmValue,
    ConversationContext,
    ConversationState,
    DetailsUpdated,
    Disqualify,
    DuplicateChecked,
    End,
    FindModes,
    FindSlots,
    ModesFound,
    OfferSlots,
    SlotBooked,
    SlotsFound,
    on_lookup,
    reoffer,
    start,
    step,
)
from sazon.engine.models import (
    Availability,
    FieldCandidate,
    Intent,
    InterviewMode,
    Language,
    MatrixLanguage,
    Outcome,
    Schedule,
    Sentiment,
    Stage,
    TurnExtraction,
)
from sazon.engine.slots import Booked, OfferReason, Slot
from sazon.engine.state import (
    DuplicateMatch,
    InterviewFix,
)
from sazon.engine.ui import summary_ui

TODAY = date(2026, 10, 19)
FIELDS = {
    "has_license": True,
    "city": "Roma, Ciudad de México",
    "availability": ["full_time"],
    "schedule": "morning",
    "experience_years": 2,
    "platforms": ["rappi"],
    "start_date": "2026-10-26",
}
CUAUH = "mx-cdmx-cuauh"


def turn(
    intent: Intent = Intent.ANSWER, *, question: str | None = None, **fields: object
) -> TurnExtraction:
    return TurnExtraction.model_validate(
        {
            "intent": intent,
            "fields": {k: {"value": v, "confidence": 0.9} for k, v in fields.items()},
            "language": Language.ES,
            "matrix_language": MatrixLanguage.ES,
            "english_ratio": 0.0,
            "code_switched": False,
            "sentiment": Sentiment.NEUTRAL,
            "question": question,
        }
    )


def say(state: ConversationState, extraction: TurnExtraction, **kwargs: object):
    return step(state, extraction, today=TODAY, **kwargs)  # pyright: ignore[reportArgumentType]


def at_contact(voice: bool = False) -> ConversationState:
    """The contact details given, their duplicate check pending."""
    state, _ = start(ConversationContext(country="MX"))
    state, _ = say(state.model_copy(update={"voice": voice}), turn(Intent.CONFIRM))
    contact = turn(full_name="Jorge Ramírez Soto", phone="5512345678", email="j@example.com")
    return say(state, contact)[0]


def at_license(voice: bool = False) -> ConversationState:
    state, action = on_lookup(at_contact(voice), DuplicateChecked(match=None))
    assert isinstance(action, AskField)
    assert action.field == FieldName.HAS_LICENSE
    return state


def at_summary(voice: bool = False) -> tuple[ConversationState, ConfirmSummary]:
    state, action = say(at_license(voice), turn(**FIELDS))
    assert isinstance(action, ConfirmSummary)
    return state, action


def asks_again(action: object, field: FieldName) -> bool:
    return isinstance(action, AskField) and action.field == field and action.again


# --- the place as the candidate said it ---


def test_the_zone_named_is_kept_with_the_location():
    state, _ = at_summary()
    assert state.profile.location_id == CUAUH
    assert state.profile.place_said == "Roma"
    rows = {row.label: row.value for row in summary_ui(state.profile, (), Language.ES, "MX").fields}
    assert rows["Zona"] == "Roma · local Cuauhtémoc"


def test_the_location_named_itself_is_shown_alone():
    state, _ = say(at_license(), turn(**{**FIELDS, "city": "Cuauhtémoc"}))
    assert state.profile.place_said is None
    rows = {row.label: row.value for row in summary_ui(state.profile, (), Language.EN, "MX").fields}
    assert rows["Area"] == "Cuauhtémoc"


def test_the_summary_says_by_voice_that_it_reads_its_values():
    _, action = at_summary(voice=True)
    assert action.voice
    _, web = at_summary()
    assert not web.voice


# --- every field by name ---

FIELD_VALUES = {
    FieldName.FULL_NAME: "Jorge Ramírez Pardo",
    FieldName.PHONE: "5598765432",
    FieldName.EMAIL: "jorge@example.com",
    FieldName.HAS_LICENSE: True,
    FieldName.CITY: "Polanco",
    FieldName.AVAILABILITY: ["weekends"],
    FieldName.SCHEDULE: "evening",
    FieldName.EXPERIENCE_YEARS: 4,
    FieldName.PLATFORMS: ["didi food"],
    FieldName.START_DATE: "2026-11-02",
}


@pytest.mark.parametrize("voice", [False, True])
@pytest.mark.parametrize("field", list(FieldName))
def test_any_field_named_at_the_summary_is_asked_again(field: FieldName, voice: bool):
    """ "Quiero cambiar la disponibilidad" asks it again, and the answer goes back to the
    summary; never "¿qué dato quiere cambiar?" again."""
    state, _ = at_summary(voice)

    state, action = say(state, turn(Intent.CORRECTION), fix=field)

    assert asks_again(action, field)
    state, action = say(state, turn(**{field.value: FIELD_VALUES[field]}))
    if field == FieldName.EXPERIENCE_YEARS:
        assert isinstance(action, ConfirmSummary)  # the platforms are already in
    assert isinstance(action, ConfirmSummary), action
    assert state.outcome is None


@pytest.mark.parametrize("field", list(FieldName))
def test_any_field_named_mid_flow_is_asked_again(field: FieldName):
    """Before the summary, once the field has a value: asked again, then the flow carries on."""
    state, _ = say(at_license(), turn(**FIELDS))
    state, action = say(state, turn(Intent.CORRECTION), fix=field)
    assert asks_again(action, field)


@pytest.mark.parametrize("value", ["El celular está mal", "123", "El celular"])
def test_the_request_s_own_words_or_an_invalid_value_are_no_new_value(value: str):
    """Live run, 2026-10-22: the extractor read "El celular está mal" as the phone, and the
    summary came back unchanged."""
    state, _ = at_summary(voice=True)
    state, action = say(state, turn(Intent.CORRECTION, phone=value), fix=FieldName.PHONE)
    assert asks_again(action, FieldName.PHONE)


def test_the_field_s_name_read_as_the_place_is_no_place():
    """Live run, 2026-10-22: "La zona" (the answer to "¿qué dato?") came as the city, and was
    read back as somewhere outside the areas."""
    state, _ = at_summary()
    state, action = say(state, turn(Intent.CORRECTION, city="La zona"), fix=FieldName.CITY)
    assert asks_again(action, FieldName.CITY)


def test_a_yes_with_a_value_the_summary_has_qualifies():
    """Live run, 2026-10-22: "Sí, está correcto" came with the start date already there."""
    state, _ = at_summary(voice=True)
    state, action = say(state, turn(Intent.CONFIRM, start_date="2026-10-26"))
    assert isinstance(action, FindModes)
    assert state.outcome == Outcome.QUALIFIED


def test_a_yes_with_a_new_value_is_not_a_yes_yet():
    state, _ = at_summary()
    state, action = say(state, turn(Intent.CONFIRM, start_date="2026-10-28"))
    assert isinstance(action, ConfirmSummary)
    assert state.outcome is None


def test_an_empty_list_with_the_request_is_no_new_value():
    """Live run, 2026-10-22: "Las apps están mal" was read as no platforms."""
    state, _ = at_summary()
    state, action = say(state, turn(Intent.CORRECTION, platforms=[]), fix=FieldName.PLATFORMS)
    assert asks_again(action, FieldName.PLATFORMS)


def test_an_unsure_license_answer_with_the_request_asks_it_again():
    """Live run, 2026-10-22: "Quiero cambiar lo de la licencia" was read as a no at 0.5."""
    state, _ = at_summary(voice=True)
    unsure = turn(Intent.CORRECTION).model_copy(
        update={"fields": {"has_license": FieldCandidate(value=False, confidence=0.5)}}
    )
    state, action = say(state, unsure, fix=FieldName.HAS_LICENSE)
    assert asks_again(action, FieldName.HAS_LICENSE)


def test_a_question_naming_a_field_is_a_correction_not_an_faq_question():
    state, _ = at_summary()
    state, action = say(
        state, turn(Intent.QUESTION, question="¿Qué teléfono tienes?"), fix=FieldName.PHONE
    )
    assert asks_again(action, FieldName.PHONE)


def test_the_license_named_again_re_runs_its_gate():
    state, _ = at_summary()
    state, action = say(state, turn(Intent.CORRECTION), fix=FieldName.HAS_LICENSE)
    assert asks_again(action, FieldName.HAS_LICENSE)
    state, action = say(state, turn(has_license=False))
    assert isinstance(action, Disqualify)
    assert state.outcome == Outcome.DISQUALIFIED


def test_the_city_named_again_re_runs_its_gate():
    state, _ = at_summary()
    state, action = say(state, turn(Intent.CORRECTION), fix=FieldName.CITY)
    assert asks_again(action, FieldName.CITY)
    assert state.profile.place_said is None
    state, action = say(state, turn(city="Polanco"))
    assert isinstance(action, ConfirmSummary)
    assert state.profile.location_id == "mx-cdmx-mh"
    assert state.profile.place_said == "Polanco"


def test_a_new_city_with_the_request_is_applied():
    state, _ = at_summary()
    state, action = say(state, turn(Intent.CORRECTION, city="Polanco"), fix=FieldName.CITY)
    assert isinstance(action, ConfirmSummary)
    assert state.profile.location_id == "mx-cdmx-mh"


def test_platforms_with_no_experience_are_asked_through_the_years():
    fields = {k: v for k, v in FIELDS.items() if k != "platforms"}
    state, _ = say(at_license(), turn(**{**fields, "experience_years": 0}))
    state, action = say(state, turn(Intent.CORRECTION), fix=FieldName.PLATFORMS)
    assert asks_again(action, FieldName.EXPERIENCE_YEARS)
    state, action = say(state, turn(experience_years=2))
    assert isinstance(action, AskField)
    assert action.field == FieldName.PLATFORMS


def test_a_field_left_out_is_asked_again_when_named():
    state, _ = at_summary()
    state = state.model_copy(update={"skipped": (FieldName.SCHEDULE,)})
    state, action = say(state, turn(Intent.CORRECTION), fix=FieldName.SCHEDULE)
    assert asks_again(action, FieldName.SCHEDULE)
    assert FieldName.SCHEDULE not in state.skipped


def test_the_new_availability_replaces_the_old():
    state, _ = at_summary()
    state, _ = say(state, turn(Intent.CORRECTION), fix=FieldName.AVAILABILITY)
    state, _ = say(state, turn(availability=["weekends"]))
    assert state.profile.availability == [Availability.WEEKENDS]
    assert state.profile.schedule == [Schedule.MORNING]


# --- the same place explained ---


def explains(action: object, place: str) -> bool:
    return (
        isinstance(action, ConfirmValue)
        and action.field == FieldName.CITY
        and action.explain
        and action.location_id == CUAUH
        and action.value == place
    )


@pytest.mark.parametrize("fix", [FieldName.CITY, None])
def test_the_same_place_as_a_correction_is_explained(fix: FieldName | None):
    """ "La zona es Roma…" at the summary: Roma is in Cuauhtémoc, so that location is theirs."""
    state, _ = at_summary(voice=True)

    state, action = say(state, turn(Intent.CORRECTION, city="Roma"), fix=fix)

    assert explains(action, "Roma")
    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, ConfirmSummary)
    assert state.profile.location_id == CUAUH


def test_another_zone_of_the_same_location_is_explained_too():
    state, _ = at_summary()
    state, action = say(state, turn(Intent.CORRECTION, city="Condesa"))
    assert explains(action, "Condesa")


def test_a_question_about_the_place_is_explained():
    """ "¿La zona no es correcta?" is about their data, not the FAQ."""
    state, _ = at_summary()
    question = "¿La zona no es correcta?"
    state, action = say(state, turn(Intent.QUESTION, question=question), fix=FieldName.CITY)
    assert explains(action, "Roma")


def test_a_no_to_the_explanation_asks_the_city_again():
    state, _ = at_summary()
    state, _ = say(state, turn(Intent.CORRECTION, city="Roma"))
    state, action = say(state, turn(Intent.CORRECTION))
    assert asks_again(action, FieldName.CITY)
    assert state.profile.location_id is None


def test_another_place_after_the_explanation_is_applied():
    state, _ = at_summary()
    state, _ = say(state, turn(Intent.CORRECTION, city="Roma"))
    state, action = say(state, turn(Intent.CORRECTION, city="Polanco"))
    assert isinstance(action, ConfirmSummary)
    assert state.profile.location_id == "mx-cdmx-mh"


def test_a_yes_with_the_place_at_the_summary_is_a_yes():
    state, _ = at_summary()
    state, action = say(state, turn(Intent.CONFIRM, city="Roma"))
    assert not isinstance(action, ConfirmValue)


# --- the interview itself ---

SLOT = Slot(id="a", startsAt="2026-10-20T10:00:00-06:00", label="martes 20", location="C")


def booked_state(modes: tuple[InterviewMode, ...]) -> ConversationState:
    state, _ = at_summary()
    state, request = say(state, turn(Intent.CONFIRM))
    assert isinstance(request, FindModes)
    state, request = on_lookup(state, ModesFound(request=request, modes=modes))
    if not isinstance(request, FindSlots):
        state, request = say(state, turn().model_copy(update={"mode": InterviewMode.ONSITE}))
    assert isinstance(request, FindSlots)
    return book(state, request, SLOT, modes[0])


def book(
    state: ConversationState, request: FindSlots, slot: Slot, mode: InterviewMode
) -> ConversationState:
    """The times looked up for `request`, and `slot` picked and booked."""
    state, offer = on_lookup(state, SlotsFound(request=request, slots=(slot,)))
    assert isinstance(offer, OfferSlots)
    state, pick = say(state, turn().model_copy(update={"slot": slot.id}))
    assert isinstance(pick, BookSlot)
    done = Booked(
        slotId=slot.id,
        startsAt=slot.starts_at,
        label=slot.label,
        location="C",
        address="Calle 1",
        mode=mode,
    )
    state, action = on_lookup(state, SlotBooked(request=pick, booked=done))
    assert isinstance(action, ConfirmBooking)
    return state


def test_the_interview_time_named_after_booking_offers_new_times():
    state = booked_state((InterviewMode.ONSITE,))
    state, action = say(state, turn(Intent.UNCLEAR), fix=InterviewFix.TIME)
    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.RESCHEDULE
    assert state.booking is not None


def test_the_interview_mode_named_after_booking_offers_the_other_one():
    state = booked_state((InterviewMode.ONSITE, InterviewMode.VIDEO))
    state, action = say(state, turn(Intent.UNCLEAR), fix=InterviewFix.MODE)
    assert isinstance(action, FindSlots)
    assert action.mode == InterviewMode.VIDEO


def test_the_interview_time_named_before_any_interview_is_the_schedule():
    state, _ = at_summary()
    state, action = say(state, turn(Intent.CORRECTION), fix=InterviewFix.TIME)
    assert asks_again(action, FieldName.SCHEDULE)


# --- a detail corrected after booking ---


def test_a_detail_named_after_booking_is_asked_and_the_interview_stays():
    state = booked_state((InterviewMode.ONSITE,))
    state, action = say(state, turn(Intent.CORRECTION), fix=FieldName.SCHEDULE)
    assert asks_again(action, FieldName.SCHEDULE)
    assert state.awaiting == FieldName.SCHEDULE
    state, action = say(state, turn(schedule="evening"))
    assert isinstance(action, DetailsUpdated)
    assert action.changed
    assert state.profile.schedule == [Schedule.EVENING]
    assert state.booking is not None
    assert state.awaiting is None
    assert state.awaiting is None


def test_no_usable_value_after_booking_is_asked_once_more_then_left():
    state = booked_state((InterviewMode.ONSITE,))
    state, _ = say(state, turn(Intent.CORRECTION), fix=FieldName.EMAIL)
    state, action = say(state, turn(email="not-an-email"))
    assert asks_again(action, FieldName.EMAIL)
    state, action = say(state, turn(Intent.UNCLEAR))
    assert isinstance(action, DetailsUpdated)
    assert not action.changed
    assert state.profile.email == "j@example.com"


def test_no_license_after_booking_closes_and_drops_the_interview():
    state = booked_state((InterviewMode.ONSITE,))
    state, _ = say(state, turn(Intent.CORRECTION), fix=FieldName.HAS_LICENSE)
    state, action = say(state, turn(has_license=False))
    assert isinstance(action, Disqualify)
    assert state.outcome == Outcome.DISQUALIFIED
    assert state.booking is None


def test_another_location_after_booking_moves_only_on_a_yes():
    state = booked_state((InterviewMode.ONSITE,))
    state, action = say(state, turn(Intent.CORRECTION, city="Polanco"))
    assert isinstance(action, ConfirmMove)
    assert action.location_id == "mx-cdmx-mh"
    assert state.booking is not None

    kept, action = say(state, turn(Intent.CORRECTION))
    assert isinstance(action, DetailsUpdated)
    assert not action.changed
    assert kept.profile.location_id == CUAUH
    assert kept.booking is not None

    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, FindModes)
    assert state.booking is None
    assert state.profile.location_id == "mx-cdmx-mh"
    assert state.outcome == Outcome.QUALIFIED


# --- a calendar cancellation or a close in the middle of a correction (SAZ-87) ---

LATER = Slot(id="b", startsAt="2026-10-21T10:00:00-06:00", label="miércoles 21", location="C")


def moving() -> ConversationState:
    state, action = say(
        booked_state((InterviewMode.ONSITE,)), turn(Intent.CORRECTION, city="Polanco")
    )
    assert isinstance(action, ConfirmMove)
    return state


def amending() -> ConversationState:
    booked = booked_state((InterviewMode.ONSITE,))
    state, _ = say(booked, turn(Intent.CORRECTION), fix=FieldName.EMAIL)
    assert state.awaiting == FieldName.EMAIL
    return state


def rebooked(state: ConversationState) -> ConversationState:
    """The recruiter's calendar cancels the interview and the candidate books another time."""
    return book(*reoffer(state), LATER, InterviewMode.ONSITE)


def test_a_move_waiting_for_its_yes_is_dropped_by_a_calendar_cancellation():
    state = rebooked(moving())
    after, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, End)
    assert after.booking is not None
    assert after.booking.slot_id == LATER.id
    assert after.profile.location_id == CUAUH

    _, action = say(state, turn(Intent.QUESTION, question="¿Cuánto se cobra por hora?"))
    assert isinstance(action, AnswerQuestion)


def test_a_detail_being_corrected_is_dropped_by_a_calendar_cancellation():
    state, action = say(rebooked(amending()), turn(Intent.UNCLEAR))
    assert not asks_again(action, FieldName.EMAIL)
    assert state.awaiting is None


def interruptions(state: ConversationState) -> list[ConversationState]:
    """The state after an opt-out, an injection, a second insult and, with an interview
    booked, its cancellation."""
    insult = turn(Intent.ABUSE)
    insulted, _ = say(state, insult)
    closes = [
        say(state, turn(Intent.OPT_OUT))[0],
        say(state, turn(), suspicious=True)[0],
        say(insulted, insult)[0],
    ]
    if state.booking is not None:
        asked, _ = say(state, turn(), cancel_request=True)
        closes.append(say(asked, turn(Intent.CONFIRM))[0])
    return closes


@pytest.mark.parametrize("pending", [moving, amending])
def test_a_close_drops_a_pending_move_or_correction(pending):
    closes = interruptions(pending())
    assert closes[-1].outcome == Outcome.WITHDRAWN  # the interview cancelled
    for state in closes:
        assert state.stage == Stage.DONE
        assert state.booked is None  # closed: nothing left to move or correct
        assert state.awaiting is None


def test_a_close_drops_the_identity_question():
    match = DuplicateMatch(application_id="other", name="Jorge")
    state, _ = on_lookup(at_contact(), DuplicateChecked(match=match))
    assert state.pending_identity is not None
    for closed in interruptions(state):
        assert closed.stage == Stage.DONE
        assert closed.pending_identity is None
