"""Asking: the screening's questions, one field at a time, with its sub-states (a read-back
waiting for a yes, a choice between options, a first name waiting for its surname, a field
asked again after a correction). A message may answer several fields at once, refuse one or
say one is wrong."""

from datetime import date
from typing import Any, ClassVar

from sazon.engine.country import after_location, after_phone
from sazon.engine.models import (
    CandidateProfile,
    FieldCandidate,
    FieldId,
    Intent,
    Outcome,
    PhoneInput,
    Platform,
    QualityFlag,
    Sentiment,
    Stage,
    TurnExtraction,
)
from sazon.engine.screening import (
    NextStep,
    _close_at_credential,
    _closes,
    _flag,
    _next_step,
    _refused_field,
    _same_value,
    _screening_fix,
    _store,
    _validate,
    _with_first_name,
)
from sazon.engine.spec import Channel, FieldSpec, FieldType, agent
from sazon.engine.state import Asking as AskingPhase
from sazon.engine.state import (
    Closed,
    ConversationState,
    Disqualified,
    DisqualifyReason,
    Node,
    PendingClarification,
    PendingConfirmation,
    ReasonId,
    add_quality_flag,
    where,
)
from sazon.engine.states.handler import Answered, Edge, NoLookup
from sazon.engine.text import field_to_fix, fold
from sazon.engine.turn import (
    ANSWERING_INTENTS,
    Turn,
    TurnInput,
    _answering,
    _close,
    _disqualify,
    _known_fields,
)
from sazon.engine.validators import (
    REFUSALS,
    Invalid,
    ReasonCode,
    Result,
    StartDate,
    Valid,
    validate_date,
    validate_years,
)
from sazon.engine.validators import place_said as said_place

# A place outside the areas named in reply to its read-back closes from this confidence.
MIN_GATE_CONFIDENCE = 0.7
# The longest out-of-area place read back to the candidate; a longer answer is not a place
# name ("mejor en inglés, I grew up in LA") and the city is asked again.
MAX_PLACE_CHARS = 40
MAX_PLACE_WORDS = 5
# Words that point at a place without naming it ("cerca de donde trabajo", "por ahí"): an
# answer with one of them is asked again, never read back as somewhere to live.
NOT_A_PLACE = frozenset(
    {
        "donde", "cerca", "aqui", "ahi", "alli", "alla", "aca", "lejos", "trabajo", "casa",
        "mi", "por", "lado", "sitio", "lugar", "privado", "here", "there", "near", "nearby",
        "where", "home", "work", "somewhere", "around", "private",
    }
)  # fmt: skip


class Asking(NoLookup):
    edges: ClassVar[frozenset[Edge]] = frozenset(
        {
            Edge(Node.AWAITING, "ambiguous match", Node.CLARIFYING),
            Edge(Node.AWAITING, "value needs a read-back", Node.CONFIRMING),
            Edge(Node.CLARIFYING, "picks one", Node.AWAITING),
            Edge(Node.CONFIRMING, "yes, or no with a new value", Node.AWAITING),
            Edge(Node.ASKING, "gate fails, or required field unresolved", Node.DISQUALIFIED),
            # The next question, once the answers are stored (`_next_step`).
            Edge(Node.AWAITING, "phone or email in", Node.CHECKING),
            Edge(Node.AWAITING, "all fields in", Node.SUMMARY),
        }
    )

    def on_turn(self, state: ConversationState, turn: TurnInput) -> Turn | Answered:
        return _data_turn(state, turn, asked=state.awaiting)

    def prompt(self, state: ConversationState) -> NextStep:
        return _next_step(state)[1]


HANDLER = Asking()


def _data_turn(
    state: ConversationState, turn: TurnInput, *, asked: FieldId | None
) -> Turn | Answered:
    """The answers in a message: a fix request or a refusal, the pending read-back, then every
    field in the spec's apply order (gates first), a disqualification and the years read-back.
    What follows (the interrupts after the handler, then the next question) is `step`'s.

    Also the summary's, for any message there but a yes or a bare "corregir algo", and the
    consent's, for a yes that answers the first question too. `asked`: the question on screen,
    which a message with no usable answer failed; none at the summary or after the consent."""
    extraction, today = turn.extraction, turn.today
    fix = _screening_fix(turn.fix)
    refused = _refused_field(state, extraction, asked)
    screening = where(state)  # asking or the summary: its after-interrupts run next
    pending = state.pending_confirmation
    if fix is not None and fix in agent().by_id:
        state, extraction, applied = _fix_request(state, extraction, fix, today)
        if not applied:
            asked = None  # a request, not a failed answer to the question that was pending
    else:
        state, extraction = _same_place(state, extraction, today)
    explaining = state.pending_confirmation is not pending  # asked this turn, not answered
    if explaining:
        asked = None
    if refused is not None:
        state, extraction = _refuse(state, extraction, refused, today)
        asked = None
    before = state
    answered: PendingConfirmation | None = None
    if not explaining:
        state, answered = _resolve_pending_confirmation(state, extraction, today)
    if state.outcome != Outcome.DISQUALIFIED:
        # A yes that closed the application ("sí, y tengo carnet") stores nothing after it.
        state = _apply_fields(state, extraction, today, answered, asked)
    closed = state.closed
    if isinstance(closed, Disqualified):
        # A gate close (license, out of area) may be undone by the next message; one for data
        # that could not be resolved is started again instead.
        gate = agent().gate(closed.reason)
        reopenable = gate is not None and gate.reopenable
        state = state.at(Closed(variant=closed.model_copy(update={"reopenable": reopenable})))
        return state, _disqualify(state)
    state = _check_years(state, today)
    if turn.de_escalated and _answered(before, state, extraction):
        # "Perdona, mal día. Tiempo completo": an apology that answers is not a second strike.
        state = state.model_copy(update={"frustration_streak": 0})
    return Answered(state, extraction, screening)


def _answered(
    before: ConversationState, after: ConversationState, extraction: TurnExtraction
) -> bool:
    """An answer turn that stored at least one valid value."""
    return extraction.intent == Intent.ANSWER and after.profile != before.profile


def _refuse(
    state: ConversationState, extraction: TurnExtraction, field: FieldId, today: date
) -> tuple[ConversationState, TurnExtraction]:
    """A refusal is neither a question for the FAQ nor a complaint. A phone or an email
    refused while the other is in (or in the same message) is left out at once: one of the two
    is enough (SAZ-72, D4). Anything else is a try at the question, re-asked with why it is
    asked (`DECLINED`), then with what happens without it (`DECLINED_AGAIN`): required data
    closes after the last try as any failed answer does, and an optional field is left empty
    and flagged (D5, D7)."""
    extraction = extraction.model_copy(update={"question": None, "intent": Intent.ANSWER})
    state = state.model_copy(update={"frustration_streak": 0})
    contact = agent().field(field).type == FieldType.CONTACT
    if contact and _other_contact(state, extraction, field, today):
        skipped = (*state.skipped, field)
        return state.model_copy(update={"skipped": skipped, "contact_notice": field}), extraction
    refused_before = state.hints.get(field) in REFUSALS
    reason = ReasonCode.DECLINED_AGAIN if refused_before else ReasonCode.DECLINED
    return _record_failure(state, field, reason), extraction


def _other_contact(
    state: ConversationState, extraction: TurnExtraction, field: FieldId, today: date
) -> bool:
    """Another contact is in, or valid in this message."""
    for other in agent().contacts:
        if other.id == field:
            continue
        if other.value(state.profile) is not None:
            return True
        candidate = extraction.fields.get(other.id)
        if candidate is not None and isinstance(
            _validate(state, other.id, candidate.value, today), Valid
        ):
            return True
    return False


def _apply_fields(
    state: ConversationState,
    extraction: TurnExtraction,
    today: date,
    answered: PendingConfirmation | None,
    asked: FieldId | None,
) -> ConversationState:
    """`answered`: the read-back this turn answered, so a turn with no new value is not a
    failed answer, and a city named in reply to "do you live in X?" is confirmed. `asked`: the
    question a turn with no value failed."""
    known = _known_fields(extraction)
    for field in known:
        candidate = extraction.fields[field]
        if state.outcome == Outcome.DISQUALIFIED:
            state = _keep_after_close(state, field, candidate.value, today)
            continue
        read_back = answered if answered is not None and answered.field == field else None
        state = _apply_field(state, field, candidate.value, today, candidate.confidence, read_back)
    if state.outcome == Outcome.DISQUALIFIED:
        return state
    unanswered = not known and answered is None and _answering(extraction)
    if unanswered and asked and not _complaint(extraction):
        state = _record_failure(state, asked, None)
    return state


def _keep_after_close(
    state: ConversationState, field: FieldId, raw: object, today: date
) -> ConversationState:
    """A value in the message that closed the application at a gate ("Soy Lucía Pérez, 612...,
    no tengo carnet"): kept when it is valid as given, so the close is not "Sin nombre" for the
    recruiter (QA sweep 2026-10-21). Nothing is read back or asked again; the gates are not
    applied after the close."""
    if agent().field(field).gate is not None:
        return state
    result = _validate(state, field, raw, today)
    if not isinstance(result, Valid) or result.needs_confirmation:
        return state
    return state.model_copy(update={"profile": _store(state.profile, field, result.value)})


def _complaint(extraction: TurnExtraction) -> bool:
    """A frustrated turn with no answer in it ("¿otra pregunta? esto es eterno") is not a try
    at the question: it never counts towards the failures that close the application (QA sweep
    2026-10-21), and the frustration count shortens the flow and offers the pause instead."""
    return extraction.sentiment == Sentiment.FRUSTRATED


def _apply_field(
    state: ConversationState,
    field: FieldId,
    raw: object,
    today: date,
    confidence: float = 1.0,
    read_back: PendingConfirmation | None = None,
) -> ConversationState:
    """`read_back`: the value of this field the candidate was just asked to confirm."""
    spec = agent().field(field)
    confirmed = read_back is not None
    result = _validate(state, field, raw, today)
    if spec.type == FieldType.PERSON_NAME:
        raw, result = _with_first_name(state, field, raw, result)
    location = spec.type == FieldType.LOCATION
    if location and _declined(state, result):
        return _out_of_area(state, field, raw, confidence, read_back)
    if isinstance(result, Valid):
        unsure_no = result.value is False and confidence < spec.min_confidence
        if spec.type == FieldType.CREDENTIAL and unsure_no:
            return _record_failure(state, field, ReasonCode.LICENSE_INVALID)
        closes = _closes(field, result)
        read_back_no = spec.voice_read_back and not confirmed and state.voice
        if closes is not None and read_back_no and field not in state.read_back:
            return _check_no(state, field, closes)
        settled = _is_read_back(result, read_back)
        picked = isinstance(raw, PhoneInput)
        place = location and isinstance(result.value, str)
        said = said_place(raw, str(result.value)) if place else None
        return _accept(state, field, result, picked=picked, settled=settled, today=today, said=said)
    if isinstance(result, Invalid):
        if location:
            return _out_of_area(state, field, raw, confidence, read_back)
        if result.reason == ReasonCode.NAME_NEEDS_SURNAME:
            return _first_name_only(state, field, str(raw))
        return _record_failure(state, field, result.reason)
    state = _record_failure(state, field, result.reason)
    if result.options and field not in state.skipped and state.outcome is None:
        options = tuple(str(option) for option in result.options)
        return _ask_back(state, PendingClarification(field=field, options=options))
    return state


def _out_of_area(
    state: ConversationState,
    field: FieldId,
    raw: object,
    confidence: float,
    read_back: PendingConfirmation | None,
) -> ConversationState:
    """A place outside the service areas disqualifies only once the candidate confirms they
    live there now: "I grew up in LA" must not close the application. So it is read back
    ("¿Vives ahora en LA?"), however sure the extractor was: the read-back is the check, and a
    place it was unsure of ("Los Ángeles, California" at 0.5) never reached it (QA sweep
    2026-10-21). A place named in reply to that read-back closes, unless the extractor was
    unsure of it and it is a new one: then it is read back too. An answer too long to be a
    place asks for the city again, and so does any place when the field closes on none."""
    reason = _out_of_area_reason(agent().field(field))
    if reason is None:
        return _record_failure(state, field, ReasonCode.OUT_OF_AREA)
    if not isinstance(raw, str):
        return _record_failure(state, field, None)
    place = " ".join(raw.split())
    if not place or len(place) > MAX_PLACE_CHARS or len(place.split()) > MAX_PLACE_WORDS:
        return _record_failure(state, field, None)
    if set(fold(place).split()) & NOT_A_PLACE:
        # "Cerca de donde trabajo", "por ahí": no place named, nothing to read back.
        return _record_failure(state, field, None)
    if _confirms_place(place, confidence, read_back):
        return _close(state, Outcome.DISQUALIFIED, reason)
    return _ask_back(state, PendingConfirmation(field=field, value=place))


def _out_of_area_reason(field: FieldSpec) -> ReasonId | None:
    """The close for a place outside the areas, when the location's gate closes on one."""
    return field.gate.close("out_of_area") if field.gate is not None else None


def _confirms_place(place: str, confidence: float, read_back: PendingConfirmation | None) -> bool:
    """`place`, outside the areas, named in reply to "¿Vives ahora en X?": the same place, or
    another the extractor is sure of. Never after a loose-match read-back ("¿Te refieres al
    local de Gràcia?"), which asked about a location, not where they live."""
    if read_back is None or read_back.location_id is not None:
        return False
    return fold(place) == fold(read_back.value) or confidence >= MIN_GATE_CONFIDENCE


def _check_no(state: ConversationState, field: FieldId, reason: ReasonId) -> ConversationState:
    """By voice, a no to a credential is read back once before it closes the application
    ("¿Entonces ahora no tienes carnet en vigor?"): speech to text may have cut the answer to
    its first word, and a bare "no" may have been about something else."""
    pending = PendingConfirmation(field=field, value=reason)
    read_back = (*state.read_back, field)
    return _ask_back(state.model_copy(update={"read_back": read_back}), pending)


def _declined(state: ConversationState, result: Result[Any]) -> bool:
    """A loose match to a location the candidate already said they did not mean."""
    return (
        isinstance(result, Valid)
        and result.needs_confirmation
        and result.value in state.declined_locations
    )


def _is_read_back(result: Valid[Any], read_back: PendingConfirmation | None) -> bool:
    if read_back is None:
        return False
    if isinstance(result.value, StartDate):
        return result.value.start_date.isoformat() == read_back.value
    if agent().field(read_back.field).type == FieldType.CONTACT:
        return result.value == read_back.value
    return read_back.location_id is not None and result.value == read_back.location_id


def _accept(
    state: ConversationState,
    field: FieldId,
    result: Valid[Any],
    *,
    picked: bool = False,
    settled: bool = False,
    today: date | None = None,
    said: str | None = None,
) -> ConversationState:
    """`settled`: the value came again with the answer to its own read-back ("sí, en Cancum"),
    so a loose match or a late date is not read back a second time. `said`: the city's zone
    the candidate named ("Roma"), kept with the location to show it as they said it."""
    spec = agent().field(field)
    closes = _closes(field, result)
    if closes is not None:
        return _close_at_credential(state, field, closes)
    state, result = _flag(state, field, result)

    needs_confirmation = result.needs_confirmation and not settled
    if isinstance(result.value, StartDate) and needs_confirmation:
        start = result.value.start_date
        asked_on = today.isoformat() if today is not None and _rolled_over(start, today) else None
        pending = PendingConfirmation(field=field, value=start.isoformat(), today=asked_on)
        return _ask_back(state, pending)
    if spec.read_back.domain_typo and needs_confirmation and not state.voice:
        # "¿Quisiste decir lucia@gmail.com?" (D2). By voice nothing is spelled back (SAZ-48):
        # the corrected address is kept.
        return _ask_back(state, PendingConfirmation(field=field, value=str(result.value)))
    if spec.read_back.loose_match and needs_confirmation:
        location_id = str(result.value)
        pending = PendingConfirmation(field=field, value=location_id, location_id=location_id)
        return _ask_back(state, pending)

    state = _settled(state, field, PendingClarification).model_copy(
        update={
            "profile": _store(state.profile, field, result.value),
            "skipped": tuple(skipped for skipped in state.skipped if skipped != field),
            "hints": {name: hint for name, hint in state.hints.items() if name != field},
            "fix_field": None if state.fix_field == field else state.fix_field,
        }
    )
    if spec.channel == Channel.PHONE and isinstance(result.value, str):
        state = after_phone(state, result.value, picked=picked)
    if spec.type == FieldType.LOCATION and isinstance(result.value, str):
        assert spec.companion is not None, "a location keeps the place as said"
        profile = state.profile.model_copy(update={spec.companion: said})
        state = after_location(state.model_copy(update={"profile": profile}), result.value)
    return state


def _rolled_over(start: date, today: date) -> bool:
    """A day and month already past this year, read as next year's ("el 1 de octubre" on 21
    October): the candidate may have meant a date gone by, so today's date goes with the
    read-back (D6)."""
    return start.year == today.year + 1 and (start.month, start.day) < (today.month, today.day)


def _first_name_only(state: ConversationState, field: FieldId, name: str) -> ConversationState:
    """Ask for the surname, remembering the first name to join it to."""
    state = state.model_copy(update={"first_name_only": " ".join(name.split())})
    return _record_failure(state, field, ReasonCode.NAME_NEEDS_SURNAME)


def _record_failure(
    state: ConversationState, field: FieldId, reason: ReasonCode | None
) -> ConversationState:
    spec = agent().field(field)
    failures = state.retries.get(field, 0) + 1
    hints = state.hints if reason is None else {**state.hints, field: reason}
    state = state.model_copy(update={"retries": {**state.retries, field: failures}, "hints": hints})
    if failures < spec.max_tries:
        return state
    if spec.required is not None:
        return _close(state, Outcome.DISQUALIFIED, spec.required)
    skipped = (*state.skipped, field)
    state = _settled(state, field, PendingClarification).model_copy(update={"skipped": skipped})
    state = add_quality_flag(state, QualityFlag.UNRESOLVED_FIELD)
    if _no_contact(state):
        return _close(state, Outcome.DISQUALIFIED, DisqualifyReason.NO_CONTACT)
    if spec.type == FieldType.CONTACT:
        state = state.model_copy(update={"contact_notice": field})
    return state


def _no_contact(state: ConversationState) -> bool:
    """Every contact given up on: one of them is enough to carry on."""
    contacts = agent().contacts
    given_up = all(contact.id in state.skipped for contact in contacts)
    unknown = all(contact.value(state.profile) is None for contact in contacts)
    return bool(contacts) and given_up and unknown


def _resolve_pending_confirmation(
    state: ConversationState, extraction: TurnExtraction, today: date
) -> tuple[ConversationState, PendingConfirmation | None]:
    """Settle a read-back: a start date beyond 90 days, a place outside the service areas, a
    loose city match, years of experience, an email with a mistyped domain, or by voice a no
    to the license question.

    Returns the state and the read-back this turn answered, if any. A yes accepts the value or
    closes the application; a no or a new value asks again or applies the value.
    """
    pending = state.pending_confirmation
    if pending is None or extraction.intent not in ANSWERING_INTENTS:
        return state, None
    spec = agent().field(pending.field)
    state = _settled(state, pending.field, PendingConfirmation)
    if spec.type == FieldType.YEARS:
        return _resolve_years(state, spec, pending, extraction), pending
    if pending.explain:
        # Yes: the location stays. A new place is applied as usual; anything else asks again.
        if extraction.intent == Intent.CONFIRM or pending.field in extraction.fields:
            return state, pending
        return _forget(state, pending.field), pending
    if extraction.intent != Intent.CONFIRM or pending.field in extraction.fields:
        if pending.location_id and extraction.intent != Intent.CONFIRM:
            declined = (*state.declined_locations, pending.location_id)
            state = state.model_copy(update={"declined_locations": declined})
        return state, pending
    if spec.type == FieldType.LOCATION and pending.location_id:
        return _accept(state, pending.field, Valid(pending.location_id)), pending
    if spec.type == FieldType.LOCATION:
        return _close(state, Outcome.DISQUALIFIED, _out_of_area_reason(spec)), pending
    if spec.type == FieldType.CREDENTIAL:
        return _close_at_credential(state, pending.field, pending.value), pending
    if spec.type == FieldType.CONTACT:
        return _accept(state, pending.field, Valid(pending.value)), pending
    result = validate_date(
        pending.value,
        today=today,
        confirmed=True,
        late_after=spec.late_after_days,
        read_back_after=spec.read_back.after_days,
    )
    if isinstance(result, Valid):
        return _accept(state, pending.field, result), pending
    return state, pending


def _forget(state: ConversationState, field: FieldId) -> ConversationState:
    """Drop a value the candidate says is wrong, so it is asked again with a short lead. A
    field left out earlier is asked again too. A field not asked on the answer it depends on
    (platforms with no experience) is asked through that answer, which decides whether it
    is asked, and forgetting that answer forgets its empty value too."""
    spec = _through(agent().field(field), state.profile)
    field = spec.id
    attributes = spec.attrs
    held = spec.value(state.profile)
    for later, condition in agent().dependents(field):
        if held is not None and not condition.holds(held):
            attributes = (*attributes, later.attr)  # none were asked: the answer meant none
    profile = state.profile.model_copy(update=dict.fromkeys(attributes))
    state = _settled(state, field, PendingConfirmation)
    update: dict[str, Any] = {
        "profile": profile,
        "fix_field": field,
        "skipped": tuple(skipped for skipped in state.skipped if skipped != field),
        "hints": {name: hint for name, hint in state.hints.items() if name != field},
    }
    if spec.type == FieldType.YEARS:
        update["confirmed_experience"] = None
    return state.model_copy(update=update)


def _through(field: FieldSpec, profile: CandidateProfile) -> FieldSpec:
    """The field a fix of `field` asks again: the earlier answer it depends on, when that
    answer means it is not asked; else itself."""
    for source, condition in field.ask_if.items():
        held = agent().field(source).value(profile)
        if held is not None and not condition.holds(held):
            return agent().field(source)
    return field


def _fix_request(
    state: ConversationState, extraction: TurnExtraction, field: FieldId, today: date
) -> tuple[ConversationState, TurnExtraction, bool]:
    """ "Mi email está mal": a new value in the message is applied as usual (True); without one
    (or with the same value read again) the field is dropped and asked again. It is a request,
    never a question for the FAQ ("¿la zona no es correcta?").

    The city they already have, named again ("La zona es Roma") or asked about ("¿por qué pone
    Cuauhtémoc?"), is explained instead: the place is in that location's area (`_explain`)."""
    asked = extraction.intent == Intent.QUESTION or extraction.question is not None
    extraction = extraction.model_copy(
        update={"question": None, "sub_questions": [], "intent": Intent.CORRECTION}
    )
    candidate = extraction.fields.get(field)
    spec = agent().field(field)
    current = spec.value(state.profile)
    if spec.type == FieldType.LOCATION and current is not None:
        if candidate is None and asked:
            return *_explain(state, spec, extraction, spec.companion_value(state.profile)), False
        if candidate is not None and _same_value(state, field, candidate.value, current, today):
            return *_explain(state, spec, extraction, said_place(candidate.value, current)), False
    if candidate is not None:
        if _new_value(state, spec, candidate, current, today):
            return state, extraction, True
        fields = {name: value for name, value in extraction.fields.items() if name != field}
        extraction = extraction.model_copy(update={"fields": fields})
    return _forget(state, field), extraction, False


def _new_value(
    state: ConversationState,
    field: FieldSpec,
    candidate: FieldCandidate,
    current: object,
    today: date,
) -> bool:
    """A value with the request to apply: not the one they have, not the request's own words
    read as the value ("El celular está mal" as the phone), and valid, unless it is for a gate
    (a place outside the areas still goes to its read-back). A credential the extractor is
    unsure of ("quiero cambiar lo de la licencia" read as a no at 0.5) is none: asked again."""
    raw = candidate.value
    if isinstance(raw, str) and field_to_fix(raw, bare=True) is not None:
        return False  # the request read as the value, or the field's name alone ("La zona")
    if raw in ([], ""):  # "Las apps están mal" read as no platforms: no value given
        return False
    unsure = candidate.confidence < field.min_confidence
    if field.type == FieldType.CREDENTIAL and unsure:
        return False
    if _same_value(state, field.id, raw, current, today):
        return False
    return field.gate is not None or isinstance(_validate(state, field.id, raw, today), Valid)


def _same_place(
    state: ConversationState, extraction: TurnExtraction, today: date
) -> tuple[ConversationState, TurnExtraction]:
    """A correction (or at the summary, any answer but a yes) that names the place they
    already have: "La zona es Roma…" with the Cuauhtémoc location. Explained, not applied
    again with nothing changed and the summary sent once more."""
    field = agent().one(FieldType.LOCATION)
    if field is None:
        return state, extraction
    candidate = extraction.fields.get(field.id)
    location_id = field.value(state.profile)
    if candidate is None or location_id is None or state.pending_confirmation is not None:
        return state, extraction
    correcting = extraction.intent == Intent.CORRECTION
    at_summary = state.stage == Stage.CONFIRM and extraction.intent != Intent.CONFIRM
    if not (correcting or at_summary):
        return state, extraction
    if not _same_value(state, field.id, candidate.value, location_id, today):
        return state, extraction
    return _explain(state, field, extraction, said_place(candidate.value, location_id))


def _explain(
    state: ConversationState, field: FieldSpec, extraction: TurnExtraction, said: str | None
) -> tuple[ConversationState, TurnExtraction]:
    """Why the place is that location, and whether that is right (`ConfirmValue.explain`)."""
    location_id = field.value(state.profile)
    assert location_id is not None
    said = said or field.companion_value(state.profile)
    pending = PendingConfirmation(
        field=field.id, value=said or "", location_id=location_id, explain=True
    )
    fields = {name: v for name, v in extraction.fields.items() if name != field.id}
    extraction = extraction.model_copy(
        update={"fields": fields, "question": None, "sub_questions": []}
    )
    return _ask_back(state, pending), extraction


def _resolve_years(
    state: ConversationState,
    field: FieldSpec,
    pending: PendingConfirmation,
    extraction: TurnExtraction,
) -> ConversationState:
    """A yes, or the same number again, keeps the years read back; anything else drops them,
    and a new number is applied as usual."""
    years = float(pending.value)
    candidate = extraction.fields.get(field.id)
    if candidate is not None:
        result = validate_years(candidate.value, most=field.most)
        if not (isinstance(result, Valid) and result.value == years):
            return state
    elif extraction.intent != Intent.CONFIRM:
        return state
    # `_check_years` flags the kept years when a check doubts them.
    profile = state.profile.model_copy(update={field.attr: years})
    return state.model_copy(update={"profile": profile, "confirmed_experience": years})


def _check_years(state: ConversationState, today: date) -> ConversationState:
    """Read back years that are hard to believe before keeping them: more than the field's
    read-back threshold, or what one of its checks doubts (more years than a platform the
    candidate named has existed in the country). The years wait in the read-back, not in the
    profile, until the candidate confirms them; confirmed, a doubt is flagged for the
    recruiter instead."""
    field = agent().one(FieldType.YEARS)
    if field is None:
        return state
    profile = state.profile
    years = field.value(profile)
    if years is None:
        return state
    doubt = _doubt(field, profile, years, state, today)
    if years == state.confirmed_experience:
        unverified = field.companion
        if doubt is not None and unverified and not getattr(profile, unverified):
            profile = profile.model_copy(update={unverified: True})
            return state.model_copy(update={"profile": profile})
        return state
    if state.pending_confirmation is not None:
        return state
    most = field.read_back.over
    if doubt is None and (most is None or years <= most):
        return state
    pending = PendingConfirmation(field=field.id, value=f"{years:g}", platform=doubt)
    profile = profile.model_copy(update={field.attr: None})
    return _ask_back(state.model_copy(update={"profile": profile}), pending)


def _doubt(
    field: FieldSpec, profile: CandidateProfile, years: float, state: ConversationState, today: date
) -> Platform | None:
    """What the field's first doubting check finds in the years (`spec.Hooks.checks`)."""
    checks = agent().hooks.checks
    found = (checks[name](profile, years, state.country, today) for name in field.checks)
    return next((doubt for doubt in found if doubt is not None), None)


def _ask_back(
    state: ConversationState, pending: PendingConfirmation | PendingClarification
) -> ConversationState:
    """Ask about `pending` next: a read-back of a value, or which of several options. A
    read-back already pending this turn goes first: the options are asked when the field
    comes up again."""
    if isinstance(pending, PendingClarification) and state.pending_confirmation is not None:
        return state
    if isinstance(pending, PendingConfirmation):
        return state.at(AskingPhase(field=pending.field, confirmation=pending))
    return state.at(AskingPhase(field=pending.field, clarification=pending))


def _settled(
    state: ConversationState,
    field: FieldId,
    kind: type[PendingConfirmation] | type[PendingClarification],
) -> ConversationState:
    """No `kind` pending for `field` any more: the next question is worked out again."""
    pending = (
        state.pending_confirmation if kind is PendingConfirmation else state.pending_clarification
    )
    if pending is not None and pending.field == field:
        return state.at(AskingPhase(field=field))
    return state
