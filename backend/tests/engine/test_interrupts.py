from datetime import date

import pytest

from sazon.agents.riders import Close, FieldName
from sazon.engine.interrupts import INTERRUPTS, Placement, interrupt
from sazon.engine.models import (
    Intent,
    Language,
    MatrixLanguage,
    Outcome,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.slots import Booked
from sazon.engine.state import (
    BOOKED,
    Asking,
    ChoosingMode,
    Closed,
    Contact,
    ConversationState,
    Disqualified,
    DisqualifyReason,
    DuplicateMatch,
    HandedOver,
    Interview,
    InterviewBooked,
    Offering,
    PhaseKind,
    Qualified,
    Summary,
    Where,
    where,
)
from sazon.engine.turn import CancelRequest, TurnInput

OPEN: frozenset[Where] = frozenset(PhaseKind) - {PhaseKind.CLOSED}
CONSENTED = OPEN - {PhaseKind.CONSENT}
OVER: frozenset[Where] = frozenset({PhaseKind.CLOSED, BOOKED})
AT_CITY = ConversationState(consented=True, phase=Asking(field=FieldName.CITY))


def turn(intent: Intent = Intent.ANSWER, *, injected: bool = False) -> TurnInput:
    extraction = TurnExtraction(
        intent=intent,
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
    )
    return TurnInput(extraction, date(2026, 10, 31), injected, None, None, CancelRequest())


def test_the_interrupts_run_in_one_order() -> None:
    assert [(each.name, each.placement) for each in INTERRUPTS] == [
        ("opt-out", Placement.BEFORE),
        ("injection", Placement.BEFORE),
        ("injection after close", Placement.BEFORE),
        ("abuse", Placement.BEFORE),
        ("opt-out after close", Placement.BEFORE),
        ("cancel request", Placement.BEFORE),
        ("pause answer", Placement.BEFORE),
        ("frustration", Placement.BEFORE),
        ("frustration after the answers", Placement.AFTER),
    ]


@pytest.mark.parametrize(
    ("name", "placement", "phases"),
    [
        ("opt-out", Placement.BEFORE, OPEN),
        ("injection", Placement.BEFORE, CONSENTED),
        ("injection after close", Placement.BEFORE, OVER),
        ("abuse", Placement.BEFORE, OPEN | OVER),
        ("opt-out after close", Placement.BEFORE, OVER),
        ("cancel request", Placement.BEFORE, CONSENTED | OVER),
        ("pause answer", Placement.BEFORE, CONSENTED - {PhaseKind.CONTACT}),
        ("frustration", Placement.BEFORE, {PhaseKind.INTERVIEW}),
        ("frustration after the answers", Placement.AFTER, {PhaseKind.ASKING, PhaseKind.SUMMARY}),
    ],
)
def test_each_interrupt_applies_to_its_phases(
    name: str, placement: Placement, phases: frozenset[Where]
) -> None:
    [found] = [each for each in INTERRUPTS if each.name == name and each.placement is placement]
    assert found.phases == phases


MATCH = DuplicateMatch(application_id="a1", name="Lucía")
BOOKING = Booked(
    slotId="s1",
    startsAt="2026-11-03T10:00:00+01:00",
    label="martes 3 de noviembre a las 10:00",
    location="Gràcia",
    address="Carrer Gran de Gràcia 1",
)


def consented(phase: object) -> ConversationState:
    return ConversationState.model_validate({"consented": True, "phase": phase})


@pytest.mark.parametrize(
    ("state", "at"),
    [
        (ConversationState(), PhaseKind.CONSENT),
        (ConversationState(phase=Closed(variant=HandedOver(application_id="a1"))), "closed"),
        (AT_CITY, PhaseKind.ASKING),
        (consented(Contact(match=MATCH)), PhaseKind.CONTACT),
        (consented(Summary()), PhaseKind.SUMMARY),
        (consented(Interview(sub=ChoosingMode())), PhaseKind.INTERVIEW),
        (consented(Interview(sub=Offering(booking=BOOKING))), PhaseKind.INTERVIEW),
        (consented(Interview(sub=InterviewBooked(booking=BOOKING))), BOOKED),
        (consented(Closed(variant=Qualified())), PhaseKind.CLOSED),
    ],
)
def test_a_booked_interview_gets_the_interrupts_of_a_closed_application(
    state: ConversationState, at: Where
) -> None:
    assert where(state) == at


def test_an_opt_out_with_an_injection_opts_out_while_open() -> None:
    state = AT_CITY
    closed, action = interrupt(state, turn(Intent.OPT_OUT, injected=True), at=PhaseKind.ASKING)
    assert action is not None
    assert closed.opted_out
    assert closed.outcome == Outcome.ABANDONED


def test_an_injection_after_a_close_leaves_a_disqualified_application_as_it_is() -> None:
    state = consented(Closed(variant=Disqualified(reason=Close.OUT_OF_AREA)))
    assert interrupt(state, turn(injected=True), at=PhaseKind.CLOSED) == (state, None)


def test_an_injection_with_the_interview_booked_closes_the_application() -> None:
    state = consented(Interview(sub=InterviewBooked(booking=BOOKING)))
    closed, action = interrupt(state, turn(injected=True), at=BOOKED)
    assert action is not None
    assert closed.disqualify_reason == DisqualifyReason.POLICY_VIOLATION
    assert closed.booking is None


def test_frustration_after_the_handler_is_not_checked_before_it() -> None:
    state = AT_CITY.model_copy(update={"frustration_streak": 2})
    assert interrupt(state, turn(), at=PhaseKind.ASKING) == (state, None)
    paused, action = interrupt(state, turn(), at=PhaseKind.ASKING, after=True)
    assert action is not None
    assert paused.pause_offered


def test_an_answer_to_the_pause_offer_that_carries_on_clears_it() -> None:
    state = AT_CITY.model_copy(update={"pause_offered": True})
    question = turn(Intent.QUESTION)
    carried_on, action = interrupt(state, question, at=PhaseKind.ASKING)
    assert action is None
    assert not carried_on.pause_offered
