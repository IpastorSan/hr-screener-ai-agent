"""The interrupts: checks that may take a candidate turn from the phase's own handling.

They run in the order of `INTERRUPTS` and the first that decides the turn wins, so the order
is the policy, for an open application and a closed one alike. Each declares the phases it
applies to and whether it runs before the phase's handler or after it, which keeps two
wrinkles of the flow: frustration is checked before the handler at the mode and the times but
after the fields are stored while asking (an angry message that also answers keeps the
answer), and once the screening is over an opt-out matters only with an interview booked, so
it comes after an injection and insults there but before them while the application is open.

A booked interview (`BOOKED`) gets the interrupts of a closed application, not those of the
interview being chosen: the screening is over, and nothing is asked of the candidate.
"""

from collections.abc import Callable
from enum import StrEnum
from typing import NamedTuple

from sazon.engine.actions import AskCancel, DeEscalate, End, EndReason, OfferPause
from sazon.engine.models import Intent, Outcome, QualityFlag, Stage, TurnExtraction
from sazon.engine.screening import _next_step, _refused_field
from sazon.engine.slots import CHANGE, NONE_WORK, Booked
from sazon.engine.spec import FieldType, agent
from sazon.engine.state import (
    BOOKED,
    Abandoned,
    CancelConfirm,
    Closed,
    ConversationState,
    DeclinedConsent,
    Idle,
    Interview,
    InterviewBooked,
    Node,
    Offering,
    PhaseKind,
    Qualified,
    Where,
    Withdrawn,
    add_quality_flag,
)
from sazon.engine.states import current_prompt, interview
from sazon.engine.states.handler import Edge
from sazon.engine.states.interview import _interview_answer
from sazon.engine.turn import (
    ANSWERING_INTENTS,
    MAX_ABUSE_STRIKES,
    CancelRequest,
    Interrupted,
    Turn,
    TurnInput,
    _close,
    _known_fields,
    _meta,
    _reject,
    _wrap,
)
from sazon.engine.ui import cancel_ui, yes_no_ui

PAUSE_AFTER_FRUSTRATED_TURNS = 2


class Placement(StrEnum):
    BEFORE = "before"
    AFTER = "after"
    """Once the handler has stored the fields, before it asks the next question."""


class Interrupt(NamedTuple):
    name: str
    phases: frozenset[Where]
    placement: Placement
    run: Callable[[ConversationState, TurnInput], Interrupted]
    edges: frozenset[Edge] = frozenset()
    """The transitions it may take, from the sub-states of `phases`; none when the turn it
    takes stays where it is (the pause offer and its answer, a first insult)."""


def _opting_out(state: ConversationState, turn: TurnInput) -> Interrupted:
    # "No quiero dar mis datos de contacto" refuses one value, it does not stop the application.
    refused = _refused_field(state, turn.extraction, state.awaiting)
    contact = refused is not None and agent().field(refused).type == FieldType.CONTACT
    if turn.extraction.intent == Intent.OPT_OUT and not contact:
        return _opt_out(state)
    return state, None


def _injection(state: ConversationState, turn: TurnInput) -> Interrupted:
    """A manipulation attempt closes the application, once consent is given (before it `_wrap`
    deflects it)."""
    if turn.injected:
        return _reject(state)
    return state, None


def _injection_after_close(state: ConversationState, turn: TurnInput) -> Interrupted:
    """Once the screening is over it closes a qualified application too, whose interview is
    dropped; one already closed stays as it is."""
    if turn.injected and state.outcome == Outcome.QUALIFIED:
        return _reject(state)
    return state, None


def _insult(state: ConversationState, turn: TurnInput) -> Interrupted:
    if turn.extraction.intent == Intent.ABUSE:
        return _abuse(state)
    return state, None


def _opting_out_booked(state: ConversationState, turn: TurnInput) -> Interrupted:
    """With the interview booked, an opt-out cancels it and withdraws the application."""
    booking = state.booking
    if booking is not None and turn.extraction.intent == Intent.OPT_OUT:
        return _withdraw(state), _opt_out_end(state, booking)
    return state, None


def _cancel_request(state: ConversationState, turn: TurnInput) -> Interrupted:
    return _cancel_turn(state, turn.extraction, turn.cancel) or (state, None)


def _pause_answer(state: ConversationState, turn: TurnInput) -> Interrupted:
    """The pause offer is answered once: a yes with nothing else pauses, a bare "no, sigamos"
    asks the pending question again, and anything else is read as usual."""
    if not state.pause_offered:
        return state, None
    extraction = turn.extraction
    intent = extraction.intent
    picked = _interview_answer(state, extraction, turn.time_request)
    state = state.model_copy(update={"pause_offered": False})
    if intent == Intent.CONFIRM and not _known_fields(extraction):
        state = state.model_copy(update={"paused": True})
        return state, End(reason=EndReason.PAUSED, **_meta(state))
    if intent in ANSWERING_INTENTS and not _known_fields(extraction) and not picked:
        # "No, sigamos" answers the pause offer, not the pending question: ask it again.
        if isinstance(state.phase, Interview):
            return state, interview.HANDLER.prompt(state)
        state, next_step = _next_step(state)
        return _wrap(state, extraction, next_step)
    return state, None


def _frustration(state: ConversationState, turn: TurnInput) -> Interrupted:
    """Frustrated twice in a row: the pause offer, and a quality flag. An answer to the mode or
    the times is acted on instead (after the handler, while asking, there is none)."""
    frustrated = state.frustration_streak >= PAUSE_AFTER_FRUSTRATED_TURNS
    if frustrated and not _interview_answer(state, turn.extraction, turn.time_request):
        return _offer_pause(state)
    return state, None


def _cancel_turn(
    state: ConversationState, extraction: TurnExtraction, cancel: CancelRequest
) -> Turn | None:
    """A request to cancel the booked interview, or the answer to the question it raised. The
    first request asks once whether to cancel it or find another time; a second, or a yes, to
    that question cancels it. Anything else (another time, a pick, a question) is read as
    usual: None."""
    booking = state.booking
    if booking is None or not state.consented:
        return None
    if cancel.offered:
        moving = extraction.slot in (CHANGE, NONE_WORK)
        if cancel.requested or (extraction.intent == Intent.CONFIRM and not moving):
            return _cancel_interview(state)
        return None
    if not cancel.requested:
        return None
    state = state.at_interview(_offer_cancel(state))
    return state, AskCancel(booking=booking, ui=cancel_ui(state.language), **_meta(state))


def _offer_cancel(state: ConversationState) -> Offering | InterviewBooked:
    """ "¿La cancelo o prefieres otra hora?" asked, while booked or moving the interview."""
    if state.offering is not None:
        return state.offering.model_copy(update={"cancel_offered": True})
    booked = state.booked
    assert booked is not None
    then = None if isinstance(booked.sub, Idle | CancelConfirm) else booked.sub
    return booked.model_copy(update={"sub": CancelConfirm(then=then)})


def _cancel_interview(state: ConversationState) -> Turn:
    """The candidate cancels their interview: it is dropped (the service cancels the booking and
    deletes its event) and the application is withdrawn. The reply names its day."""
    booking = state.booking
    state = _withdraw(state)
    return state, End(
        reason=EndReason.CANCELLED, booking_cancelled=True, cancelled=booking, **_meta(state)
    )


def _offer_pause(state: ConversationState) -> Turn:
    state = add_quality_flag(state, QualityFlag.FRUSTRATION)
    state = state.model_copy(update={"pause_offered": True, "frustration_streak": 0})
    return state, OfferPause(
        ui=yes_no_ui(state.language),
        language=state.language,
        country=state.country,
        simplify=True,
    )


def _abuse(state: ConversationState) -> Turn:
    """Insults, counted across the whole conversation, after a close too: the first gets a
    calm line, the second ends the conversation. That drops an interview already booked (the
    service cancels it), as an injection does; a qualified candidate keeps the outcome, for a
    recruiter to review, and a closed application keeps its own."""
    strikes = state.abuse_strikes + 1
    state = state.model_copy(update={"abuse_strikes": strikes})
    if strikes >= MAX_ABUSE_STRIKES:
        cancelled = state.booking is not None
        state = add_quality_flag(state, QualityFlag.ABUSE)
        closed = state.closed
        if closed is None:
            state = _close(state, Outcome.ABANDONED)
        elif isinstance(closed, Abandoned):
            # Ended for good: a return never asks "¿Quieres continuar como Lucía?" again.
            state = state.at(Closed(variant=closed.model_copy(update={"match": None})))
        return state, End(reason=EndReason.ABUSE, booking_cancelled=cancelled, **_meta(state))
    follow_up = current_prompt(state)
    ui = follow_up.ui if follow_up else None
    closed = state.stage == Stage.DONE
    state = state.model_copy(update={"de_escalated": True})
    return state, DeEscalate(follow_up=follow_up, ui=ui, closed=closed, **_meta(state))


def _opt_out(state: ConversationState) -> Turn:
    if not state.consented:
        state = ConversationState(
            phase=Closed(variant=DeclinedConsent()),
            context=state.context,
            country=state.country,
            country_source=state.country_source,
            language=state.language,
        )
        return state, End(reason=EndReason.DECLINED_CONSENT, **_meta(state))
    # An interview already booked is cancelled with it, also while new times are on offer, and
    # the application is withdrawn. Without one the outcome stays: abandoned, or qualified.
    booking = state.booking
    state = _withdraw(state) if booking is not None else _close(state, Outcome.ABANDONED)
    closed = state.closed
    assert isinstance(closed, Qualified | Withdrawn | Abandoned)
    state = state.at(Closed(variant=closed.model_copy(update={"opted_out": True})))
    return state, _opt_out_end(state, booking)


def _withdraw(state: ConversationState) -> ConversationState:
    """The candidate cancelled their booked interview, by asking or by opting out: the booking
    is dropped and the application is withdrawn (retirada), whatever it was."""
    return state.at(Closed(variant=Withdrawn()))


def _opt_out_end(state: ConversationState, booking: Booked | None) -> End:
    """The goodbye to an opt-out, which says so when it cancelled the interview."""
    return End(
        reason=EndReason.OPT_OUT,
        booking_cancelled=booking is not None,
        cancelled=booking,
        **_meta(state),
    )


OPEN: frozenset[Where] = frozenset(PhaseKind) - {PhaseKind.CLOSED}
CONSENTED = OPEN - {PhaseKind.CONSENT}
SCREENING: frozenset[Where] = frozenset({PhaseKind.ASKING, PhaseKind.SUMMARY})
OVER: frozenset[Where] = frozenset({PhaseKind.CLOSED, BOOKED})
"""The screening is over: closed, or the interview booked."""

SCREENING_NODES = (Node.ASKING, Node.CONTACT, Node.SUMMARY)
"""The sub-states of the consented phases before the interview: closing there abandons."""
OPEN_NODES = (Node.CONSENT, *SCREENING_NODES)
"""Where an application is still open before it qualifies."""
CHOOSING_NODES = (Node.CHOOSING_MODE, Node.OFFERING)
"""The interview being chosen, not yet booked."""

# The transitions each interrupt may take (`Interrupt.edges`).
OPT_OUT = frozenset(
    {
        Edge(Node.CONSENT, "opt-out", Node.DECLINED_CONSENT),
        *(Edge(at, "opt-out", Node.ABANDONED) for at in SCREENING_NODES),
        *(Edge(at, "opt-out", Node.QUALIFIED) for at in CHOOSING_NODES),
        Edge(Node.OFFERING, "opt-out, moving a booking", Node.WITHDRAWN),
    }
)
INJECTION = frozenset(
    Edge(at, "injection", Node.DISQUALIFIED) for at in SCREENING_NODES + CHOOSING_NODES
)
INJECTION_OVER = frozenset(
    Edge(at, "injection", Node.DISQUALIFIED) for at in (Node.BOOKED, Node.QUALIFIED)
)
ABUSE = frozenset(
    {
        *(Edge(at, "second insult", Node.ABANDONED) for at in OPEN_NODES),
        Edge(Node.INTERVIEW, "second insult", Node.QUALIFIED),
    }
)
OPT_OUT_OVER = frozenset({Edge(Node.BOOKED, "opt-out", Node.WITHDRAWN)})
CANCEL = frozenset(
    {
        *(
            Edge(at, "cancel", Node.CANCEL_CONFIRM)
            for at in (Node.IDLE, Node.AMENDING, Node.MOVING)
        ),
        # The question was answered before the interrupts ran (`machine._observe`).
        Edge(Node.BOOKED, "cancel again, or yes", Node.WITHDRAWN),
        Edge(Node.OFFERING, "cancel again, or yes", Node.WITHDRAWN),
    }
)

INTERRUPTS = (
    Interrupt("opt-out", OPEN, Placement.BEFORE, _opting_out, OPT_OUT),
    Interrupt("injection", CONSENTED, Placement.BEFORE, _injection, INJECTION),
    Interrupt(
        "injection after close", OVER, Placement.BEFORE, _injection_after_close, INJECTION_OVER
    ),
    Interrupt("abuse", OPEN | OVER, Placement.BEFORE, _insult, ABUSE),
    Interrupt("opt-out after close", OVER, Placement.BEFORE, _opting_out_booked, OPT_OUT_OVER),
    Interrupt("cancel request", CONSENTED | OVER, Placement.BEFORE, _cancel_request, CANCEL),
    Interrupt("pause answer", CONSENTED - {PhaseKind.CONTACT}, Placement.BEFORE, _pause_answer),
    Interrupt("frustration", frozenset({PhaseKind.INTERVIEW}), Placement.BEFORE, _frustration),
    Interrupt("frustration after the answers", SCREENING, Placement.AFTER, _frustration),
)


def interrupt(
    state: ConversationState, turn: TurnInput, *, at: Where, after: bool = False
) -> Interrupted:
    """Run the interrupts of phase `at` in order, before its handler or after it: the first
    action decides the turn. An interrupt may change the state and still let the turn through
    (an answer to the pause offer clears it)."""
    placement = Placement.AFTER if after else Placement.BEFORE
    for each in INTERRUPTS:
        if each.placement is placement and at in each.phases:
            state, action = each.run(state, turn)
            if action is not None:
                return state, action
    return state, None
