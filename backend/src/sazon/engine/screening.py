"""The screening's fields, shared by the states that ask, check and summarize them: the
order they are asked in, how a value is read and stored, and what is asked next."""

from datetime import date
from typing import Any

from sazon.engine.actions import (
    AskField,
    CheckDuplicate,
    Clarify,
    ConfirmIdentity,
    ConfirmSummary,
    ConfirmValue,
    Prompt,
    SkipEmail,
    SkipPhone,
)
from sazon.engine.country import posting_country
from sazon.engine.models import (
    CandidateProfile,
    FieldId,
    Outcome,
    PhoneInput,
    QualityFlag,
    Stage,
    TurnExtraction,
)
from sazon.engine.service_areas import shared_city
from sazon.engine.spec import Channel, FieldSpec, FieldType, agent
from sazon.engine.state import (
    Asking,
    Contact,
    ConversationState,
    CountrySource,
    InterviewFix,
    ReasonId,
    Summary,
    add_quality_flag,
)
from sazon.engine.text import fold
from sazon.engine.turn import Turn, _close, _meta
from sazon.engine.ui import field_options, field_ui, summary_ui, yes_no_ui
from sazon.engine.validators import (
    Country,
    Invalid,
    ReasonCode,
    Result,
    Valid,
    validate_choice,
    validate_choices,
    validate_credential,
    validate_date,
    validate_email,
    validate_free_text,
    validate_location,
    validate_person_name,
    validate_phone,
    validate_years,
    validate_yes_no,
)

CREDENTIAL_READINGS = {
    ReasonCode.LICENSE_IN_PROGRESS: "in_progress",
    ReasonCode.LICENSE_NOT_VALID: "not_valid",
}
"""A credential's no, read by why: any other is a plain no."""
BUTTONS_FROM_RETRY = 2

type NextStep = (
    AskField | Clarify | ConfirmValue | ConfirmSummary | CheckDuplicate | ConfirmIdentity
)


def _screening_fix(fix: FieldId | InterviewFix | None) -> FieldId | None:
    """Before any interview, "cambiar la hora" is about the hours they can work."""
    if isinstance(fix, InterviewFix):
        return agent().interview.blocks if fix == InterviewFix.TIME else None
    return fix


def _refused_field(
    state: ConversationState, extraction: TurnExtraction, field: FieldId | None
) -> FieldId | None:
    """The field asked for (`field`), when the candidate would rather not give it ("no quiero
    dar mi correo", "prefiero no decirlo") and the message brings no value for it."""
    if not extraction.declines_field or field is None or not state.consented:
        return None
    if state.stage == Stage.DONE or state.pending_confirmation is not None:
        return None
    return None if field in extraction.fields else field


def _tell_contact_dropped(state: ConversationState, next_step: Prompt) -> Turn:
    """The line for a phone or email the screening carries on without, before `next_step`."""
    field = agent().field(state.contact_notice or "")
    state = state.model_copy(update={"contact_notice": None})
    meta = _meta(state)
    if field.channel == Channel.EMAIL:
        return state, SkipEmail(follow_up=next_step, ui=next_step.ui, **meta)
    has_email = state.profile.email is not None
    return state, SkipPhone(has_email=has_email, follow_up=next_step, ui=next_step.ui, **meta)


def _validate(state: ConversationState, field: FieldId, raw: object, today: date) -> Result[Any]:
    """`raw` read as the field's type says."""
    spec = agent().field(field)
    match spec.type:
        case FieldType.PERSON_NAME:
            return validate_person_name(raw)
        case FieldType.CONTACT if spec.channel == Channel.EMAIL:
            return validate_email(raw)
        case FieldType.CONTACT if isinstance(raw, PhoneInput):
            return validate_phone(raw.number, country=raw.country, fallback=False)
        case FieldType.CONTACT:
            return validate_phone(raw, country=state.country)
        case FieldType.CREDENTIAL:
            return validate_credential(raw, spec.words)
        case FieldType.YES_NO:
            return validate_yes_no(raw)
        case FieldType.LOCATION:
            pending = state.pending_clarification
            if pending and pending.field == field and raw in pending.options:
                return Valid(raw)
            return validate_location(raw, country=_city_country(state))
        case FieldType.MULTI_ENUM if spec.reader is not None:
            return agent().hooks.readers[spec.reader](raw)
        case FieldType.MULTI_ENUM:
            return validate_choices(raw, spec.words, other=spec.other, exclusive=spec.exclusive)
        case FieldType.ENUM:
            return validate_choice(raw, spec.words)
        case FieldType.YEARS:
            return validate_years(raw, most=spec.most)
        case FieldType.DATE:
            return validate_date(
                raw,
                today=today,
                late_after=spec.late_after_days,
                read_back_after=spec.read_back.after_days,
            )
        case FieldType.FREE_TEXT:
            return validate_free_text(raw)


def _reading(field: FieldId, result: Valid[Any]) -> str | None:
    """A credential's answer as its gate reads it: yes, or no and why (`CREDENTIAL_READINGS`);
    None for any other field."""
    if agent().field(field).type != FieldType.CREDENTIAL or not isinstance(result.value, bool):
        return None
    if result.value:
        return "yes"
    return CREDENTIAL_READINGS.get(result.note or ReasonCode.LICENSE_INVALID, "no")


def _closes(field: FieldId, result: Valid[Any]) -> ReasonId | None:
    """The close a credential's answer leads to at its gate; None when it passes, or for any
    other field."""
    gate, reading = agent().field(field).gate, _reading(field, result)
    return gate.close(reading) if gate is not None and reading is not None else None


def _flag(
    state: ConversationState, field: FieldId, result: Valid[Any]
) -> tuple[ConversationState, Valid[Any]]:
    """A credential's reading its gate flags ("in_progress" for a certificate that takes
    minutes to get) is kept as the value, with a gate flag for the recruiter, and the
    screening goes on; any other value is kept as it is."""
    gate, reading = agent().field(field).gate, _reading(field, result)
    if gate is None or reading is None or not gate.flags(reading):
        return state, result
    return add_quality_flag(state, QualityFlag.GATE_FLAG), Valid(reading)


def _close_at_credential(
    state: ConversationState, field: FieldId, reason: ReasonId
) -> ConversationState:
    """Closed at a credential's gate, with its no kept."""
    profile = state.profile.model_copy(update={agent().field(field).attr: False})
    return _close(state.model_copy(update={"profile": profile}), Outcome.DISQUALIFIED, reason)


def _zone_question(field: FieldId, options: tuple[str, ...]) -> str | None:
    """The city to ask the zone of: many locations of a location field, all in one city
    ("DF")."""
    most = agent().field(field).zone_question_from
    if most is None or len(options) < most:
        return None
    return shared_city(options)


def _city_country(state: ConversationState) -> Country | None:
    if state.country_source == CountrySource.POSTING:
        return posting_country(state.context)
    return None


def _store(profile: CandidateProfile, field: FieldId, value: object) -> CandidateProfile:
    """The value in the field's attributes (a tuple fills them in order), and the fields asked
    only on some answers to it (`ask_if`) settled: none of them while they are not asked (an
    empty choice), asked again once they are."""
    spec = agent().field(field)
    values = value if isinstance(value, tuple) else (value,)
    stored = profile.model_copy(update=dict(zip(spec.attrs, values, strict=False)))
    before = spec.value(profile)
    for later, condition in agent().dependents(field):
        held = later.value(stored)
        now = condition.holds(spec.value(stored))
        if not now and held is None:
            stored = stored.model_copy(update={later.attr: _empty(later)})
        elif now and before is not None and not condition.holds(before):
            # From no to yes ("está mal, llevo 2 años"): the field was never asked.
            stored = stored.model_copy(update={later.attr: held or None})
    return stored


def _empty(field: FieldSpec) -> object:
    """What a field not asked holds: an empty choice for a `multi_enum`, else nothing."""
    return [] if field.type == FieldType.MULTI_ENUM else None


def _with_first_name(
    state: ConversationState, field: FieldId, raw: object, result: Result[Any]
) -> tuple[object, Result[Any]]:
    """The surname after a lone first name ("Carlos", then "Sánchez Pérez") is joined to it.
    An answer that repeats the first name ("Carlos Sánchez") or starts with a word of it
    ("Juan Carlos", then "Juan Sánchez") is taken as given."""
    first = state.first_name_only
    needs_surname = isinstance(result, Invalid) and result.reason == ReasonCode.NAME_NEEDS_SURNAME
    surname = isinstance(result, Valid) or needs_surname
    named = agent().field(field).value(state.profile)
    if not first or not surname or not isinstance(raw, str) or named:
        return raw, result
    words = fold(raw).split()
    if f" {fold(first)} " in f" {fold(raw)} " or (words and words[0] in fold(first).split()):
        return raw, result
    joined = f"{first} {raw}"
    return joined, validate_person_name(joined)


def _same_value(
    state: ConversationState, field: FieldId, raw: object, value: object, today: date
) -> bool:
    """`raw`, once validated, is `value` ("PPS@gmail.com" is "pps@gmail.com"), as the profile
    holds it: a start date's day, the platforms' list."""
    result = _validate(state, field, raw, today)
    if not isinstance(result, Valid):
        return False
    held = result.value
    if isinstance(held, tuple):
        held = held[0]  # a start date's day, the platforms' list
    return held == value


def _next_step(state: ConversationState) -> tuple[ConversationState, NextStep]:
    """What the screening asks next: the question pending in the phase ("¿Quieres continuar
    como Lucía?", a read-back, a choice between options), else the duplicate check due, the
    first field missing, or the summary."""
    meta = _meta(state)
    if state.pending_identity is not None:
        return state, _confirm_identity(state)
    confirmation = state.pending_confirmation
    if confirmation:
        # "¿Me confirma cuántos años ha repartido con Rappi?" is answered with a number.
        asks_number = confirmation.platform is not None
        action = ConfirmValue(
            field=confirmation.field,
            value=confirmation.value,
            platform=confirmation.platform,
            location_id=confirmation.location_id,
            today=date.fromisoformat(confirmation.today) if confirmation.today else None,
            explain=confirmation.explain,
            ui=None if asks_number else yes_no_ui(state.language),
            **meta,
        )
        return state, action

    clarification = state.pending_clarification
    if clarification:
        field = clarification.field
        action = Clarify(
            field=field,
            options=clarification.options,
            city=_zone_question(field, clarification.options),
            ui=field_ui(field, clarification.options, state.language, state.country),
            **meta,
        )
        return state, action

    for field in agent().fields:
        if _needs_duplicate_check(state, field):
            state = state.at(Contact())
            profile = state.profile
            return state, CheckDuplicate(phone=profile.phone, email=profile.email, **meta)
        if _is_missing(state, field):
            return _ask(state, field.id)

    state = state.at(Summary())
    ui = summary_ui(state.profile, state.skipped, state.language, state.country)
    return state, ConfirmSummary(profile=state.profile, voice=state.voice, ui=ui, **meta)


def _ask(state: ConversationState, field: FieldId) -> tuple[ConversationState, AskField]:
    meta = _meta(state)
    retry = state.retries.get(field, 0)
    options: tuple[str, ...] = ()
    if retry >= BUTTONS_FROM_RETRY or meta["simplify"]:
        options = field_options(field, state.country)
    ui = field_ui(field, options, state.language, state.country)
    state = state.at(Asking(field=field))
    action = AskField(
        field=field,
        retry=retry,
        hint=state.hints.get(field),
        options=options,
        again=state.fix_field == field,
        ui=ui,
        **meta,
    )
    return state, action


def _needs_duplicate_check(state: ConversationState, field: FieldSpec) -> bool:
    """Look for an open application right after the first contact, before the next is asked;
    with none, once the next is in. One given after a first that matched nothing is looked up
    too, before the question after the contacts."""
    profile = state.profile
    contacts = agent().contacts
    if any(field.id == contact.id for contact in contacts[1:]):
        return not state.duplicate_checked and contacts[0].value(profile) is not None
    after = agent().after_contacts
    if after is None or field.id != after.id:
        return False
    if not state.duplicate_checked:
        return any(contact.value(profile) is not None for contact in contacts)
    return state.duplicate_email_unchecked and profile.email is not None


def _is_missing(state: ConversationState, field: FieldSpec) -> bool:
    if field.id in state.skipped:
        return False
    return agent().asked(field, state.profile) and field.value(state.profile) is None


def _confirm_identity(state: ConversationState) -> ConfirmIdentity:
    match = state.pending_identity
    assert match is not None
    meta = _meta(state)
    if match.country is not None:
        meta["country"] = match.country  # the application's location decides the register
    return ConfirmIdentity(
        name=match.name, matched_by=match.matched_by, ui=yes_no_ui(state.language), **meta
    )
