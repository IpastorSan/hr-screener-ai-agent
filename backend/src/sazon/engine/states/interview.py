"""Interview: choosing the interview mode (`ChoosingMode`), then its time (`Offering`), until
one is booked (`InterviewBooked`) or none suits. Once booked, the candidate may move it, switch
its mode, correct a detail or change the location; a booking moved keeps its interview until a
new time is booked."""

import re
from collections.abc import Sequence
from datetime import date
from typing import Any, ClassVar

from sazon.engine.actions import (
    ApplicationSaved,
    AskField,
    AskMode,
    BookSlot,
    ConfirmBooking,
    ConfirmMove,
    DetailsUpdated,
    FindModes,
    FindSlots,
    OfferSlots,
    Prompt,
)
from sazon.engine.country import after_location
from sazon.engine.lookups import LookupResult, ModesFound, SlotBooked, SlotsFound
from sazon.engine.models import FieldId, Intent, InterviewMode, TurnExtraction
from sazon.engine.modes import allowed_mode
from sazon.engine.screening import _closes, _flag, _store, _validate, _with_first_name
from sazon.engine.slots import (
    CHANGE,
    NONE_WORK,
    VOICE_READ_SLOTS,
    Miss,
    OfferReason,
    SlotQuery,
    TimeRequest,
)
from sazon.engine.spec import FieldType, agent
from sazon.engine.state import (
    Amending,
    CancelConfirm,
    ChoosingMode,
    Closed,
    ConversationState,
    Disqualified,
    Idle,
    InterviewBooked,
    InterviewFix,
    Moving,
    Node,
    Offering,
    Qualified,
)
from sazon.engine.states.handler import Edge
from sazon.engine.text import fold
from sazon.engine.turn import (
    FRESH_TIMES,
    Lookup,
    Turn,
    TurnInput,
    _answer,
    _answering,
    _booked_mode,
    _closed_reply,
    _disqualify,
    _known_fields,
    _meta,
    _wrap,
)
from sazon.engine.ui import field_options, field_ui, mode_ui, slots_ui, yes_no_ui
from sazon.engine.validators import ReasonCode, Valid
from sazon.engine.validators import place_said as said_place

MAX_SLOT_OFFERS = 2
TIME_QUESTION = re.compile(
    r"\b(huecos?|horarios?|horas?|dias?|fechas?|mananas?|tardes?|noches?|tarde-noche|"
    r"semana|finde|lunes|martes|miercoles|jueves|viernes|sabados?|domingos?|antes|despues|"
    r"\d{1,2}(:\d{2})?|times?|slots?|days?|mornings?|afternoons?|evenings?|nights?|"
    r"weekends?|later|earlier|before|after)\b"
)
"""A question about when the interview could be, asked while turning the offered times down."""
MODE_ORDER = tuple(InterviewMode)


class Interview:
    edges: ClassVar[frozenset[Edge]] = frozenset(
        {
            # A mode chosen, or the only one found (`on_lookup`).
            Edge(Node.CHOOSING_MODE, "mode", Node.OFFERING),
            # A pick booked (`on_lookup`), or none suits and the booking is kept.
            Edge(Node.OFFERING, "booked, or none suits a booking", Node.IDLE),
            Edge(Node.OFFERING, "none free, or none suits after two offers", Node.QUALIFIED),
            # Booked (`_after_booking`).
            Edge(Node.IDLE, "change of time or mode", Node.OFFERING),
            Edge(Node.IDLE, "fix a detail", Node.AMENDING),
            Edge(Node.AMENDING, "corrected, or given up", Node.IDLE),
            Edge(Node.IDLE, "new location", Node.MOVING),
            Edge(Node.AMENDING, "new location", Node.MOVING),
            Edge(Node.MOVING, "no", Node.IDLE),
            Edge(Node.MOVING, "yes", Node.CHOOSING_MODE),
            Edge(Node.BOOKED, "no license", Node.DISQUALIFIED),
        }
    )

    def on_turn(self, state: ConversationState, turn: TurnInput) -> Turn:
        match state.interview:
            case ChoosingMode():
                return _mode_choice(state, turn.extraction)
            case Offering():
                mode_fix = turn.fix == InterviewFix.MODE
                return _scheduling(state, turn.extraction, turn.time_request, switch_mode=mode_fix)
            case _:
                return _after_booking(state, turn)

    def prompt(self, state: ConversationState) -> Prompt | Lookup | None:
        return None if state.booked is not None else _prompt(state)

    def on_lookup(self, state: ConversationState, result: LookupResult) -> Turn:
        if isinstance(result, ModesFound):
            return _modes_found(state, result.modes)
        if isinstance(result, SlotsFound):
            return _slots_found(state, result)
        if isinstance(result, SlotBooked):
            return _booked(state, result)
        raise ValueError(f"no {result.kind} is pending in the interview")


HANDLER = Interview()


def _prompt(state: ConversationState) -> Prompt | Lookup:
    """The mode question, or the times on offer; the lookup for them when there are none yet."""
    choosing = state.interview
    if isinstance(choosing, ChoosingMode):
        if len(state.available_modes) < 2:
            return _find_modes(state)
        return _ask_mode(state, retry=choosing.retries)
    if not state.offered_slots:
        return _find_slots(state, OfferReason.QUALIFIED)
    return _offer_slots(state)


def _modes_found(state: ConversationState, modes: Sequence[InterviewMode]) -> Turn:
    """Continue after `FindModes` with the interview modes the location's recruiters offer.

    Both: ask which one the candidate wants. One: it is the mode, no question. None: no one
    interviews there, so the slot lookup (in person) comes back empty and the application is
    saved. A question asked with the confirmation is answered before the next step.
    """
    available = tuple(mode for mode in MODE_ORDER if mode in modes)
    state = state.model_copy(update={"available_modes": available})
    if len(available) > 1:
        pending_question = state.pending_question
        state = state.at_interview(ChoosingMode())
        ask = _ask_mode(state, qualified=True)
        if pending_question is None:
            return state, ask
        return state, _answer(state, pending_question, ask)
    state = _choose_mode(state, available[0] if available else InterviewMode.ONSITE)
    return state, _find_slots(state, OfferReason.QUALIFIED)


def _slots_found(state: ConversationState, found: SlotsFound) -> Turn:
    """Continue after `FindSlots`. The service passes the free slots it found, best first, and
    for a `query` whether they fit it (`miss` None) or are the nearest to it, and why.

    No slots: a booking stays as it was; without one, the application is saved, qualified.
    Times asked about with nothing free at all leave the offer as it was, saying so.
    """
    request, slots, miss = found.request, found.slots, found.miss
    pending_question = state.pending_question
    offering = _offering(state).model_copy(update={"parked_question": None})
    state = state.at_interview(offering)
    asked = request.reason == OfferReason.ASKED
    if not slots and asked and state.offered_slots:
        return state, _offer_slots(state, OfferReason.ASKED, miss=Miss.LEFT)
    if not slots:
        return _no_slot_fits(state, none_free=request.reason != OfferReason.MORE)
    offers = 1
    if request.reason == OfferReason.MORE:
        offers = offering.offers + 1
    elif request.reason in (OfferReason.TAKEN, OfferReason.ASKED):
        # A question about the times is not a round turned down.
        offers = max(offering.offers, 1)
    read = slots[:VOICE_READ_SLOTS] if state.voice else slots
    shown = tuple(dict.fromkeys((*state.shown_slots, *(slot.id for slot in read))))
    offering = offering.model_copy(
        update={"slots": tuple(slots), "offers": offers, "retries": 0, "which_asked": False}
    )
    state = state.at_interview(offering).model_copy(
        update={"shown_slots": shown, "slot_query": request.query}
    )
    offer = _offer_slots(state, request.reason, miss=miss)
    if pending_question is None:
        return state, offer
    return state, _answer(state, pending_question, offer)


def _booked(state: ConversationState, booking: SlotBooked) -> Turn:
    """Continue after `BookSlot` with the booking, or None when the slot filled up meanwhile."""
    request, booked = booking.request, booking.booked
    if booked is None:
        offering = _offering(state)
        offered = tuple(slot for slot in offering.slots if slot.id != request.slot_id)
        state = state.at_interview(offering.model_copy(update={"slots": offered}))
        return state, _find_slots(state, OfferReason.TAKEN, exclude=(request.slot_id,))
    rescheduled = state.booking is not None
    state = state.at_interview(InterviewBooked(booking=booked)).model_copy(update=FRESH_TIMES)
    return state, ConfirmBooking(
        booking=booked,
        rescheduled=rescheduled,
        has_email=state.profile.email is not None,
        ui=booked,
        **_meta(state),
    )


def _offer_slots(
    state: ConversationState,
    reason: OfferReason | None = None,
    retry: int = 0,
    *,
    miss: Miss | None = None,
    pick: bool = False,
) -> OfferSlots:
    slots = state.offered_slots
    return OfferSlots(
        location_id=state.profile.location_id,
        slots=slots,
        reason=reason,
        retry=retry,
        pick=pick,
        mode=state.profile.interview_mode,
        matched=miss is None,
        miss=miss,
        ui=slots_ui(slots),
        **_meta(state),
    )


def _find_modes(state: ConversationState) -> FindModes:
    return FindModes(location_id=state.profile.location_id or "", **_meta(state))


def _ask_mode(state: ConversationState, retry: int = 0, *, qualified: bool = False) -> AskMode:
    options = state.available_modes
    return AskMode(
        options=options,
        qualified=qualified,
        retry=retry,
        ui=mode_ui(options, state.language),
        **_meta(state),
    )


def _find_slots(
    state: ConversationState,
    reason: OfferReason,
    exclude: tuple[str, ...] = (),
    query: SlotQuery | None = None,
    *,
    skip_shown: bool = False,
) -> FindSlots:
    """A booked slot is never offered again: picking it would not move anything. Nor is a
    time the candidate turned down, and without a question about other times (`query`) the
    days they turned down are skipped too (D9). `skip_shown`: nor any time offered in this
    conversation (more times, or other times asked about)."""
    if state.booking is not None:
        exclude = (*exclude, state.booking.slot_id)
    declined = state.declined_slots
    shown = state.shown_slots if skip_shown else ()
    exclude = tuple(dict.fromkeys((*exclude, *(slot.id for slot in declined), *shown)))
    avoid: tuple[date, ...] = ()
    if query is None:
        avoid = tuple(sorted({date.fromisoformat(slot.starts_at[:10]) for slot in declined}))
    profile = state.profile
    prefer = agent().interview
    blocks, before = (_preferred(state, field) for field in (prefer.blocks, prefer.before))
    return FindSlots(
        location_id=profile.location_id or "",
        schedule=tuple(blocks) if blocks else None,
        start_date=before,
        reason=reason,
        exclude=exclude,
        mode=profile.interview_mode,
        query=query,
        avoid_days=avoid,
        **_meta(state),
    )


def _preferred(state: ConversationState, field: FieldId | None) -> Any:
    """The answer the interview's times are fitted to (`spec.InterviewSpec`)."""
    return agent().field(field).value(state.profile) if field is not None else None


def _mode_choice(state: ConversationState, extraction: TurnExtraction) -> Turn:
    """A reply to "in person or video call?": a mode on offer, or something else."""
    if len(state.available_modes) < 2:
        return _wrap(state, extraction, _prompt(state))
    mode = allowed_mode(extraction.mode, state.available_modes)
    if mode is not None:
        state = _choose_mode(state, mode)
        return _wrap(state, extraction, _find_slots(state, OfferReason.MODE))
    if _answering(extraction):
        choosing = state.interview
        assert isinstance(choosing, ChoosingMode)
        retries = choosing.retries + 1
        state = state.at_interview(choosing.model_copy(update={"retries": retries}))
        return state, _ask_mode(state, retry=retries)
    return _wrap(state, extraction, _prompt(state))


def _choose_mode(state: ConversationState, mode: InterviewMode) -> ConversationState:
    """Interview `mode` from now on: its times are looked up, a new round (the slots on offer
    were for the other one). A booked interview moves to it once one is booked; a question
    waiting on the modes' lookup waits on this one."""
    offering = Offering(booking=state.booking, parked_question=state.pending_question)
    profile = state.profile.model_copy(update={"interview_mode": mode})
    return state.at_interview(offering).model_copy(update={"profile": profile, **FRESH_TIMES})


def _other_mode(state: ConversationState) -> InterviewMode | None:
    """The mode on offer that is not the chosen one ("quiero cambiar la modalidad")."""
    current = _booked_mode(state) or state.profile.interview_mode
    others = [mode for mode in state.available_modes if mode != current]
    return others[0] if others else None


def _mode_change(state: ConversationState, extraction: TurnExtraction) -> InterviewMode | None:
    """The other interview mode on offer, when the candidate asks to switch to it."""
    mode = allowed_mode(extraction.mode, state.available_modes)
    if mode is None or mode == state.profile.interview_mode:
        return None
    return mode


def _scheduling(
    state: ConversationState,
    extraction: TurnExtraction,
    request: TimeRequest | None = None,
    *,
    switch_mode: bool = False,
) -> Turn:
    """A reply to the offered slots: a pick, "none of these", a switch to the other interview
    mode ("mejor por video": new slots for that mode), a question about other times (looked
    up, never answered from the FAQ), words that fit several slots (asked which), or
    something else. Only a slot the words single out is booked. `switch_mode`: "quiero cambiar
    la modalidad", the other mode on offer without naming it."""
    mode = _mode_change(state, extraction) or (_other_mode(state) if switch_mode else None)
    if mode is not None:
        state = _choose_mode(state, mode)
        return _wrap(state, extraction, _find_slots(state, OfferReason.MODE))
    offering = _offering(state)
    choice = extraction.slot
    if choice is not None and any(slot.id == choice for slot in offering.slots):
        return state, BookSlot(slot_id=choice, **_meta(state))
    if request is not None and request.matches:
        # "A las cuatro" with three times at 16:00: which day? Only those are offered.
        which = tuple(slot for slot in offering.slots if slot.id in request.matches)
        if offering.which_asked:
            # Asked which once already: never the same question again (it looped in
            # production), but the buttons and a one-line ask, counted as a retry.
            retries = offering.retries + 1
            state = state.at_interview(
                offering.model_copy(update={"slots": which, "retries": retries})
            )
            return state, _offer_slots(state, retry=retries, pick=True)
        update = {"slots": which, "retries": 0, "which_asked": True}
        state = state.at_interview(offering.model_copy(update=update))
        return state, _offer_slots(state, OfferReason.WHICH)
    query = request.query if request is not None else None
    if choice in (NONE_WORK, CHANGE) and offering.slots:
        state = _decline_offered(state)
        if query is not None:
            return _wrap(state, _time_question_answered(extraction), _ask_times(state, query))
        if offering.offers >= MAX_SLOT_OFFERS:
            return _no_slot_fits(state, none_free=False)
        if extraction.question and TIME_QUESTION.search(fold(extraction.question)):
            # "None of these, anything else?": the next offer is the answer.
            extraction = _time_question_answered(extraction)
        return _wrap(state, extraction, _find_slots(state, OfferReason.MORE, skip_shown=True))
    if query is not None:
        return _wrap(state, _time_question_answered(extraction), _ask_times(state, query))
    if (_answering(extraction) or (request is not None and request.unmatched_time)) and (
        offering.slots
    ):
        # "10:40" when 10:30 and 10:45 are on offer: asked again, never another time booked.
        retries = offering.retries + 1
        state = state.at_interview(offering.model_copy(update={"retries": retries}))
        return state, _offer_slots(state, retry=retries)
    return _wrap(state, extraction, _prompt(state))


def _interview_answer(
    state: ConversationState, extraction: TurnExtraction, request: TimeRequest | None
) -> bool:
    """The message answers the mode or the times question: a mode on offer, a slot, "none of
    these", a switch of mode, or a question about the times. Frustrated or not, it is acted
    on, never met with the pause offer."""
    if isinstance(state.interview, ChoosingMode):
        return allowed_mode(extraction.mode, state.available_modes) is not None
    if state.offering is None:
        return False
    offered = {slot.id for slot in state.offered_slots}
    chose = extraction.slot in offered or extraction.slot in (NONE_WORK, CHANGE)
    return chose or request is not None or _mode_change(state, extraction) is not None


def _decline_offered(state: ConversationState) -> ConversationState:
    """The offered times are turned down: kept so they are never offered again (D9)."""
    known = {slot.id for slot in state.declined_slots}
    declined = (*state.declined_slots, *(s for s in state.offered_slots if s.id not in known))
    state = state.at_interview(_offering(state).model_copy(update={"slots": ()}))
    return state.model_copy(update={"declined_slots": declined})


def _ask_times(state: ConversationState, query: SlotQuery) -> FindSlots:
    """The free times that fit what the candidate asked about, looked up by the service. None
    already offered in this conversation, except the clock time they name ("¿y a las 12?",
    which may be one). "¿Y otras opciones?" asks again what the offer on screen answered ("el
    martes 27"): its other times, until that day has none left."""
    if query.unconstrained and state.slot_query is not None:
        query = state.slot_query.model_copy(update={"other": True})
    reason = OfferReason.ASKED if state.booking is None else OfferReason.RESCHEDULE
    skip_shown = query.other or not query.at
    return _find_slots(state, reason, query=query, skip_shown=skip_shown)


def _time_question_answered(extraction: TurnExtraction) -> TurnExtraction:
    """The question was about the times: the lookup answers it, never the FAQ."""
    return extraction.model_copy(
        update={"question": None, "sub_questions": [], "intent": Intent.ANSWER}
    )


def _no_slot_fits(state: ConversationState, *, none_free: bool) -> Turn:
    """No slot was free or suited. A booking stays; otherwise the screening ends, qualified and
    without an interview, with the application saved."""
    booking = state.booking
    if booking is not None:
        # A switch of mode with no time for it keeps the interview as booked, mode included.
        mode = booking.mode or state.profile.interview_mode
        profile = state.profile.model_copy(update={"interview_mode": mode})
        state = state.at_interview(InterviewBooked(booking=booking))
        state = state.model_copy(update={"profile": profile})
        has_email = state.profile.email is not None
        return state, ConfirmBooking(
            booking=booking, kept=True, has_email=has_email, ui=booking, **_meta(state)
        )
    state = state.at(Closed(variant=Qualified(slots_declined=True)))
    return state, ApplicationSaved(none_free=none_free, **_meta(state))


def _offering(state: ConversationState) -> Offering:
    offering = state.offering
    assert offering is not None, "the interview's times are being offered"
    return offering


# --- booked ---


def _after_booking(state: ConversationState, turn: TurnInput) -> Turn:
    """A message with the interview booked, once the interrupts let it through (`interrupts.py`:
    an injection attempt, insults, an opt-out and a request to cancel it). A detail named as
    wrong is corrected (`_amend_turn`). A request to move the interview gets new times, those
    asked about when it names any ("¿la puedo pasar al viernes?"), as does "quiero cambiar la
    hora de la entrevista"; "cambiar la modalidad" gets times for the other mode on offer, or for
    the same one when there is no other."""
    extraction, fix = turn.extraction, turn.fix
    amend = fix if not isinstance(fix, InterviewFix) else None
    amended = _amend_turn(state, extraction, amend, turn.today)
    if amended is not None:
        return amended
    interview_fix = fix if isinstance(fix, InterviewFix) else None
    mode = _mode_change(state, extraction)
    if interview_fix == InterviewFix.MODE:
        mode = mode or _other_mode(state)
    if mode is not None:
        # "Mejor por videollamada" after booking: times for that mode; the booking moves there.
        state = _choose_mode(state, mode)
        return state, _find_slots(state, OfferReason.MODE)
    if interview_fix is not None or _reschedule(state, extraction):
        state = state.at_interview(Offering(booking=state.booking))
        request = turn.time_request
        query = request.query if request is not None else None
        return state, _find_slots(state, OfferReason.RESCHEDULE, query=query)
    return _closed_reply(state, extraction)


def _reschedule(state: ConversationState, extraction: TurnExtraction) -> bool:
    """After booking, the candidate asks to move the interview or says they can't make it."""
    if extraction.slot in (CHANGE, NONE_WORK):
        return True
    return extraction.intent == Intent.CORRECTION and not _known_fields(extraction)


def _amend_turn(
    state: ConversationState, extraction: TurnExtraction, amend: FieldId | None, today: date
) -> Turn | None:
    """With the interview booked, a detail named as wrong ("mi correo está mal") is asked again
    and the answer updates the profile; the interview stays (`DetailsUpdated`). A correction
    that brings the value ("mi correo nuevo es...") is applied at once. A new location asks
    first, since moving cancels the interview and offers times there (`ConfirmMove`). None
    when the message corrects nothing: it is read as any message after booking."""
    sub = _booked_sub(state)
    if isinstance(sub, Moving):
        state = _at_booked(state, Idle())
        if extraction.intent == Intent.CONFIRM and not _known_fields(extraction):
            return _move(state, sub.location_id, sub.said)
        location = agent().one(FieldType.LOCATION)
        assert location is not None, "a move is to a location"
        if location.id not in extraction.fields:
            return state, _updated(state, location.id, changed=False)
        amend = location.id
    field = amend or (sub.field if isinstance(sub, Amending) else None)
    if field is None:
        known = _known_fields(extraction)
        if extraction.intent != Intent.CORRECTION or not known:
            return None
        field = known[0]
    candidate = extraction.fields.get(field)
    if candidate is not None:
        return _amend_value(state, field, candidate.value, today)
    if amend is not None:
        return _ask_amend(state, field)
    if extraction.intent == Intent.QUESTION:
        return None  # answered as after booking; the detail is still asked
    return _amend_failed(state, field)


def _ask_amend(
    state: ConversationState, field: FieldId, retries: int = 0, hint: ReasonCode | None = None
) -> Turn:
    """The detail asked again, with its buttons when it is picked from fixed options (one with
    an "other" in words is typed)."""
    state = _at_booked(state, Amending(field=field, retries=retries))
    spec = agent().field(field)
    typed = spec.type == FieldType.MULTI_ENUM and spec.other
    options = () if typed else field_options(field, state.country)
    ui = field_ui(field, options, state.language, state.country)
    return state, AskField(field=field, retry=retries, hint=hint, again=True, ui=ui, **_meta(state))


def _amend_failed(state: ConversationState, field: FieldId, hint: ReasonCode | None = None) -> Turn:
    """No usable value: asked once more, then the detail is left as it was."""
    sub = _booked_sub(state)
    if not isinstance(sub, Amending) or sub.retries < 1:
        return _ask_amend(state, field, 1, hint)
    state = _at_booked(state, Idle())
    return state, _updated(state, field, changed=False)


def _updated(state: ConversationState, field: FieldId, *, changed: bool) -> DetailsUpdated:
    booking = state.booking
    assert booking is not None
    return DetailsUpdated(field=field, booking=booking, changed=changed, ui=booking, **_meta(state))


def _amend_value(state: ConversationState, field: FieldId, raw: object, today: date) -> Turn:
    """The corrected value after booking. A credential's gate still applies: a no closes the
    application and drops the interview. A place of the same location changes nothing; one of
    another location asks before moving there."""
    spec = agent().field(field)
    result = _validate(state, field, raw, today)
    if spec.type == FieldType.PERSON_NAME:
        raw, result = _with_first_name(state, field, raw, result)
    if not isinstance(result, Valid):
        return _amend_failed(state, field, result.reason)
    state = _at_booked(state, Idle())
    closes = _closes(field, result)
    if closes is not None and spec.gate is not None:
        # Closed at the gate, no longer qualified: the interview is dropped.
        profile = state.profile.model_copy(update={spec.attr: False})
        closed = Disqualified(reason=closes, reopenable=spec.gate.reopenable)
        state = state.model_copy(update={"profile": profile, "phase": Closed(variant=closed)})
        return state, _disqualify(state)
    state, result = _flag(state, field, result)
    if spec.type == FieldType.LOCATION:
        location_id = str(result.value)
        if result.needs_confirmation:
            return _amend_failed(state, field)
        if location_id == spec.value(state.profile):
            return state, _updated(state, field, changed=False)
        state = _at_booked(
            state, Moving(location_id=location_id, said=said_place(raw, location_id))
        )
        booking = state.booking
        assert booking is not None
        return state, ConfirmMove(
            booking=booking,
            from_location_id=spec.value(state.profile) or "",
            location_id=location_id,
            ui=yes_no_ui(state.language),
            **_meta(state),
        )
    profile = _store(state.profile, field, result.value)
    changed = profile != state.profile
    return state.model_copy(update={"profile": profile}), _updated(state, field, changed=changed)


def _move(state: ConversationState, location_id: str, said: str | None) -> Turn:
    """Move to another location: the booked interview is dropped (the service cancels it) and
    the interview modes and times of the new location are looked up, still qualified."""
    location = agent().one(FieldType.LOCATION)
    assert location is not None, "a move is to a location"
    place = dict(zip(location.attrs, (location_id, said), strict=True))
    profile = state.profile.model_copy(update={**place, "interview_mode": None})
    state = after_location(state.model_copy(update={"profile": profile}), location_id)
    state = state.at_interview(ChoosingMode()).model_copy(
        update={"available_modes": (), "declined_slots": (), **FRESH_TIMES}
    )
    return state, _find_modes(state)


def _booked_sub(state: ConversationState) -> Idle | Amending | Moving | CancelConfirm:
    booked = state.booked
    assert booked is not None, "the interview is booked"
    return booked.sub


def _at_booked(state: ConversationState, sub: Idle | Amending | Moving) -> ConversationState:
    booked = state.booked
    assert booked is not None, "the interview is booked"
    return state.at_interview(booked.model_copy(update={"sub": sub}))
