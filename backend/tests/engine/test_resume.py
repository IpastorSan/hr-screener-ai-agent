"""Resuming an earlier application from its state: a closed one gets its status, an open one
its pending question, in the new conversation's register."""

from datetime import date

import pytest

from sazon.agents.riders import FieldName
from sazon.engine import (
    ApplicationStatus,
    AskField,
    ConversationState,
    CountrySource,
    FindModes,
    FindSlots,
    resume,
)
from sazon.engine.models import (
    CandidateProfile,
    InterviewMode,
    Language,
    Outcome,
    QualityFlag,
    Stage,
)
from sazon.engine.slots import Booked, OfferReason, Slot
from sazon.engine.state import (
    Asking,
    ChoosingMode,
    Closed,
    Contact,
    Interview,
    InterviewBooked,
    Offering,
    Qualified,
)

TODAY = date(2026, 10, 19)
PHONE = "+34612345678"
EMAIL = "ps@gmail.com"


def asks(action: object, field: FieldName) -> bool:
    return isinstance(action, AskField) and action.field == field and not action.again


BOOKED = Booked(
    slotId="s1",
    startsAt="2026-10-22T09:00-06:00",
    label="jueves 22 de octubre a las 09:00",
    location="Monterrey Centro",
    address="Av. Juárez 1",
)


def earlier(**update: object) -> ConversationState:
    """A Mexican application (usted), its country set by its location unless overridden."""
    base = ConversationState(
        phase=Closed(variant=Qualified()),
        consented=True,
        country="MX",
        country_source=CountrySource.LOCATION,
        language=Language.ES,
        profile=CandidateProfile(full_name="Carlos Navarro", location_id="mx-mty-centro"),
    )
    return base.model_copy(update=update)


def new_call(**update: object) -> ConversationState:
    """The new conversation that matched it: Spain, by voice."""
    state = ConversationState(
        phase=Contact(),
        consented=True,
        country="ES",
        country_source=CountrySource.PHONE,
        voice=True,
    )
    return state.model_copy(update=update)


@pytest.mark.parametrize(
    ("booking", "status"),
    [
        (BOOKED, BOOKED),
        (BOOKED.model_copy(update={"mode": InterviewMode.VIDEO}), "video"),
        (BOOKED.model_copy(update={"starts_at": "2026-10-12T09:00-06:00"}), None),
    ],
)
def test_a_closed_application_gets_its_status_in_its_location_s_register(booking, status):
    """QA batch 3: back through a Spanish chat, a Mexican application with its location
    chosen is spoken to in usted from the question about the match on, its status too, as
    an open one is. The language is still the new conversation's."""
    previous = earlier(phase=Interview(sub=InterviewBooked(booking=booking)))

    state, action = resume(previous, new_call(), today=TODAY)

    assert isinstance(action, ApplicationStatus)
    assert (action.language, action.country) == (Language.ES, "MX")
    if status is None:
        assert action.booking is None
    else:
        assert action.booking == booking
    assert state.outcome == Outcome.QUALIFIED


def test_without_a_location_the_new_conversation_decides_the_register():
    previous = earlier(
        phase=Interview(sub=InterviewBooked(booking=BOOKED)), country_source=CountrySource.POSTING
    )

    state, action = resume(previous, new_call(language=Language.EN), today=TODAY)

    assert isinstance(action, ApplicationStatus)
    assert (action.language, action.country) == (Language.EN, "ES")
    assert (state.language, state.country) == (Language.EN, "ES")


@pytest.mark.parametrize("profile", [{}, {"interview_mode": InterviewMode.VIDEO}])
def test_a_qualified_application_saved_without_an_interview_is_offered_times_again(profile):
    """Live test 4f64892c (SAZ-72): no time was free, or none suited, and the application was
    saved. Coming back is a new chance at an interview, not a goodbye."""
    base = earlier().profile.model_copy(update=profile)
    previous = earlier(phase=Closed(variant=Qualified(slots_declined=True)), profile=base)

    state, action = resume(previous, new_call(), today=TODAY)

    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.QUALIFIED
    assert (action.location_id, action.mode) == ("mx-mty-centro", base.interview_mode)
    assert (state.stage, state.outcome) == (Stage.SCHEDULING, Outcome.QUALIFIED)
    assert state.offering == Offering()


def test_a_qualified_application_closed_for_abuse_only_gets_its_status():
    previous = earlier(quality_flags=(QualityFlag.ABUSE,))
    _, action = resume(previous, new_call(), today=TODAY)
    assert isinstance(action, ApplicationStatus)


def test_an_open_application_asks_its_pending_question_rebuilt_from_its_state():
    previous = earlier(
        phase=Asking(field=FieldName.HAS_LICENSE),
        country_source=CountrySource.TIMEZONE,
        profile=CandidateProfile(full_name="Carlos Navarro", phone=PHONE, email=EMAIL),
        duplicate_checked=True,
        # The shape SAZ-47 wrote and SAZ-48 abandoned: a state saved before SAZ-48 still
        # resumes.
        read_back=(FieldName.PHONE, FieldName.EMAIL),
    )

    state, action = resume(previous, new_call(language=Language.EN), today=TODAY)

    assert asks(action, FieldName.HAS_LICENSE)
    assert (state.language, state.country) == (Language.EN, "ES")
    assert (action.language, action.country) == (Language.EN, "ES")
    assert state.voice


def test_an_open_application_keeps_the_country_its_location_set():
    profile = CandidateProfile(
        full_name="Carlos Navarro",
        phone="+523324789251",
        email=EMAIL,
        has_license=True,
        location_id="mx-mty-centro",
    )
    previous = earlier(
        phase=Asking(field=FieldName.AVAILABILITY),
        profile=profile,
        duplicate_checked=True,
    )
    state, action = resume(previous, new_call(), today=TODAY)
    assert state.country == "MX"
    assert asks(action, FieldName.AVAILABILITY)


def test_interview_times_on_offer_are_looked_up_again():
    slot = Slot(id="old", startsAt="2026-10-01T09:00-06:00", label="old", location="x")
    previous = earlier(phase=Interview(sub=Offering(slots=(slot,))))

    state, action = resume(previous, new_call(), today=TODAY)

    assert isinstance(action, FindSlots)
    assert action.reason == OfferReason.QUALIFIED
    assert state.offered_slots == ()


def test_a_qualified_application_still_choosing_the_mode_looks_the_modes_up():
    previous = earlier(phase=Interview(sub=ChoosingMode()), available_modes=())
    _, action = resume(previous, new_call(), today=TODAY)
    assert isinstance(action, FindModes)
