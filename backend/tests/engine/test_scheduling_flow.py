"""The engine's side of interview scheduling: the location's interview modes, slots and
bookings come in through `on_lookup` (`ModesFound`, `SlotsFound`, `SlotBooked`); the engine
only asks, offers, matches and confirms them."""

import re
from datetime import date, time
from pathlib import Path

import pytest

from sazon.agents.riders import FieldName
from sazon.engine import (
    AnswerQuestion,
    ApplicationSaved,
    AskCancel,
    AskMode,
    BookSlot,
    ConfirmBooking,
    ConfirmSummary,
    ConversationState,
    DeEscalate,
    Disqualify,
    DisqualifyReason,
    DuplicateChecked,
    End,
    EndReason,
    FindModes,
    FindSlots,
    ModesFound,
    OfferPause,
    OfferSlots,
    SlotBooked,
    SlotsFound,
    awaits_talent_pool_answer,
    current_prompt,
    on_lookup,
    resume,
    start,
    step,
)
from sazon.engine.models import (
    Intent,
    InterviewMode,
    Language,
    MatrixLanguage,
    Outcome,
    QualityFlag,
    Schedule,
    Sentiment,
    Stage,
    TurnExtraction,
)
from sazon.engine.slots import (
    CHANGE,
    NONE_WORK,
    Booked,
    Miss,
    OfferReason,
    Slot,
    SlotQuery,
    TimeRequest,
)
from sazon.engine.state import (
    ChoosingMode,
    Interview,
    Offering,
    Qualified,
)
from sazon.engine.ui import QuickReplies, Slots

TODAY = date(2026, 10, 19)
FAQ = Path(__file__).parents[2] / "src" / "sazon" / "rag" / "faq"
FIELDS = {
    "full_name": "Lucía Fernández",
    "phone": "612 345 678",
    "email": "lucia@example.com",
    "has_license": True,
    "city": "Gràcia",
    "availability": ["part_time"],
    "schedule": "tardes",
    "experience_years": 1,
    "platforms": ["Glovo"],
    "start_date": "2026-10-26",
}
FIRST = (
    Slot(id="a", startsAt="2026-10-20T16:00:00+02:00", label="martes 20 a las 16:00", location="G"),
    Slot(
        id="b", startsAt="2026-10-21T16:00:00+02:00", label="miércoles 21 a las 16:00", location="G"
    ),
    Slot(id="c", startsAt="2026-10-22T16:00:00+02:00", label="jueves 22 a las 16:00", location="G"),
)
MORE = (
    Slot(
        id="d", startsAt="2026-10-23T16:00:00+02:00", label="viernes 23 a las 16:00", location="G"
    ),
)


def offering(state: ConversationState) -> Offering:
    assert state.offering is not None, "the times are on offer"
    return state.offering


def choosing(state: ConversationState) -> ChoosingMode:
    assert isinstance(state.interview, ChoosingMode), "the mode is being chosen"
    return state.interview


def saved_without_interview(state: ConversationState) -> bool:
    """Qualified, and no time suited or none was free."""
    return isinstance(state.closed, Qualified) and state.closed.slots_declined


def turn(
    intent: Intent = Intent.ANSWER,
    *,
    slot: str | None = None,
    mode: str | None = None,
    question: str | None = None,
    **fields: object,
) -> TurnExtraction:
    return TurnExtraction.model_validate(
        {
            "intent": intent,
            "fields": {name: {"value": value, "confidence": 0.9} for name, value in fields.items()},
            "language": Language.ES,
            "matrix_language": MatrixLanguage.ES,
            "english_ratio": 0.0,
            "code_switched": False,
            "sentiment": Sentiment.NEUTRAL,
            "question": question,
            "slot": slot,
            "mode": mode,
        }
    )


def say(state: ConversationState, extraction: TurnExtraction):
    return step(state, extraction, today=TODAY)


ONSITE, VIDEO = InterviewMode.ONSITE, InterviewMode.VIDEO
BOTH = (ONSITE, VIDEO)


def booked(slot: Slot, mode: InterviewMode = ONSITE, meet_url: str | None = None) -> Booked:
    return Booked(
        slotId=slot.id,
        startsAt=slot.starts_at,
        label=slot.label,
        location="Sazón Barcelona Gràcia",
        address="Carrer de Verdi 45",
        mode=mode,
        meetUrl=meet_url,
    )


def confirmed() -> tuple[ConversationState, FindModes]:
    """Screened and confirmed: the engine asks which interview modes the location offers."""
    state, _ = start()
    state, _ = say(state, turn(Intent.CONFIRM))
    state, _ = say(state, turn(**FIELDS))
    state, action = on_lookup(state, DuplicateChecked(match=None))
    assert isinstance(action, ConfirmSummary)
    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, FindModes)
    return state, action


def qualified() -> tuple[ConversationState, FindSlots]:
    """Qualified at a location with in-person interviews only: straight to the slots."""
    state, request = confirmed()
    state, action = on_lookup(state, ModesFound(request=request, modes=(ONSITE,)))
    assert isinstance(action, FindSlots)
    return state, action


def asked_mode() -> tuple[ConversationState, AskMode]:
    state, request = confirmed()
    state, action = on_lookup(state, ModesFound(request=request, modes=BOTH))
    assert isinstance(action, AskMode)
    return state, action


def offered() -> ConversationState:
    state, request = qualified()
    state, action = on_lookup(state, SlotsFound(request=request, slots=FIRST))
    assert isinstance(action, OfferSlots)
    return state


def booked_state(slot: Slot = FIRST[0]) -> ConversationState:
    state, action = say(offered(), turn(slot=slot.id))
    assert isinstance(action, BookSlot)
    state, action = on_lookup(state, SlotBooked(request=action, booked=booked(slot)))
    assert isinstance(action, ConfirmBooking)
    return state


def test_qualifying_asks_the_service_for_slots_matching_the_profile():
    state, request = qualified()

    assert state.outcome == Outcome.QUALIFIED
    assert state.stage == Stage.SCHEDULING
    assert request.reason == OfferReason.QUALIFIED
    assert request.location_id == "es-bcn-gracia"
    assert request.schedule == (Schedule.AFTERNOON,)
    assert request.start_date == date(2026, 10, 26)
    assert request.exclude == ()
    assert current_prompt(state) is None  # the lookup is not a message


def test_offered_slots_come_only_from_the_service():
    state, request = qualified()

    state, action = on_lookup(state, SlotsFound(request=request, slots=FIRST))

    assert isinstance(action, OfferSlots)
    assert action.slots == FIRST
    assert action.reason == OfferReason.QUALIFIED
    assert isinstance(action.ui, Slots)
    assert [slot.id for slot in action.ui.slots] == ["a", "b", "c"]
    assert state.offered_slots == FIRST
    assert current_prompt(state) == action.model_copy(update={"reason": None})


def test_qualify_to_booked():
    state, action = say(offered(), turn(slot="b"))
    assert isinstance(action, BookSlot)
    assert action.slot_id == "b"
    assert state.booking is None

    state, action = on_lookup(state, SlotBooked(request=action, booked=booked(FIRST[1])))

    assert isinstance(action, ConfirmBooking)
    assert action.booking.slot_id == "b"
    assert action.booking.address == "Carrer de Verdi 45"
    assert action.ui == action.booking
    assert not action.rescheduled
    assert state.booking == booked(FIRST[1])
    assert state.outcome == Outcome.QUALIFIED
    assert state.stage == Stage.DONE
    assert state.offered_slots == ()
    assert current_prompt(state) is None


def test_a_slot_that_was_not_offered_is_never_booked():
    state, action = say(offered(), turn(slot="made-up-by-the-model"))
    assert isinstance(action, OfferSlots)
    assert action.retry == 1
    assert action.slots == FIRST
    assert state.booking is None


def test_an_unclear_reply_offers_the_same_slots_again():
    state, action = say(offered(), turn())
    assert isinstance(action, OfferSlots)
    assert action.retry == 1
    assert action.slots == FIRST
    assert offering(state).retries == 1


def test_a_question_is_answered_then_the_slots_offered_again():
    state, action = say(offered(), turn(Intent.QUESTION, question="¿Dónde es?"))
    assert isinstance(action, AnswerQuestion)
    assert isinstance(action.follow_up, OfferSlots)
    assert action.follow_up.slots == FIRST
    assert isinstance(action.ui, Slots)
    assert offering(state).retries == 0


def test_none_work_offers_more_once_then_saves_the_application():
    state, action = say(offered(), turn(slot=NONE_WORK))
    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.MORE
    assert set(action.exclude) == {"a", "b", "c"}

    state, action = on_lookup(state, SlotsFound(request=action, slots=MORE))
    assert isinstance(action, OfferSlots)
    assert action.reason == OfferReason.MORE
    assert action.slots == MORE

    state, action = say(state, turn(slot=NONE_WORK))
    assert isinstance(action, ApplicationSaved)
    assert not action.none_free
    assert action.ui is None
    assert saved_without_interview(state)
    assert state.stage == Stage.DONE
    assert state.outcome == Outcome.QUALIFIED
    assert state.booking is None
    assert not awaits_talent_pool_answer(state)

    # No question was asked: a "yes" afterwards is not an opt-in to anything.
    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, End)
    assert state.talent_pool_opt_in is None
    assert state.outcome == Outcome.QUALIFIED


@pytest.mark.parametrize(
    "question",
    [
        "¿Tenéis algún hueco por la tarde-noche?",
        "¿Hay algo después de las 19:00?",
        "¿Y el sábado?",
        "Do you have anything later in the day?",
    ],
)
def test_asking_for_other_times_with_none_work_is_not_an_faq_question(question: str):
    """The next offer answers it; the FAQ would say to ask at the interview, and log a miss."""
    state, action = say(offered(), turn(slot=NONE_WORK, question=question))
    assert isinstance(action, FindSlots)
    assert state.pending_question is None

    state, action = on_lookup(state, SlotsFound(request=action, slots=MORE))
    assert isinstance(action, OfferSlots)
    assert action.slots == MORE


def test_another_question_with_none_work_is_still_answered():
    state, action = say(offered(), turn(slot=NONE_WORK, question="¿Cuánto se cobra?"))
    assert isinstance(action, FindSlots)

    state, action = on_lookup(state, SlotsFound(request=action, slots=MORE))
    assert isinstance(action, AnswerQuestion)
    assert isinstance(action.follow_up, OfferSlots)


def test_no_free_slots_saves_the_application():
    state, request = qualified()

    state, action = on_lookup(state, SlotsFound(request=request, slots=()))

    assert isinstance(action, ApplicationSaved)
    assert action.none_free
    assert state.stage == Stage.DONE
    assert state.outcome == Outcome.QUALIFIED


def test_no_more_slots_after_none_work_saves_the_application():
    state, action = say(offered(), turn(slot=NONE_WORK))
    assert isinstance(action, FindSlots)

    state, action = on_lookup(state, SlotsFound(request=action, slots=()))

    assert isinstance(action, ApplicationSaved)
    assert not action.none_free


def test_a_slot_taken_meanwhile_is_reoffered_without_it():
    state, action = say(offered(), turn(slot="a"))
    assert isinstance(action, BookSlot)

    state, action = on_lookup(state, SlotBooked(request=action, booked=None))

    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.TAKEN
    assert action.exclude == ("a",)
    state, action = on_lookup(state, SlotsFound(request=action, slots=(*FIRST[1:], *MORE)))
    assert isinstance(action, OfferSlots)
    assert action.reason == OfferReason.TAKEN
    assert state.booking is None


def test_replying_after_booking_reschedules():
    state = booked_state(FIRST[0])

    state, action = say(state, turn(slot=CHANGE))

    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.RESCHEDULE
    assert action.exclude == ("a",)
    assert state.stage == Stage.SCHEDULING
    assert state.booking is not None

    state, action = on_lookup(state, SlotsFound(request=action, slots=FIRST[1:]))
    assert isinstance(action, OfferSlots)
    assert action.reason == OfferReason.RESCHEDULE
    state, action = say(state, turn(slot="c"))
    assert isinstance(action, BookSlot)
    state, action = on_lookup(state, SlotBooked(request=action, booked=booked(FIRST[2])))

    assert isinstance(action, ConfirmBooking)
    assert action.rescheduled
    assert state.booking is not None
    assert state.booking.slot_id == "c"
    assert state.stage == Stage.DONE


def test_a_correction_after_booking_also_reschedules():
    _, action = say(booked_state(), turn(Intent.CORRECTION))
    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.RESCHEDULE


def test_rescheduling_with_nothing_suitable_keeps_the_booking():
    state, action = say(booked_state(FIRST[0]), turn(slot=CHANGE))
    assert isinstance(action, FindSlots)
    state, action = on_lookup(state, SlotsFound(request=action, slots=FIRST[1:]))
    state, action = say(state, turn(slot=NONE_WORK))
    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.MORE
    assert set(action.exclude) == {"a", "b", "c"}
    state, action = on_lookup(state, SlotsFound(request=action, slots=MORE))
    state, action = say(state, turn(slot=NONE_WORK))

    assert isinstance(action, ConfirmBooking)
    assert action.kept
    assert state.booking is not None
    assert state.booking.slot_id == "a"
    assert state.stage == Stage.DONE
    assert not saved_without_interview(state)


def test_thanks_after_booking_closes_without_touching_it():
    state = booked_state()
    state, action = say(state, turn(Intent.SMALL_TALK))
    assert isinstance(action, End)
    assert state.booking is not None


def test_opting_out_after_booking_drops_the_booking():
    state, action = say(booked_state(), turn(Intent.OPT_OUT))
    assert isinstance(action, End)
    assert action.reason == EndReason.OPT_OUT
    assert state.booking is None
    assert state.outcome == Outcome.WITHDRAWN
    assert action.booking_cancelled
    assert action.cancelled == booked(FIRST[0])


# --- the candidate cancels the interview ---


def cancel(state: ConversationState, extraction: TurnExtraction):
    return step(state, extraction, today=TODAY, cancel_request=True)


def asked_to_cancel() -> ConversationState:
    """ "No puedo ir, cancela la entrevista" after booking: the question, no times."""
    state, action = cancel(booked_state(), turn(Intent.CORRECTION, slot=CHANGE))
    assert isinstance(action, AskCancel)
    return state


def test_a_cancel_request_after_booking_asks_whether_to_cancel_or_move_it():
    state, action = cancel(booked_state(), turn(Intent.CORRECTION, slot=CHANGE))

    assert isinstance(action, AskCancel)
    assert action.booking == booked(FIRST[0])
    assert isinstance(action.ui, QuickReplies)
    assert [o.value for o in action.ui.options] == ["cancel", "reschedule"]
    assert state.cancel_offered
    assert state.booking is not None
    assert state.stage == Stage.DONE


@pytest.mark.parametrize(
    ("extraction", "requested"),
    [(turn(Intent.CONFIRM), False), (turn(Intent.ANSWER), True), (turn(Intent.OPT_OUT), True)],
)
def test_a_yes_or_a_second_request_cancels_the_interview(extraction, requested):
    state = asked_to_cancel()

    state, action = step(state, extraction, today=TODAY, cancel_request=requested)

    assert isinstance(action, End)
    assert action.booking_cancelled
    assert action.cancelled == booked(FIRST[0])
    assert state.booking is None
    assert state.outcome == Outcome.WITHDRAWN
    assert state.stage == Stage.DONE
    assert not state.cancel_offered


def test_a_cancel_request_cancels_with_its_own_reason():
    _, action = say(asked_to_cancel(), turn(Intent.CONFIRM))
    assert isinstance(action, End)
    assert action.reason == EndReason.CANCELLED


def test_another_time_to_the_cancel_question_offers_new_times_and_keeps_the_booking():
    state, action = say(asked_to_cancel(), turn(slot=CHANGE))

    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.RESCHEDULE
    assert state.booking is not None
    assert not state.cancel_offered


def test_a_yes_that_asks_for_another_time_does_not_cancel():
    _, action = say(asked_to_cancel(), turn(Intent.CONFIRM, slot=CHANGE))
    assert isinstance(action, FindSlots)


def test_cant_make_it_without_a_cancel_word_still_offers_new_times():
    _, action = say(booked_state(), turn(Intent.CORRECTION, slot=CHANGE))
    assert isinstance(action, FindSlots)


def test_a_cancel_request_while_new_times_are_on_offer_asks_too():
    state, action = say(booked_state(FIRST[0]), turn(slot=CHANGE))
    assert isinstance(action, FindSlots)
    state, action = on_lookup(state, SlotsFound(request=action, slots=FIRST[1:]))
    assert isinstance(action, OfferSlots)

    state, action = cancel(state, turn(Intent.CORRECTION))
    assert isinstance(action, AskCancel)
    state, action = say(state, turn(Intent.CONFIRM))

    assert isinstance(action, End)
    assert action.reason == EndReason.CANCELLED
    assert state.booking is None
    assert state.offered_slots == ()
    assert state.stage == Stage.DONE
    assert state.outcome == Outcome.WITHDRAWN


def test_a_cancel_request_without_a_booking_asks_nothing():
    state, action = qualified()
    state, action = on_lookup(state, SlotsFound(request=action, slots=()))
    _, action = cancel(state, turn(Intent.CORRECTION))
    assert not isinstance(action, AskCancel)


def test_opting_out_while_new_times_are_on_offer_cancels_the_interview():
    """QA: "Ya no me interesa, cancela la entrevista" during a reschedule offer said goodbye and
    left the interview booked."""
    state, action = say(booked_state(FIRST[0]), turn(slot=CHANGE))
    assert isinstance(action, FindSlots)
    state, action = on_lookup(state, SlotsFound(request=action, slots=FIRST[1:]))
    assert state.stage == Stage.SCHEDULING

    state, action = cancel(state, turn(Intent.OPT_OUT))

    assert isinstance(action, End)
    assert action.reason == EndReason.OPT_OUT
    assert action.booking_cancelled
    assert action.cancelled == booked(FIRST[0])
    assert state.booking is None
    assert state.offered_slots == ()
    assert state.stage == Stage.DONE
    assert state.outcome == Outcome.WITHDRAWN


def test_opting_out_after_the_cancel_question_cancels_the_interview():
    state, action = say(asked_to_cancel(), turn(Intent.OPT_OUT))
    assert isinstance(action, End)
    assert action.reason == EndReason.OPT_OUT
    assert action.booking_cancelled
    assert state.booking is None
    assert state.outcome == Outcome.WITHDRAWN


def test_opting_out_at_the_first_times_offered_keeps_the_application_qualified():
    """No interview booked yet: nothing is withdrawn, the outcome stays as it was."""
    state, action = qualified()
    state, action = on_lookup(state, SlotsFound(request=action, slots=FIRST))
    assert state.booking is None

    state, action = say(state, turn(Intent.OPT_OUT))

    assert isinstance(action, End)
    assert action.reason == EndReason.OPT_OUT
    assert not action.booking_cancelled
    assert state.outcome == Outcome.QUALIFIED
    assert state.opted_out


# --- interview mode (SAZ-39) ---


def test_qualifying_looks_up_the_modes_before_any_slot():
    state, request = confirmed()

    assert request.location_id == "es-bcn-gracia"
    assert state.outcome == Outcome.QUALIFIED
    assert state.stage == Stage.MODE
    assert current_prompt(state) is None  # the lookup is not a message


def test_both_modes_on_offer_asks_which_one_with_buttons():
    state, action = asked_mode()

    assert action.options == BOTH
    assert action.qualified
    assert action.retry == 0
    assert isinstance(action.ui, QuickReplies)
    assert [option.value for option in action.ui.options] == ["onsite", "video"]
    assert [option.label for option in action.ui.options] == ["En persona", "Videollamada"]
    assert state.stage == Stage.MODE
    assert state.available_modes == BOTH
    assert state.profile.interview_mode is None
    assert state.offered_slots == ()
    # Shown again later (a nudge, a resumed chat), it is the bare question.
    assert current_prompt(state) == action.model_copy(update={"qualified": False})


def test_the_modes_come_in_a_fixed_order_whatever_the_service_says():
    state, request = confirmed()
    state, action = on_lookup(state, ModesFound(request=request, modes=(VIDEO, ONSITE)))
    assert isinstance(action, AskMode)
    assert action.options == BOTH


@pytest.mark.parametrize("only", [ONSITE, VIDEO])
def test_a_single_mode_is_not_asked(only: InterviewMode):
    state, request = confirmed()

    state, action = on_lookup(state, ModesFound(request=request, modes=(only,)))

    assert isinstance(action, FindSlots)
    assert action.mode == only
    assert action.reason == OfferReason.QUALIFIED
    assert state.profile.interview_mode == only
    assert state.stage == Stage.SCHEDULING
    state, offer = on_lookup(state, SlotsFound(request=action, slots=FIRST))
    assert isinstance(offer, OfferSlots)
    assert offer.mode == only


def test_no_recruiter_at_all_looks_for_in_person_times_and_saves_the_application():
    state, request = confirmed()
    state, action = on_lookup(state, ModesFound(request=request, modes=()))
    assert isinstance(action, FindSlots)
    assert action.mode == ONSITE
    state, action = on_lookup(state, SlotsFound(request=action, slots=()))
    assert isinstance(action, ApplicationSaved)
    assert action.none_free


@pytest.mark.parametrize(("hint", "mode"), [("video", VIDEO), ("onsite", ONSITE)])
def test_choosing_a_mode_finds_slots_for_it(hint: str, mode: InterviewMode):
    state, _ = asked_mode()

    state, action = say(state, turn(mode=hint))

    assert isinstance(action, FindSlots)
    assert action.mode == mode
    assert action.reason == OfferReason.MODE
    assert action.schedule == (Schedule.AFTERNOON,)
    assert state.profile.interview_mode == mode
    assert state.stage == Stage.SCHEDULING
    state, offer = on_lookup(state, SlotsFound(request=action, slots=FIRST))
    assert isinstance(offer, OfferSlots)
    assert offer.mode == mode
    assert offer.reason == OfferReason.MODE
    assert offering(state).offers == 1


def test_a_mode_hint_in_words_is_read_as_the_mode():
    state, _ = asked_mode()
    state, action = say(state, turn(mode="por videollamada"))
    assert isinstance(action, FindSlots)
    assert action.mode == VIDEO


@pytest.mark.parametrize("hint", [None, "phone", "both"])
def test_no_mode_on_offer_asks_again(hint: str | None):
    state, _ = asked_mode()

    state, action = say(state, turn(mode=hint))

    assert isinstance(action, AskMode)
    assert action.retry == 1
    assert not action.qualified
    assert choosing(state).retries == 1
    assert state.profile.interview_mode is None
    state, action = say(state, turn())
    assert isinstance(action, AskMode)
    assert action.retry == 2


def test_a_question_at_the_mode_choice_is_answered_then_asked_again():
    state, _ = asked_mode()
    state, action = say(state, turn(Intent.QUESTION, question="¿Cuánto dura?"))
    assert isinstance(action, AnswerQuestion)
    assert isinstance(action.follow_up, AskMode)
    assert isinstance(action.ui, QuickReplies)
    assert choosing(state).retries == 0


def test_a_mode_and_a_question_together_find_slots_then_answer():
    state, _ = asked_mode()
    state, action = say(state, turn(Intent.ANSWER, mode="video", question="¿Cuánto dura?"))
    assert isinstance(action, FindSlots)
    assert state.pending_question is not None
    state, action = on_lookup(state, SlotsFound(request=action, slots=FIRST))
    assert isinstance(action, AnswerQuestion)
    assert isinstance(action.follow_up, OfferSlots)


def test_abuse_at_the_mode_choice_repeats_the_question():
    state, _ = asked_mode()
    state, action = say(state, turn(Intent.ABUSE))
    assert isinstance(action, DeEscalate)
    assert isinstance(action.follow_up, AskMode)


def video_offered() -> ConversationState:
    state, _ = asked_mode()
    state, action = say(state, turn(mode="video"))
    assert isinstance(action, FindSlots)
    state, action = on_lookup(state, SlotsFound(request=action, slots=FIRST))
    assert isinstance(action, OfferSlots)
    return state


def test_switching_mode_before_booking_offers_slots_for_the_new_mode():
    state = video_offered()

    state, action = say(state, turn(Intent.CORRECTION, mode="onsite"))

    assert isinstance(action, FindSlots)
    assert action.mode == ONSITE
    assert action.reason == OfferReason.MODE
    assert action.exclude == ()
    assert state.profile.interview_mode == ONSITE
    assert state.offered_slots == ()
    assert offering(state).offers == 0
    state, action = on_lookup(state, SlotsFound(request=action, slots=MORE))
    assert isinstance(action, OfferSlots)
    assert action.mode == ONSITE
    state, action = say(state, turn(slot="d"))
    assert isinstance(action, BookSlot)


def test_the_same_mode_again_is_not_a_switch():
    state = video_offered()
    state, action = say(state, turn(mode="video"))
    assert isinstance(action, OfferSlots)
    assert action.retry == 1
    assert state.offered_slots == FIRST


def test_a_mode_that_is_not_on_offer_is_ignored():
    state, request = qualified()  # in person only
    state, _ = on_lookup(state, SlotsFound(request=request, slots=FIRST))

    state, action = say(state, turn(mode="video"))

    assert isinstance(action, OfferSlots)
    assert action.retry == 1
    assert state.profile.interview_mode == ONSITE


def test_a_video_booking_is_confirmed_with_its_mode_and_link():
    state = video_offered()
    state, action = say(state, turn(slot="a"))
    assert isinstance(action, BookSlot)
    link = "https://meet.google.com/abc-defg-hij"

    state, action = on_lookup(
        state, SlotBooked(request=action, booked=booked(FIRST[0], VIDEO, link))
    )

    assert isinstance(action, ConfirmBooking)
    assert action.booking.mode == VIDEO
    assert action.booking.meet_url == link
    assert action.ui == action.booking
    assert action.has_email
    assert state.profile.interview_mode == VIDEO


def test_switching_mode_after_booking_moves_the_interview():
    state = video_offered()
    state, action = say(state, turn(slot="a"))
    assert isinstance(action, BookSlot)
    state, _ = on_lookup(state, SlotBooked(request=action, booked=booked(FIRST[0], VIDEO)))

    state, action = say(state, turn(Intent.CORRECTION, mode="onsite"))

    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.MODE
    assert action.mode == ONSITE
    assert action.exclude == ("a",)
    assert state.stage == Stage.SCHEDULING
    state, action = on_lookup(state, SlotsFound(request=action, slots=FIRST[1:]))
    state, action = say(state, turn(slot="b"))
    assert isinstance(action, BookSlot)
    state, action = on_lookup(state, SlotBooked(request=action, booked=booked(FIRST[1], ONSITE)))
    assert isinstance(action, ConfirmBooking)
    assert action.rescheduled
    assert action.booking.mode == ONSITE


def test_a_switch_after_booking_with_no_time_keeps_the_booking_and_its_mode():
    state = video_offered()
    state, action = say(state, turn(slot="a"))
    assert isinstance(action, BookSlot)
    state, _ = on_lookup(state, SlotBooked(request=action, booked=booked(FIRST[0], VIDEO)))
    state, action = say(state, turn(mode="onsite"))
    assert isinstance(action, FindSlots)

    state, action = on_lookup(state, SlotsFound(request=action, slots=()))

    assert isinstance(action, ConfirmBooking)
    assert action.kept
    assert state.booking is not None
    assert state.booking.mode == VIDEO
    assert state.profile.interview_mode == VIDEO


def test_a_question_while_the_modes_are_looked_up_is_answered_with_the_mode_question():
    state, _ = confirmed()  # the lookup did not come back (the turn failed)
    state, request = say(state, turn(Intent.QUESTION, question="¿Hay que llevar algo?"))
    assert isinstance(request, FindModes)
    assert state.pending_question is not None

    state, action = on_lookup(state, ModesFound(request=request, modes=BOTH))

    assert isinstance(action, AnswerQuestion)
    assert action.question == "¿Hay que llevar algo?"
    assert isinstance(action.follow_up, AskMode)
    assert action.follow_up.qualified
    assert state.pending_question is None


def test_without_an_email_the_video_confirmation_knows_it():
    state, _ = start()
    state, _ = say(state, turn(Intent.CONFIRM))
    fields = {**FIELDS}
    del fields["email"]
    state, _ = say(state, turn(**fields))
    state = state.model_copy(update={"skipped": (*state.skipped, FieldName.EMAIL)})
    state, action = on_lookup(state, DuplicateChecked(match=None))
    assert isinstance(action, ConfirmSummary)
    state, request = say(state, turn(Intent.CONFIRM))
    assert isinstance(request, FindModes)
    state, request = on_lookup(state, ModesFound(request=request, modes=(VIDEO,)))
    assert isinstance(request, FindSlots)
    state, _ = on_lookup(state, SlotsFound(request=request, slots=FIRST))
    state, book = say(state, turn(slot="a"))
    assert isinstance(book, BookSlot)

    state, action = on_lookup(state, SlotBooked(request=book, booked=booked(FIRST[0], VIDEO)))

    assert isinstance(action, ConfirmBooking)
    assert not action.has_email


# --- insults after booking (SAZ-58) ---

REGISTERS = [(Language.ES, "ES"), (Language.ES, "MX"), (Language.EN, "ES")]


@pytest.mark.parametrize(("language", "country"), REGISTERS)
def test_after_booking_a_first_insult_gets_a_calm_line_and_a_second_cancels_the_interview(
    language: Language, country: str
):
    register = {"language": language, "country": country, "language_locked": True}
    state = booked_state().model_copy(update=register)
    assert state.stage == Stage.DONE

    state, action = say(state, turn(Intent.ABUSE))

    assert isinstance(action, DeEscalate)
    assert action.closed
    assert action.follow_up is None
    assert (action.language, action.country) == (language, country)
    assert state.abuse_strikes == 1
    assert state.booking is not None

    state, action = say(state, turn(Intent.ABUSE))

    assert isinstance(action, End)
    assert action.reason == EndReason.ABUSE
    assert action.booking_cancelled
    assert (action.language, action.country) == (language, country)
    assert state.booking is None  # the service cancels it
    assert QualityFlag.ABUSE in state.quality_flags
    assert state.outcome == Outcome.QUALIFIED  # for a recruiter to review
    assert state.stage == Stage.DONE


def test_strikes_before_the_booking_count_after_it():
    state, action = say(offered(), turn(Intent.ABUSE))
    assert isinstance(action, DeEscalate)
    assert not action.closed
    state, action = say(state, turn(slot="a"))
    assert isinstance(action, BookSlot)
    state, _ = on_lookup(state, SlotBooked(request=action, booked=booked(FIRST[0])))

    state, action = say(state, turn(Intent.ABUSE))

    assert isinstance(action, End)
    assert action.booking_cancelled
    assert state.booking is None


def test_a_second_insult_while_rescheduling_cancels_the_interview_too():
    state, action = say(booked_state(), turn(Intent.ABUSE))
    state = state.at(Interview(sub=Offering(booking=state.booking)))  # asked for another time

    state, action = say(state, turn(Intent.ABUSE))

    assert isinstance(action, End)
    assert action.booking_cancelled
    assert state.booking is None
    assert state.offered_slots == ()
    assert state.stage == Stage.DONE


def test_insults_after_a_close_without_a_booking_keep_its_outcome():
    state, request = qualified()
    state, action = on_lookup(state, SlotsFound(request=request, slots=()))
    assert isinstance(action, ApplicationSaved)

    state, action = say(state, turn(Intent.ABUSE))
    assert isinstance(action, DeEscalate)
    state, action = say(state, turn(Intent.ABUSE))

    assert isinstance(action, End)
    assert action.reason == EndReason.ABUSE
    assert not action.booking_cancelled
    assert state.outcome == Outcome.QUALIFIED
    assert QualityFlag.ABUSE in state.quality_flags


# --- an injection after booking (SAZ-41) ---


@pytest.mark.parametrize("intent", [Intent.ANSWER, Intent.CORRECTION, Intent.QUESTION])
def test_an_injection_after_booking_closes_the_application_and_drops_the_interview(intent):
    state = booked_state()
    assert state.stage == Stage.DONE
    assert state.booking is not None

    state, action = step(state, turn(intent), today=TODAY, suspicious=True)

    assert isinstance(action, Disqualify)
    assert action.reason == DisqualifyReason.POLICY_VIOLATION
    assert state.outcome == Outcome.DISQUALIFIED
    assert state.booking is None
    assert state.offered_slots == ()


def test_an_injection_while_slots_are_offered_drops_them():
    state, action = step(offered(), turn(), today=TODAY, suspicious=True)

    assert isinstance(action, Disqualify)
    assert state.outcome == Outcome.DISQUALIFIED
    assert state.offered_slots == ()


# --- times asked about, words that fit several times, a time not on offer (QA batch 2) ---


AFTERNOON = SlotQuery(part="afternoon")
FRIDAY = SlotQuery(weekdays=(4,))


def test_words_that_fit_several_times_ask_which_and_book_nothing():
    """ "A las cuatro" with three times at 16:00: the extractor's pick never books one."""
    request = TimeRequest(matches=("a", "c"))
    state, action = step(offered(), turn(), today=TODAY, time_request=request)

    assert isinstance(action, OfferSlots)
    assert action.reason == OfferReason.WHICH
    assert [slot.id for slot in action.slots] == ["a", "c"]
    assert state.offered_slots == (FIRST[0], FIRST[2])
    assert state.booking is None


def test_words_that_fit_several_times_again_get_the_buttons_never_the_same_question():
    """Production loop: "¿Te va bien el martes 27 o el martes 3?" → "El martes 27 está bien."
    read as both → "Hay más de uno así" again and again. The second time the buttons and a
    one-line ask, counted as a retry; a new offer asks which again."""
    request = TimeRequest(matches=("a", "c"))
    state, action = step(offered(), turn(), today=TODAY, time_request=request)
    assert isinstance(action, OfferSlots)
    assert action.reason == OfferReason.WHICH

    for retry in (1, 2):
        state, action = step(state, turn(), today=TODAY, time_request=request)
        assert isinstance(action, OfferSlots)
        assert action.reason is None
        assert action.pick
        assert action.retry == retry
        assert offering(state).retries == retry
        assert [slot.id for slot in action.slots] == ["a", "c"]
        assert isinstance(action.ui, Slots)

    state, action = step(state, turn(slot="c"), today=TODAY)
    assert isinstance(action, BookSlot)
    assert action.slot_id == "c"

    state, action = on_lookup(
        state, SlotBooked(request=action, booked=None)
    )  # taken meanwhile: a new offer
    assert isinstance(action, FindSlots)
    state, action = on_lookup(state, SlotsFound(request=action, slots=FIRST))
    assert not offering(state).which_asked
    _, action = step(state, turn(), today=TODAY, time_request=request)
    assert isinstance(action, OfferSlots)
    assert action.reason == OfferReason.WHICH


def test_a_time_none_of_the_slots_has_is_asked_again():
    _, action = step(offered(), turn(), today=TODAY, time_request=TimeRequest(unmatched_time=True))

    assert isinstance(action, OfferSlots)
    assert action.retry == 1
    assert action.slots == FIRST


def test_a_question_about_other_times_is_looked_up_not_answered_from_the_faq():
    question = turn(Intent.QUESTION, question="¿Tienes algo el viernes?")
    state, action = step(offered(), question, today=TODAY, time_request=TimeRequest(query=FRIDAY))

    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.ASKED
    assert action.query == FRIDAY
    assert action.avoid_days == ()
    assert state.pending_question is None
    assert state.declined_slots == ()  # asking is not turning them down

    state, action = on_lookup(state, SlotsFound(request=action, slots=MORE))
    assert isinstance(action, OfferSlots)
    assert action.reason == OfferReason.ASKED
    assert action.matched
    assert offering(state).offers == 1  # not a round turned down


def test_nothing_fitting_the_question_offers_the_nearest_and_says_so():
    state, action = step(offered(), turn(), today=TODAY, time_request=TimeRequest(query=FRIDAY))
    assert isinstance(action, FindSlots)

    state, action = on_lookup(state, SlotsFound(request=action, slots=MORE, miss=Miss.NONE))

    assert isinstance(action, OfferSlots)
    assert not action.matched
    assert action.slots == MORE


def test_nothing_free_at_all_for_the_question_keeps_the_offer():
    state, action = step(offered(), turn(), today=TODAY, time_request=TimeRequest(query=FRIDAY))
    assert isinstance(action, FindSlots)

    state, action = on_lookup(state, SlotsFound(request=action, slots=()))

    assert isinstance(action, OfferSlots)
    assert not action.matched
    assert action.miss == Miss.LEFT  # "no me quedan más", never "otros cercanos" for the same
    assert action.slots == FIRST
    assert state.stage == Stage.SCHEDULING


# --- the call of 2026-10-22: other times never repeat, a time asked about, by voice ---

TUESDAY_27 = SlotQuery(days=(date(2026, 10, 27),))
NEXT = (
    Slot(id="e", startsAt="2026-10-27T10:15:00+01:00", label="martes 27 a las 10:15", location="G"),
    Slot(id="f", startsAt="2026-10-27T10:30:00+01:00", label="martes 27 a las 10:30", location="G"),
    Slot(id="g", startsAt="2026-10-27T10:45:00+01:00", label="martes 27 a las 10:45", location="G"),
)


def ask_times(state: ConversationState, query: SlotQuery, question: str = "¿Y?"):
    extraction = turn(Intent.QUESTION, question=question)
    return step(state, extraction, today=TODAY, time_request=TimeRequest(query=query))


def test_other_times_never_include_any_time_offered_so_far():
    """ "¿Qué otros huecos tienes el martes 27?" got 10:15 and 10:30 again: only the first
    offer was excluded."""
    state, action = ask_times(offered(), TUESDAY_27)
    assert isinstance(action, FindSlots)
    assert set(action.exclude) == {"a", "b", "c"}
    state, _ = on_lookup(state, SlotsFound(request=action, slots=NEXT))

    state, action = ask_times(state, TUESDAY_27.model_copy(update={"other": True}))

    assert isinstance(action, FindSlots)
    assert set(action.exclude) == {"a", "b", "c", "e", "f", "g"}
    assert state.pending_question is None  # looked up, never the FAQ


def test_by_voice_only_the_times_read_aloud_count_as_offered():
    """A call reads two of the three times: the third can still come in the next offer."""
    state = offered().model_copy(update={"voice": True})
    state, action = ask_times(state, TUESDAY_27)
    assert isinstance(action, FindSlots)
    state, _ = on_lookup(state, SlotsFound(request=action, slots=NEXT))

    _, action = ask_times(state, SlotQuery(other=True))

    assert isinstance(action, FindSlots)
    assert {"e", "f"} <= set(action.exclude)
    assert "g" not in action.exclude


def test_other_options_keep_to_the_day_asked_about():
    """ "¿Y otras opciones?" after "el martes 27": more of the 27th, until it has none left."""
    state, action = ask_times(offered(), TUESDAY_27)
    assert isinstance(action, FindSlots)
    state, _ = on_lookup(state, SlotsFound(request=action, slots=NEXT))

    state, action = ask_times(state, SlotQuery(other=True), "¿Y otras opciones?")

    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.ASKED
    assert action.query == TUESDAY_27.model_copy(update={"other": True})
    assert state.pending_question is None


def test_a_time_asked_about_may_be_one_offered_before_but_never_one_turned_down():
    state, action = say(offered(), turn(slot=NONE_WORK))
    assert isinstance(action, FindSlots)
    state, _ = on_lookup(state, SlotsFound(request=action, slots=NEXT))

    _, action = ask_times(state, SlotQuery(days=(date(2026, 10, 27),), at=(time(10, 30),)))

    assert isinstance(action, FindSlots)
    assert set(action.exclude) == {"a", "b", "c"}


def test_a_time_that_is_not_free_is_said_by_the_lookup():
    query = SlotQuery(days=(date(2026, 10, 27),), at=(time(12),))
    state, action = ask_times(offered(), query, "¿Podría ser a las 12?")
    assert isinstance(action, FindSlots)

    state, action = on_lookup(state, SlotsFound(request=action, slots=NEXT, miss=Miss.TIME))

    assert isinstance(action, OfferSlots)
    assert action.reason == OfferReason.ASKED
    assert action.miss == Miss.TIME
    assert not action.matched


def test_booking_starts_the_times_offered_again():
    state = booked_state()
    assert state.shown_slots == ()
    assert state.slot_query is None


def test_none_of_these_with_a_time_of_day_looks_that_up_and_declines_them():
    extraction = turn(slot=NONE_WORK, question="¿Tienes algo por la tarde?")
    request = TimeRequest(query=AFTERNOON)
    state, action = step(offered(), extraction, today=TODAY, time_request=request)

    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.ASKED
    assert action.query == AFTERNOON
    assert set(action.exclude) == {"a", "b", "c"}
    assert action.avoid_days == ()  # the same days at another time of day are fine (D9)
    assert [slot.id for slot in state.declined_slots] == ["a", "b", "c"]


def test_a_second_round_moves_to_other_days_and_never_repeats_a_declined_time():
    """D9: never the same days fifteen minutes later."""
    _, action = say(offered(), turn(slot=NONE_WORK))

    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.MORE
    assert action.avoid_days == (date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22))


def test_declined_times_are_not_offered_again_on_return():
    state, action = say(offered(), turn(slot=NONE_WORK))
    assert isinstance(action, FindSlots)
    state, action = on_lookup(state, SlotsFound(request=action, slots=MORE))
    state, action = say(state, turn(slot=NONE_WORK))
    assert isinstance(action, ApplicationSaved)

    _, action = resume(state, state, today=TODAY)

    assert isinstance(action, FindSlots)
    assert set(action.exclude) == {"a", "b", "c", "d"}
    assert date(2026, 10, 23) in action.avoid_days


@pytest.mark.parametrize("mode", [VIDEO, ONSITE])
def test_a_question_after_booking_says_which_mode_the_interview_is(mode: InterviewMode):
    """QA batch 2: "¿cómo va a ser la entrevista?" after a video booking was answered "en el
    local" from the FAQ: the answer is told the booked mode."""
    state, action = say(offered(), turn(slot="a"))
    assert isinstance(action, BookSlot)
    state, _ = on_lookup(state, SlotBooked(request=action, booked=booked(FIRST[0], mode)))

    _, action = say(state, turn(Intent.QUESTION, question="¿Cómo va a ser la entrevista?"))

    assert isinstance(action, AnswerQuestion)
    assert action.interview_mode == mode


def test_a_question_before_booking_has_no_interview_mode():
    _, action = say(offered(), turn(Intent.QUESTION, question="¿Cómo va a ser la entrevista?"))
    assert isinstance(action, AnswerQuestion)
    assert action.interview_mode is None


@pytest.mark.parametrize("language", ["es", "en"])
def test_the_hiring_steps_faq_names_neither_mode_alone(language: str):
    """Read after a video booking too: it never says the interview is at the location."""
    text = (FAQ / language / "hiring-steps.md").read_text().split("---")[-1].lower()
    assert "en el local" not in text
    assert "at the location" not in text
    assert re.search(r"videollamada|video call", text)


def frustrated(extraction: TurnExtraction | None = None) -> TurnExtraction:
    return (extraction or turn()).model_copy(update={"sentiment": Sentiment.FRUSTRATED})


def test_frustration_at_the_mode_question_brings_the_pause_offer():
    state, _ = asked_mode()
    state, action = say(state, frustrated())
    assert isinstance(action, AskMode)
    assert action.simplify

    state, action = say(state, frustrated())

    assert isinstance(action, OfferPause)
    assert QualityFlag.FRUSTRATION in state.quality_flags
    assert state.pause_offered

    # "No, sigamos": the mode question again, not the summary.
    state, action = say(state, turn())
    assert isinstance(action, AskMode)
    assert state.stage == Stage.MODE


def test_frustration_at_the_times_brings_the_pause_offer():
    state, action = say(offered(), frustrated())
    assert isinstance(action, OfferSlots)

    state, action = say(state, frustrated())

    assert isinstance(action, OfferPause)
    assert QualityFlag.FRUSTRATION in state.quality_flags

    state, action = say(state, turn(Intent.CONFIRM))
    assert isinstance(action, End)
    assert action.reason == EndReason.PAUSED
    assert state.booking is None


def test_a_frustrated_pick_is_booked_not_paused():
    state, _ = say(offered(), frustrated())
    _, action = say(state, frustrated(turn(slot="b")))
    assert isinstance(action, BookSlot)

    state, _ = asked_mode()
    state, _ = say(state, frustrated())
    _, action = say(state, frustrated(turn(mode="video")))
    assert isinstance(action, FindSlots)


def test_moving_a_booking_to_the_day_asked_about_looks_it_up():
    extraction = turn(Intent.CORRECTION, slot=CHANGE)
    request = TimeRequest(query=FRIDAY)
    _, action = step(booked_state(), extraction, today=TODAY, time_request=request)

    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.RESCHEDULE
    assert action.query == FRIDAY
    assert "a" in action.exclude
