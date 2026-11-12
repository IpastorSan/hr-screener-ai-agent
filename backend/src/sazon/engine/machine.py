"""The screening flow as a pure function: (state, extraction) -> (new state, next action).

`step` hands each turn to the interrupts (`interrupts.py`) and to the handler of the state's
phase (`states/`); the other entry points start, nudge, close, reopen and resume a
conversation. No I/O and no clock: the caller passes `today`. Fields are written only by
validators and the outcome only by the engine, whatever the extraction contains.
"""

from datetime import date
from typing import Any

from sazon.engine.actions import ApplicationStatus, FindSlots, Greet, ReEngage
from sazon.engine.country import initial_country
from sazon.engine.interrupts import OPEN_NODES, interrupt
from sazon.engine.language import COUNTRY_DEFAULT_LANGUAGE, decide_language
from sazon.engine.lookups import LookupResult
from sazon.engine.models import (
    FieldId,
    Language,
    Outcome,
    QualityFlag,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.screening import _next_step, _tell_contact_dropped
from sazon.engine.slots import Booked, OfferReason, TimeRequest
from sazon.engine.spec import agent
from sazon.engine.state import (
    Abandoned,
    Asking,
    CancelConfirm,
    Closed,
    Consent,
    Contact,
    ConversationContext,
    ConversationState,
    CountrySource,
    Disqualified,
    Idle,
    Interview,
    InterviewBooked,
    InterviewFix,
    Node,
    Offering,
    Phase,
    PhaseKind,
    add_quality_flag,
    phase_kind,
    where,
)
from sazon.engine.states import HANDLERS, current_prompt, prompt
from sazon.engine.states.closed import _reopens, _saved_without_interview, _undo_close
from sazon.engine.states.handler import Answered, Edge
from sazon.engine.states.interview import _find_slots
from sazon.engine.turn import (
    FRESH_TIMES,
    LOOKUPS,
    MAX_ABUSE_STRIKES,
    CancelRequest,
    Turn,
    TurnInput,
    _close,
    _greet,
    _meta,
    _unknown_fields,
    _wrap,
)
from sazon.engine.validators import Country


def start(context: ConversationContext | None = None) -> tuple[ConversationState, Greet]:
    context = context or ConversationContext()
    country, source = initial_country(context)
    state = ConversationState(
        context=context,
        country=country,
        country_source=source,
        language=greeting_language(context, country),
    )
    return state, _greet(state)


def greeting_language(context: ConversationContext, country: Country) -> Language:
    """The posting's language, else English for an `en` browser locale, else Spanish."""
    if context.language is not None:
        return context.language
    locale = (context.locale or "").replace("_", "-").lower()
    if locale == "en" or locale.startswith("en-"):
        return Language.EN
    return COUNTRY_DEFAULT_LANGUAGE[country]


def step(
    state: ConversationState,
    extraction: TurnExtraction,
    *,
    today: date,
    suspicious: bool = False,
    fix: FieldId | InterviewFix | None = None,
    value_only: bool = False,
    time_request: TimeRequest | None = None,
    cancel_request: bool = False,
) -> Turn:  # [Turn = ConversationState, Action]
    """Advance the conversation by one candidate turn.

    The checks run in this order and the first that applies decides the turn, so the order is
    the policy:

    1. A gate close the message contradicts is undone (`closed._reopens`).
    2. The interrupts before the handler (`interrupts.INTERRUPTS`): opt-out (refusing one
       contact is not one), an injection after consent, abuse, a request to cancel the
       interview, the answer to the pause offer and, at the mode or the times, frustration;
       for a closed application, its own reduced list.
    3. The handler of the state's phase (`states.HANDLERS`): the consent notice, "¿Quieres
       continuar como Lucía?", the mode and the times, the summary, after a close, or the
       data turn while asking (`asking._data_turn`).
    4. When the handler stored answers and has nothing else to say (`Answered`): the
       interrupts after the handler (frustration), then the next question (`_next_step`),
       with an answer or a deflection in front (`_wrap`).

    `suspicious` comes from `sanitize_input`: hidden control characters were stripped, or the
    text reads like instructions to the model. With consent given, that (or a field name the
    engine does not know) closes the application: `policy_violation`.

    `fix`: the candidate says a value they gave is wrong, asks to change it or asks about it
    ("mi email está mal", "quiero cambiar la zona", "¿la zona no es correcta?"); it is asked
    again unless the message brings a new one. A place that is the location they already have
    is explained instead. Right after a gate closed the application, that or an answer
    contradicting the gate reopens it. An `InterviewFix` moves the booked interview (its time)
    or switches it to the other mode on offer.

    `value_only`: the message has no words, only a value (an email, a phone number): it does
    not count towards a language switch.

    `time_request`: what the service read in the candidate's words about the interview times
    beyond a single pick (`scheduling.resolve`): several offered slots fit them, a clock time
    none has, or other times asked about.

    `cancel_request`: the candidate asks to cancel their interview ("cancela la entrevista",
    `text.asks_to_cancel`). With one booked, Ana asks once whether to cancel it or find another
    time; the answer cancels it or carries on as a request to move it.
    """
    if fix in set(InterviewFix):
        fix = InterviewFix(fix)  # read back from JSON as a plain string
    reopenable = state.reopenable
    cancel = CancelRequest(requested=cancel_request, offered=state.cancel_offered)
    de_escalated = state.de_escalated
    state = _observe(state, extraction, suspicious, value_only)
    injected = _injected(extraction, suspicious)
    turn = TurnInput(extraction, today, injected, fix, time_request, cancel, de_escalated)
    kind = phase_kind(state)
    closed_at_gate = kind is PhaseKind.CLOSED and reopenable
    if closed_at_gate and not injected and _reopens(state, extraction, fix, today):
        state = _undo_close(state)
        kind = phase_kind(state)

    state, action = interrupt(state, turn, at=where(state))
    if action is not None:
        return state, action
    handled = HANDLERS[kind].on_turn(state, turn)
    if not isinstance(handled, Answered):
        return handled

    state, extraction, screening = handled
    state, action = interrupt(state, turn, at=screening, after=True)
    if action is not None:
        return state, action
    state, next_step = _next_step(state)
    if state.contact_notice is not None and not isinstance(next_step, LOOKUPS):
        return _tell_contact_dropped(state, next_step)
    return _wrap(state, extraction, next_step)


def on_lookup(state: ConversationState, result: LookupResult) -> Turn:
    """Carry on after a lookup the last action asked for (`CheckDuplicate`, `FindModes`,
    `FindSlots`, `BookSlot`) with what the service found: the handler of the state that asked
    takes it, and says what comes next."""
    return HANDLERS[phase_kind(state)].on_lookup(state, result)


def reoffer(state: ConversationState) -> tuple[ConversationState, FindSlots]:
    """The interview was cancelled outside the chat (the recruiter's calendar, SAZ-40): back
    to scheduling with no booking, and new times are looked up for the candidate. A detail
    being corrected or a move waiting for its yes goes with the booking."""
    state = state.model_copy(update={"phase": Interview(sub=Offering()), **FRESH_TIMES})
    return state, _find_slots(state, OfferReason.RESCHEDULE)


def rebook(state: ConversationState, booking: Booked) -> ConversationState:
    """The interview was moved outside the chat (the recruiter's calendar): the booking shows
    its new time. A state with none booked comes back unchanged."""
    match state.interview:
        case InterviewBooked() | Offering(booking=Booked()) as sub:
            return state.at_interview(sub.model_copy(update={"booking": booking}))
        case _:
            return state


def awaits_reoffer(state: ConversationState) -> bool:
    """Left by `reoffer` with no times on offer yet: the next visit looks them up."""
    offering = state.offering
    return offering is not None and offering.booking is None and not offering.slots


def re_engage(state: ConversationState, attempt: int) -> tuple[ConversationState, ReEngage]:
    """A nudge for a candidate who stopped answering, repeating the pending question and its
    widget."""
    prompt = current_prompt(state)
    ui = prompt.ui if prompt is not None else None
    return state, ReEngage(attempt=attempt, follow_up=prompt, ui=ui, **_meta(state))


def abandon(state: ConversationState) -> ConversationState:
    return _close(state, Outcome.ABANDONED, match=state.pending_identity)


def reopen(state: ConversationState) -> ConversationState:
    """Resume an abandoned application where it stopped. Any other state comes back unchanged.

    A conversation closed for abuse stays closed, and so does one the candidate restarted. An
    opt-out reopens only from a new chat that matches it, never in place (`opted_out`).
    """
    if state.outcome != Outcome.ABANDONED or state.abuse_strikes >= MAX_ABUSE_STRIKES:
        return state
    abandoned = state.closed
    assert isinstance(abandoned, Abandoned)
    if abandoned.restarted:
        return state
    # The question it stopped at is worked out again (`_next_step`), "¿Quieres continuar como
    # Lucía?" first when it was waiting for its answer.
    phase: Phase = Consent()
    if state.consented:
        first = agent().fields[0].id
        phase = Contact(match=abandoned.match) if abandoned.match else Asking(field=first)
    state = state.model_copy(
        update={"phase": phase, "pause_offered": False, "paused": False, "frustration_streak": 0}
    )
    if not state.consented:
        return state
    return _next_step(state)[0]


def restart(state: ConversationState) -> ConversationState:
    """ "Empezar de nuevo" (QA batch 3): an application still open, or abandoned and resumable,
    closes for good, so the duplicate check no longer resumes it and the new chat starts clean.
    Any other state (closed at a gate, qualified, already restarted) comes back unchanged, and
    so does a chat that handed over to another application (`duplicate_of`): it holds none of
    its own, and the application it found is left as it is (SAZ-88)."""
    if state.restarted or state.duplicate_of is not None:
        return state
    if state.outcome is not None and reopen(state).outcome is not None:
        return state
    if state.outcome is None:
        state = _close(state, Outcome.ABANDONED, match=state.pending_identity)
    abandoned = state.closed
    assert isinstance(abandoned, Abandoned)
    return state.at(Closed(variant=abandoned.model_copy(update={"restarted": True})))


def resume(state: ConversationState, current: ConversationState, *, today: date) -> Turn:
    """Carry on an earlier application (`state`) from a new conversation (`current`) that
    matched it (SAZ-47). What to say is rebuilt from the application's state, never from the
    action stored with it.

    The application takes the new conversation's language and channel, and its country (the
    register) unless its location already set the country: then that country's register
    holds from the question about the match on (QA batch 3), the status of a booked interview
    included. A qualified application saved with
    no interview (none was free or none suited) goes back to scheduling: times are looked up
    again (SAZ-72). One with its interview booked gets its status, in the new conversation's
    register, and nothing more. An open one gets its pending question; interview times on
    offer are looked up again, as the ones offered then may have passed.
    """
    update: dict[str, Any] = {
        "language": current.language,
        "language_locked": state.language_locked or current.language_locked,
        "voice": current.voice,
        **FRESH_TIMES,
    }
    if state.country_source != CountrySource.LOCATION:
        update |= {"country": current.country, "country_source": current.country_source}
    state = state.model_copy(update=update)
    if _saved_without_interview(state):
        state = state.at_interview(Offering())
        return state, _find_slots(state, OfferReason.QUALIFIED)
    if state.booked is not None or state.closed is not None:
        booking = state.booking
        if booking is not None and date.fromisoformat(booking.starts_at[:10]) < today:
            booking = None  # the interview has taken place: the application stays saved
        # The application's country once its location set it, as from the question about
        # the match on (QA batch 3): one register from there on, on every path.
        meta = _meta(current)
        meta["country"] = state.country
        return state, ApplicationStatus(booking=booking, ui=booking, **meta)
    offering = state.offering
    if offering is not None:
        state = state.at_interview(offering.model_copy(update={"slots": (), "retries": 0}))
        reason = OfferReason.QUALIFIED if state.booking is None else OfferReason.RESCHEDULE
        return state, _find_slots(state, reason)
    waiting = prompt(state)
    assert waiting is not None, "an open application waits on a prompt"
    return state, waiting


EDGES: dict[str, frozenset[Edge]] = {
    "start": frozenset({Edge(Node.START, "a new chat", Node.CONSENT)}),
    # Before any interrupt or handler sees the message: "¿La cancelo o prefieres otra hora?"
    # answered, back to what it interrupted (`_observe`), and a gate close the message
    # contradicts undone (`_reopens`, `_undo_close`).
    "step": frozenset(
        {
            *(
                Edge(Node.CANCEL_CONFIRM, "answered", at)
                for at in (Node.IDLE, Node.AMENDING, Node.MOVING)
            ),
            Edge(Node.DISQUALIFIED, "contradicts the gate", Node.AWAITING),
        }
    ),
    "abandon": frozenset(Edge(at, "silence", Node.ABANDONED) for at in OPEN_NODES),
    "restart": frozenset(Edge(at, "empezar de nuevo", Node.ABANDONED) for at in OPEN_NODES),
    "reopen": frozenset(Edge(Node.ABANDONED, "returns", at) for at in OPEN_NODES),
    "reoffer": frozenset({Edge(Node.BOOKED, "cancelled in the calendar", Node.OFFERING)}),
    "resume": frozenset({Edge(Node.QUALIFIED, "returns, saved with no interview", Node.OFFERING)}),
}
"""The transitions of the entry points other than a handler's: a new chat, the scheduler's
and the calendar's, and a return in a new conversation (`resume`, the earlier application)."""


# --- per-turn bookkeeping ---


def _observe(
    state: ConversationState,
    extraction: TurnExtraction,
    suspicious: bool,
    value_only: bool = False,
) -> ConversationState:
    frustrated = extraction.sentiment == Sentiment.FRUSTRATED
    language: dict[str, Any] = {}
    if not value_only or extraction.explicit_language_request is not None:
        language = {
            "language": decide_language(state, extraction),
            "language_locked": state.language_locked
            or extraction.explicit_language_request is not None,
            "recent_matrix": (*state.recent_matrix, extraction.matrix_language)[-2:],
        }
    state = state.model_copy(
        update={
            **language,
            "frustration_streak": state.frustration_streak + 1 if frustrated else 0,
            "de_escalated": False,
            "paused": False,
            "phase": _answer_awaited(state.phase),
        }
    )
    if _injected(extraction, suspicious):
        state = add_quality_flag(state, QualityFlag.INJECTION_SUSPECTED)
    return state


def _injected(extraction: TurnExtraction, suspicious: bool) -> bool:
    return suspicious or bool(_unknown_fields(extraction))


def _answer_awaited(phase: Phase) -> Phase:
    """The phase once a message came in: a gate close can no longer be undone by a later one,
    and "¿La cancelo o prefieres otra hora?" was answered (`step` read both first)."""
    match phase:
        case Closed(variant=Disqualified(reopenable=True) as closed):
            return Closed(variant=closed.model_copy(update={"reopenable": False}))
        case Interview(sub=Offering(cancel_offered=True) as offering):
            return Interview(sub=offering.model_copy(update={"cancel_offered": False}))
        case Interview(sub=InterviewBooked(sub=CancelConfirm(then=then)) as booked):
            return Interview(sub=booked.model_copy(update={"sub": then or Idle()}))
        case _:
            return phase
