from collections.abc import Mapping
from enum import StrEnum
from functools import cache, cached_property
from typing import Annotated, Any, Final, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sazon.engine.models import (
    CandidateProfile,
    FieldId,
    InterviewMode,
    Language,
    MatrixLanguage,
    Outcome,
    Platform,
    QualityFlag,
    QuestionTopic,
    Stage,
    StepId,
)
from sazon.engine.slots import Booked, Slot, SlotQuery
from sazon.engine.validators import Country, ReasonCode


class InterviewFix(StrEnum):
    """The candidate asks to change the interview itself ("quiero cambiar la hora de la
    entrevista", "cambiar la modalidad"), as `text.field_to_fix` names it."""

    TIME = "interview"
    MODE = "interview_mode"


class CountrySource(StrEnum):
    """Where the conversation's country came from, weakest last."""

    LOCATION = "location"
    POSTING = "posting"
    PHONE_PICKER = "phone_picker"
    PHONE = "phone"
    TIMEZONE = "timezone"
    LOCALE = "locale"
    DEFAULT = "default"


type ReasonId = str
"""Why an application closed: one of the engine's own (`DisqualifyReason`), or a reason the
agent spec gives a gate's close or a required field's last failed try ("no_license")."""


class DisqualifyReason(StrEnum):
    """The closes every agent has, whatever its fields (`ReasonId`)."""

    POLICY_VIOLATION = "policy_violation"
    """An attempt to manipulate the assistant (prompt injection), closed automatically."""
    NO_CONTACT = "no_contact"
    """Neither a phone nor an email: nobody could reach them about an interview."""


class ConversationContext(BaseModel):
    """What the page that opened the chat knows: the job posting and browser hints."""

    model_config = ConfigDict(frozen=True)

    country: Country | None = None
    location_id: str | None = None
    language: Language | None = None
    timezone: str | None = None
    locale: str | None = None


class PendingClarification(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: FieldId
    options: tuple[str, ...]


class PendingQuestion(BaseModel):
    """A question asked in the turn that triggered the duplicate check, answered after it."""

    model_config = ConfigDict(frozen=True)

    question: str | None
    topic: QuestionTopic | None = None
    parts: tuple[str, ...] = ()
    """The sub-questions of a question that asks several things; empty for one question."""


class DuplicateMatch(BaseModel):
    """An open application with the phone or email just given (D10). Ana asks whether to carry
    on as that person before anything of it is said; a yes resumes it, a no starts a new one."""

    model_config = ConfigDict(frozen=True)

    application_id: str
    name: str | None = None
    """The application's first name: "¿Quieres continuar como Lucía?"."""
    matched_by: Literal["phone", "email"] = "phone"
    country: Country | None = None
    """The country of the application's location, once it has one: the question is asked in
    its register, as everything after a yes is."""
    retries: int = 0


class PendingConfirmation(BaseModel):
    """A value read back to the candidate before it is kept. What `value` holds by `field`:

    - `start_date`: the ISO date, more than 90 days away.
    - `city` with `location_id`: the location id a place matched only loosely.
    - `city` without it: the place outside the service areas, as the candidate named it.
    - `experience_years`: the years, as `%g` ("20", "1.5").
    - `email`: the address with its mistyped domain corrected ("lucia@gmail.com").
    - a `credential` (voice only): the reason a confirmed no would close with.
    """

    model_config = ConfigDict(frozen=True)

    field: FieldId
    value: str
    platform: Platform | None = None
    """`experience_years` longer than this platform has existed in the country."""
    location_id: str | None = None
    """`city`: the one location a place matched only loosely ("Cancum"); a yes picks it. Without
    it, `value` is a place outside the service areas and a yes closes the application."""
    today: str | None = None
    """`start_date`: a day and month already past this year, read as next year's ("el 1 de
    octubre" on 21 October): the ISO date it was asked on, said with the read-back (D6)."""
    explain: bool = False
    """`city` with `location_id`: the candidate corrected or asked about the place, and it is the
    location they already have ("La zona es Roma" with Cuauhtémoc): why it is that location,
    and whether that is right. `value` is the place as they said it. A yes keeps it, a no asks
    the city again."""


# --- the phase: which state the conversation is in, with the data only that state has ---


class Strict(BaseModel):
    """Frozen, and copied only with its own fields: `model_copy` would set any other name
    silently, as the flat fields of version 1 were. A copy with new values works its cached
    properties out again (an agent spec's fields by id, its quality flags)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        unknown = (update or {}).keys() - type(self).model_fields.keys()
        if unknown:
            raise TypeError(f"not fields of {type(self).__name__}: {', '.join(sorted(unknown))}")
        copied = super().model_copy(update=update, deep=deep)
        for name in _cached(type(self)) if update else ():
            vars(copied).pop(name, None)  # pyright: ignore[reportAttributeAccessIssue]
        return copied


@cache
def _cached(model: type[BaseModel]) -> frozenset[str]:
    """The names of a model's cached properties, which a copy holds as they were."""
    return frozenset(
        name
        for cls in model.__mro__
        for name, member in vars(cls).items()
        if isinstance(member, cached_property)
    )


class Consent(Strict):
    """The privacy notice, waiting for a yes."""

    kind: Literal["consent"] = "consent"


class Asking(Strict):
    """A screening question on screen: `field` awaits its answer, a read-back of it its yes or
    no (`confirmation`), or a choice between options for it (`clarification`)."""

    kind: Literal["asking"] = "asking"
    field: FieldId
    confirmation: PendingConfirmation | None = None
    clarification: PendingClarification | None = None

    @model_validator(mode="after")
    def _one_pending(self) -> Self:
        pending = [p for p in (self.confirmation, self.clarification) if p is not None]
        if len(pending) > 1:
            raise ValueError("a read-back and a choice between options are never both pending")
        if pending and pending[0].field != self.field:
            raise ValueError(f"a pending {pending[0].field} while asking {self.field}")
        return self


class Contact(Strict):
    """The open applications with the phone or email just given: the lookup is due
    (`checking`), or "¿Quieres continuar como Lucía?" waits for its answer (`match`, D10)."""

    kind: Literal["contact"] = "contact"
    match: DuplicateMatch | None = None
    parked_question: PendingQuestion | None = None
    """A question asked in the turn that asked for the lookup, answered after it."""

    @model_validator(mode="after")
    def _checking_only(self) -> Self:
        if self.match is not None and self.parked_question is not None:
            raise ValueError("a question waits only on the lookup, not on a match")
        return self


class Summary(Strict):
    """Every field in: the card with them, waiting for a yes."""

    kind: Literal["summary"] = "summary"


class ChoosingMode(Strict):
    """Qualified: the modes the location offers are looked up (`available_modes` empty), or the
    choice between two waits for its answer."""

    kind: Literal["choosing_mode"] = "choosing_mode"
    retries: int = 0
    parked_question: PendingQuestion | None = None


class Offering(Strict):
    """The interview's times: looked up (`slots` empty), or on offer. `booking` is the interview
    already booked while the candidate moves it, kept until a new time is."""

    kind: Literal["offering"] = "offering"
    slots: tuple[Slot, ...] = ()
    offers: int = 0
    """Offers made this round: one more follows "none work"."""
    retries: int = 0
    """Unclear picks."""
    which_asked: bool = False
    """"Hay más de uno así" was asked about these offers: never twice. Words that fit several
    of them again get the buttons and a one-line ask."""
    booking: Booked | None = None
    cancel_offered: bool = False
    """"¿La cancelo o prefieres otra hora?" waits for its answer (`booking` set)."""
    parked_question: PendingQuestion | None = None

    @model_validator(mode="after")
    def _cancel_needs_booking(self) -> Self:
        if self.cancel_offered and self.booking is None:
            raise ValueError("a cancel is offered only with an interview booked")
        return self


class Idle(Strict):
    kind: Literal["idle"] = "idle"


class Amending(Strict):
    """A detail being corrected after booking, asked again, and the tries at it."""

    kind: Literal["amending"] = "amending"
    field: FieldId
    retries: int = 0


class Moving(Strict):
    """A new location named: moving there (it cancels the interview and offers times there)
    waits for a yes. `said`: the place as the candidate named it."""

    kind: Literal["moving"] = "moving"
    location_id: str
    said: str | None = None


class CancelConfirm(Strict):
    """ "Cancela la entrevista": "¿La cancelo o prefieres otra hora?" waits for its answer. A
    cancel or a yes cancels it; anything else is read in `then`, the sub-state it interrupted."""

    kind: Literal["cancel_confirm"] = "cancel_confirm"
    then: Annotated[Amending | Moving, Field(discriminator="kind")] | None = None


class InterviewBooked(Strict):
    """The interview is booked. The candidate may move it, switch its mode, correct a detail,
    change the location or cancel it."""

    kind: Literal["booked"] = "booked"
    booking: Booked
    sub: Annotated[Idle | Amending | Moving | CancelConfirm, Field(discriminator="kind")] = Idle()


class Interview(Strict):
    kind: Literal["interview"] = "interview"
    sub: Annotated[ChoosingMode | Offering | InterviewBooked, Field(discriminator="kind")]


class Disqualified(Strict):
    """Closed at a gate, for required data that could not be resolved, or for a manipulation
    attempt. `reopenable`: the turn before closed it at a gate, and this message may undo it by
    contradicting the answer or correcting a field. Out of area, `talent_pool_opt_in` is the
    answer to keeping the data for future vacancies, None until given."""

    kind: Literal["disqualified"] = "disqualified"
    reason: ReasonId
    reopenable: bool = False
    talent_pool_opt_in: bool | None = None

    @model_validator(mode="after")
    def _gate_only(self) -> Self:
        from sazon.engine.spec import agent  # the spec reads the reasons defined here

        gate = agent().gate(self.reason)
        if self.reopenable and (gate is None or not gate.reopenable):
            raise ValueError(f"a {self.reason} close is not reopened")
        if self.talent_pool_opt_in is not None and not agent().talent_pool(self.reason):
            raise ValueError(f"the talent pool is not offered on a {self.reason} close")
        return self


class Qualified(Strict):
    """Qualified with no interview booked: no time was free, none suited (`slots_declined`),
    the candidate stopped answering or opted out, or the interview was dropped for abuse."""

    kind: Literal["qualified"] = "qualified"
    slots_declined: bool = False
    opted_out: bool = False


class Withdrawn(Strict):
    """The candidate cancelled their booked interview, by asking or by opting out."""

    kind: Literal["withdrawn"] = "withdrawn"
    opted_out: bool = False


class Abandoned(Strict):
    """Closed before qualifying: silence, an opt-out ("lo dejo": `opted_out`, this
    conversation stays closed), abuse, or "Empezar de nuevo" (`restarted`: closed for good and
    left out of the analytics). `drop_off`: the funnel step it stopped at. `match`: a "¿Quieres
    continuar como Lucía?" still unanswered, asked again when it reopens."""

    kind: Literal["abandoned"] = "abandoned"
    drop_off: StepId
    match: DuplicateMatch | None = None
    opted_out: bool = False
    restarted: bool = False


class DeclinedConsent(Strict):
    kind: Literal["declined_consent"] = "declined_consent"


class HandedOver(Strict):
    """The candidate said yes to "¿Quieres continuar como Lucía?": the matched application
    carries on, and this conversation holds none of its own."""

    kind: Literal["handed_over"] = "handed_over"
    application_id: str


Ended = Qualified | Withdrawn | Abandoned | DeclinedConsent | HandedOver


class Closed(Strict):
    kind: Literal["closed"] = "closed"
    variant: Annotated[Disqualified | Ended, Field(discriminator="kind")]


type Phase = Annotated[
    Consent | Asking | Contact | Summary | Interview | Closed, Field(discriminator="kind")
]


VERSION = 2
"""The stored shape: 2 carries the `phase`; 1 kept it as a `stage` and flags (`upgrade.py`)."""


class ConversationState(Strict):
    """Everything the engine needs between turns. Never mutated; `step` returns a new copy.

    `phase` is the state the conversation is in, with what only that state needs. The rest is
    what every state shares: the profile, the context, the counters, and the memories of what
    was said or offered before (they outlive the state that set them). `stage`, `outcome` and
    the other properties read the phase as the flat state stored them before version 2.
    """

    state_version: int = VERSION
    phase: Phase = Consent()
    profile: CandidateProfile = CandidateProfile()
    context: ConversationContext = ConversationContext()

    country: Country = "ES"
    country_source: CountrySource = CountrySource.DEFAULT
    language: Language = Language.ES
    language_locked: bool = False
    recent_matrix: tuple[MatrixLanguage, ...] = ()

    consented: bool = False
    # A first name given without a surname: a lone surname in the next answer is joined to it.
    first_name_only: str | None = None
    retries: Mapping[FieldId, int] = {}
    hints: Mapping[FieldId, ReasonCode] = {}
    skipped: tuple[FieldId, ...] = ()
    # A field the candidate said is wrong ("mi email está mal"): asked again with a short lead
    # until it has a new value, also once an application abandoned meanwhile reopens.
    fix_field: FieldId | None = None
    # A phone or email given up on this turn (refused with the other in, or failed its tries):
    # the next message says the screening carries on without it, after any lookup or the
    # warning about an earlier manipulation close (D4).
    contact_notice: FieldId | None = None
    # Locations the candidate said they did not mean ("¿Se refiere a la sucursal de Álvaro
    # Obregón?" "No"): the same loose match is not offered again.
    declined_locations: tuple[str, ...] = ()
    duplicate_checked: bool = False
    # The check ran on the phone alone: an email given later is looked up too.
    duplicate_email_unchecked: bool = False
    # Applications the candidate said are not theirs ("¿Quieres continuar como Lucía?" "No",
    # D10): never offered again in this conversation.
    declined_applications: tuple[str, ...] = ()
    # Earlier applications with this phone or email closed for a manipulation attempt (D11),
    # as the duplicate check counted them, and whether the warning about it has been given.
    policy_closes: int = 0
    policy_warned: bool = False

    abuse_strikes: int = 0
    frustration_streak: int = 0
    pause_offered: bool = False
    # The last reply was a de-escalation: an answer to it is a fresh start, not more frustration.
    de_escalated: bool = False
    # The candidate took the pause offer and has not written since: no nudges until they do.
    paused: bool = False

    # The interview modes some recruiter at the location offers, looked up once the candidate
    # qualifies (the choice is asked only when there are two). The choice itself is
    # `profile.interview_mode`.
    available_modes: tuple[InterviewMode, ...] = ()
    # Every offered time the candidate turned down ("ninguno"), across rounds and returns:
    # never offered again, and a new round moves to other days (D9).
    declined_slots: tuple[Slot, ...] = ()
    # Every time offered in this conversation (by voice only the two read aloud), and the
    # question about times behind the offer on screen: a question about other times never
    # gets one already offered, and "¿y otras opciones?" keeps to the day or time asked about.
    # Both start again once the interview is booked, the mode or location changes, or the
    # candidate comes back in a new conversation.
    shown_slots: tuple[str, ...] = ()
    slot_query: SlotQuery | None = None

    quality_flags: tuple[QualityFlag, ...] = ()
    # Experience years the candidate confirmed after the read-back: not asked about again.
    confirmed_experience: float | None = None

    # The conversation is a phone call this turn (set by the service from its channel).
    voice: bool = False
    # Voice read-backs done (SAZ-47): the license question once its disqualifying answer was
    # read back. Older states may still list a phone or email spelled back before SAZ-48.
    read_back: tuple[FieldId, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _read_version_1(cls, data: Any) -> Any:
        """A state stored before it carried its phase (kept for one release after `0011`)."""
        from sazon.engine.upgrade import is_v1, upgrade  # it builds the phases defined here

        if isinstance(data, Mapping) and is_v1(cast(Mapping[str, Any], data)):
            return upgrade(cast(Mapping[str, Any], data))
        return data

    def at(self, phase: Phase) -> Self:
        return self.model_copy(update={"phase": phase})

    def at_interview(self, sub: ChoosingMode | Offering | InterviewBooked) -> Self:
        return self.at(Interview(sub=sub))

    @property
    def stage(self) -> StepId:
        """The funnel step: the field's while asking, else the phase's (analytics, the API)."""
        match self.phase:
            case Consent():
                return Stage.CONSENT
            case Asking(field=field):
                from sazon.engine.spec import agent

                return agent().field(field).funnel_step
            case Contact():
                from sazon.engine.spec import agent

                return agent().contact_step
            case Summary():
                return Stage.CONFIRM
            case Interview(sub=ChoosingMode()):
                return Stage.MODE
            case Interview(sub=Offering()):
                return Stage.SCHEDULING
            case _:
                return Stage.DONE

    @property
    def awaiting(self) -> FieldId | None:
        """The field asked: the screening's question, or a detail corrected after booking."""
        if isinstance(self.phase, Asking):
            return self.phase.field
        booked = self.booked
        sub = booked.sub if booked is not None else None
        if isinstance(sub, CancelConfirm):
            sub = sub.then
        return sub.field if isinstance(sub, Amending) else None

    @property
    def outcome(self) -> Outcome | None:
        match self.phase:
            case Interview():
                return Outcome.QUALIFIED
            case Closed(variant=Disqualified()):
                return Outcome.DISQUALIFIED
            case Closed(variant=Qualified()):
                return Outcome.QUALIFIED
            case Closed(variant=Withdrawn()):
                return Outcome.WITHDRAWN
            case Closed(variant=Abandoned()):
                return Outcome.ABANDONED
            case Closed(variant=DeclinedConsent()):
                return Outcome.DECLINED_CONSENT
            case _:
                return None

    @property
    def interview(self) -> ChoosingMode | Offering | InterviewBooked | None:
        return self.phase.sub if isinstance(self.phase, Interview) else None

    @property
    def offering(self) -> Offering | None:
        sub = self.interview
        return sub if isinstance(sub, Offering) else None

    @property
    def booked(self) -> InterviewBooked | None:
        sub = self.interview
        return sub if isinstance(sub, InterviewBooked) else None

    @property
    def closed(self) -> Disqualified | Ended | None:
        return self.phase.variant if isinstance(self.phase, Closed) else None

    @property
    def booking(self) -> Booked | None:
        """The interview booked, also while the candidate moves it."""
        sub = self.interview
        return sub.booking if isinstance(sub, Offering | InterviewBooked) else None

    @property
    def offered_slots(self) -> tuple[Slot, ...]:
        return self.offering.slots if self.offering is not None else ()

    @property
    def pending_confirmation(self) -> PendingConfirmation | None:
        return self.phase.confirmation if isinstance(self.phase, Asking) else None

    @property
    def pending_clarification(self) -> PendingClarification | None:
        return self.phase.clarification if isinstance(self.phase, Asking) else None

    @property
    def pending_identity(self) -> DuplicateMatch | None:
        return self.phase.match if isinstance(self.phase, Contact) else None

    @property
    def pending_question(self) -> PendingQuestion | None:
        """A question parked across the lookup the last turn asked for."""
        waiting = self.interview if isinstance(self.phase, Interview) else self.phase
        if isinstance(waiting, Contact | ChoosingMode | Offering):
            return waiting.parked_question
        return None

    @property
    def cancel_offered(self) -> bool:
        """ "¿La cancelo o prefieres otra hora?" waits for its answer."""
        if self.offering is not None:
            return self.offering.cancel_offered
        return self.booked is not None and isinstance(self.booked.sub, CancelConfirm)

    @property
    def disqualify_reason(self) -> ReasonId | None:
        return self.closed.reason if isinstance(self.closed, Disqualified) else None

    @property
    def reopenable(self) -> bool:
        return isinstance(self.closed, Disqualified) and self.closed.reopenable

    @property
    def talent_pool_opt_in(self) -> bool | None:
        return self.closed.talent_pool_opt_in if isinstance(self.closed, Disqualified) else None

    @property
    def drop_off_stage(self) -> StepId | None:
        return self.closed.drop_off if isinstance(self.closed, Abandoned) else None

    @property
    def opted_out(self) -> bool:
        """The candidate said they want to stop ("lo dejo"): this conversation stays closed,
        whatever they write next. A new chat with the same phone or email may still resume the
        application."""
        closed = self.closed
        return isinstance(closed, Qualified | Withdrawn | Abandoned) and closed.opted_out

    @property
    def restarted(self) -> bool:
        return isinstance(self.closed, Abandoned) and self.closed.restarted

    @property
    def duplicate_of(self) -> str | None:
        """The open application this chat handed over to (D10): it holds none of its own."""
        return self.closed.application_id if isinstance(self.closed, HandedOver) else None


def add_quality_flag(state: ConversationState, flag: QualityFlag) -> ConversationState:
    if flag in state.quality_flags:
        return state
    return state.model_copy(update={"quality_flags": (*state.quality_flags, flag)})


class PhaseKind(StrEnum):
    """Which top-level state the conversation is in (`ConversationState.phase`): the key of
    the handlers (`states.HANDLERS`) and of the interrupts (`interrupts.py`)."""

    CONSENT = "consent"
    ASKING = "asking"
    CONTACT = "contact"
    SUMMARY = "summary"
    INTERVIEW = "interview"
    CLOSED = "closed"


def phase_kind(state: ConversationState) -> PhaseKind:
    return PhaseKind(state.phase.kind)


BOOKED: Final = "interview.booked"
type Where = PhaseKind | Literal["interview.booked"]
"""Where a turn's interrupts are chosen from (`interrupts.py`): the phase, but a booked
interview has those of a closed application, and a message before the duplicate check ran is
read as answers, with those of asking."""


def where(state: ConversationState) -> Where:
    if state.booked is not None:
        return BOOKED
    if isinstance(state.phase, Contact) and state.phase.match is None:
        return PhaseKind.ASKING
    return phase_kind(state)


class Node(StrEnum):
    """A state of the diagram (`docs/engine/states.mmd`): a phase, or a sub-state of one,
    named by its path. A transition's ends are the leaves (`node`); an edge may start or end at
    a parent, for every sub-state in it (`Edge`)."""

    START = "start"
    """Before the conversation exists (`start`)."""
    CONSENT = "consent"
    ASKING = "asking"
    AWAITING = "asking.awaiting"
    CONFIRMING = "asking.confirming"
    CLARIFYING = "asking.clarifying"
    CONTACT = "contact"
    CHECKING = "contact.checking"
    IDENTITY = "contact.identity"
    SUMMARY = "summary"
    INTERVIEW = "interview"
    CHOOSING_MODE = "interview.choosing_mode"
    OFFERING = "interview.offering"
    BOOKED = "interview.booked"
    IDLE = "interview.booked.idle"
    AMENDING = "interview.booked.amending"
    MOVING = "interview.booked.moving"
    CANCEL_CONFIRM = "interview.booked.cancel_confirm"
    CLOSED = "closed"
    DISQUALIFIED = "closed.disqualified"
    QUALIFIED = "closed.qualified"
    WITHDRAWN = "closed.withdrawn"
    ABANDONED = "closed.abandoned"
    DECLINED_CONSENT = "closed.declined_consent"
    HANDED_OVER = "closed.handed_over"

    def within(self, parent: "Node") -> bool:
        """This node is `parent` or one of its sub-states."""
        return self == parent or self.startswith(f"{parent}.")


def node(state: ConversationState) -> Node:
    """The leaf of the diagram the state is at."""
    match state.phase:
        case Consent():
            return Node.CONSENT
        case Asking(confirmation=PendingConfirmation()):
            return Node.CONFIRMING
        case Asking(clarification=PendingClarification()):
            return Node.CLARIFYING
        case Asking():
            return Node.AWAITING
        case Contact(match=DuplicateMatch()):
            return Node.IDENTITY
        case Contact():
            return Node.CHECKING
        case Summary():
            return Node.SUMMARY
        case Interview(sub=InterviewBooked(sub=sub)):
            return Node(f"{Node.BOOKED}.{sub.kind}")
        case Interview(sub=sub):
            return Node(f"{Node.INTERVIEW}.{sub.kind}")
        case Closed(variant=variant):
            return Node(f"{Node.CLOSED}.{variant.kind}")
