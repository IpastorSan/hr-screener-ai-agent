"""What the engine wants said next. The responder phrases it; it never decides it."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sazon.engine.models import (
    CandidateProfile,
    FieldId,
    InterviewMode,
    Language,
    Platform,
    QuestionTopic,
    Schedule,
    schedule_list,
)
from sazon.engine.slots import Booked, Miss, OfferReason, Slot, SlotQuery
from sazon.engine.state import ReasonId
from sazon.engine.ui import Ui
from sazon.engine.validators import Country, ReasonCode


class DeflectKind(StrEnum):
    OFF_TOPIC = "off_topic"
    INJECTION = "injection"


class EndReason(StrEnum):
    DECLINED_CONSENT = "declined_consent"
    OPT_OUT = "opt_out"
    ABUSE = "abuse"
    PAUSED = "paused"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    """The candidate cancelled their interview: it is dropped, the application is withdrawn."""
    RESTARTED = "restarted"
    """Not said to anyone: recorded when the restart button closes an open application."""


class BaseAction(BaseModel):
    """Every action says which language and country register to reply in.

    `simplify` asks for a shorter message with buttons, for a frustrated candidate.
    """

    model_config = ConfigDict(frozen=True)

    language: Language
    country: Country
    simplify: bool = False
    ui: Ui | None = None


class Greet(BaseAction):
    """Introduces Ana as an AI assistant and asks for explicit consent to the privacy notice.

    Only an explicit yes (CONFIRM) starts the screening and only an explicit no (OPT_OUT) ends it.
    Anything else, including answers to later questions, repeats the request with `retry=1`.
    """

    kind: Literal["greet"] = "greet"
    ai_disclosure: Literal[True] = True
    privacy_notice: Literal[True] = True
    retry: int = 0


class AskField(BaseAction):
    """`again`: the candidate said the value they gave is wrong ("mi email está mal"): ask for
    it once more, with a short lead."""

    kind: Literal["ask_field"] = "ask_field"
    field: FieldId
    retry: int = 0
    hint: ReasonCode | None = None
    options: tuple[str, ...] = ()
    again: bool = False


class Clarify(BaseAction):
    """The answer matched several values. Without options, ask for more detail.

    `city`: the options are more than two locations, all in this city ("DF" matched every
    Mexico City location): ask which zone of it, without naming them."""

    kind: Literal["clarify"] = "clarify"
    field: FieldId
    options: tuple[str, ...]
    city: str | None = None


class ConfirmValue(BaseAction):
    """Read back one value before acting on it and ask if it is right.

    `start_date`: an ISO date beyond 90 days. `city`: a place outside the service areas, as the
    candidate named it; a yes closes the application as out of area. With `location_id`, the
    one location the place matched loosely instead: a yes picks it. `experience_years`: years
    hard to believe, 15 or more, or more than `platform` has existed in the country; asked
    neutrally, and a yes keeps them.

    `email`: an address whose domain looks mistyped ("gmail.con"), `value` the corrected one:
    "¿Quisiste decir lucia@gmail.com?" (D2). A yes keeps the corrected one.

    By voice only (SAZ-47): a disqualifying `credential` answer (`value` the reason it would
    close with) is checked once before the application closes. Phone and email are
    never spelled back (SAZ-48): the call page shows what was heard.
    """

    kind: Literal["confirm_value"] = "confirm_value"
    field: FieldId
    value: str
    platform: Platform | None = None
    location_id: str | None = None
    today: date | None = None
    explain: bool = False
    """`city` with `location_id`: `value` (the place the candidate named again, or asked about)
    is in that location's area, which they already have: "Roma está en la alcaldía Cuauhtémoc,
    así que le corresponde ese local. ¿Es correcto?". Sent as written."""
    """`start_date` only: a day and month already past this year, read as next year's. The
    read-back says today's date: "Hoy es 21 de octubre, ¿te refieres al 1 de octubre de 2027?"
    (D6). Sent as written."""


class CheckDuplicate(BaseAction):
    """Not a message: the service looks up open applications and hands them to `on_lookup`
    (`DuplicateChecked`)."""

    kind: Literal["check_duplicate"] = "check_duplicate"
    phone: str | None
    email: str | None


class ResumeExisting(BaseAction):
    kind: Literal["resume_existing"] = "resume_existing"
    application_id: str


class ConfirmIdentity(BaseAction):
    """The phone or email just given belongs to an open application (D10): "Ya tenemos este
    teléfono registrado. ¿Quieres continuar como Lucía?", with yes and no buttons. Nothing else
    of the application (its stage, a booked day, time or place) is said before the yes. Sent
    as written; by voice a shorter line."""

    kind: Literal["confirm_identity"] = "confirm_identity"
    name: str | None = None
    matched_by: Literal["phone", "email"] = "phone"


class ApplicationStatus(BaseAction):
    """A returning candidate whose earlier application is already closed (qualified, with the
    interview booked or the application saved): its status in one sentence, then goodbye.
    `booking` None means saved."""

    kind: Literal["application_status"] = "application_status"
    booking: Booked | None = None


class ConfirmSummary(BaseAction):
    kind: Literal["confirm_summary"] = "confirm_summary"
    profile: CandidateProfile
    correcting: bool = False
    """The candidate said something is wrong without saying what: ask which detail."""
    voice: bool = False
    """A phone call: there is no card, so the key values are read aloud, built in code and sent
    as written (name, place and location, availability, start date)."""


class Disqualify(BaseAction):
    """`blocked`: a policy close of a new application from a phone or email already closed
    three times for manipulation (D11): a neutral line, no screening."""

    kind: Literal["disqualify"] = "disqualify"
    reason: ReasonId
    invite_reapply: bool
    talent_pool_offer: bool
    blocked: bool = False


class FindModes(BaseAction):
    """Not a message: the candidate qualified, and the service looks up which interview modes
    the recruiters at `location_id` offer and hands them to `on_lookup` (`ModesFound`)."""

    kind: Literal["find_modes"] = "find_modes"
    location_id: str


class AskMode(BaseAction):
    """Ask whether the interview should be in person or by video call. Only asked when both
    `options` are offered at the location. `qualified`: the first ask, right after the
    screening ended. `retry` > 0: the last reply chose neither."""

    kind: Literal["ask_mode"] = "ask_mode"
    options: tuple[InterviewMode, ...]
    qualified: bool = False
    retry: int = 0


class FindSlots(BaseAction):
    """Not a message: the service looks up free slots and hands them to `on_lookup` (`SlotsFound`).

    The candidate qualified (`reason` qualified), turned down the offered slots, wants to move
    their booking, picked a slot that filled up, switched interview mode, or asked about other
    times (`query`: only times that fit it, or the nearest when none does). `exclude` holds
    slots not to offer again, `avoid_days` days the candidate turned down (D9: a new round
    moves to other days). `mode`: only recruiters who interview that way count.
    """

    kind: Literal["find_slots"] = "find_slots"
    location_id: str
    schedule: tuple[Schedule, ...] | None
    start_date: date | None
    reason: OfferReason
    exclude: tuple[str, ...] = ()
    mode: InterviewMode | None = None
    query: SlotQuery | None = None
    avoid_days: tuple[date, ...] = ()

    @field_validator("schedule", mode="before")
    @classmethod
    def _schedule_list(cls, value: object) -> object:
        """An action stored before SAZ-41 holds one schedule as a string; read it as a list."""
        return schedule_list(value)


class OfferSlots(BaseAction):
    """Offer `slots` (from the scheduling service, never from a model) and ask which suits.

    `reason` is None when the same offer is shown again. `retry` > 0: the last reply picked none
    of them. No slots means none are free right now. `mode` video: the times are for a video
    call. `matched` false: nothing free fits the times the candidate asked about, and these
    are the nearest; `miss` says why (`Miss`). `pick`: their words fit several of these again
    after "which one?": the buttons and a one-line ask, never the same question again.
    """

    kind: Literal["offer_slots"] = "offer_slots"
    location_id: str | None
    slots: tuple[Slot, ...] = ()
    reason: OfferReason | None = None
    retry: int = 0
    mode: InterviewMode | None = None
    matched: bool = True
    miss: Miss | None = None
    pick: bool = False


class BookSlot(BaseAction):
    """Not a message: the service books `slot_id` (moving any current booking there) and hands
    the booking to `on_lookup` (`SlotBooked`)."""

    kind: Literal["book_slot"] = "book_slot"
    slot_id: str


class ConfirmBooking(BaseAction):
    """The interview is booked: say when and where, and that replying here changes it.

    Onsite: the location and its address. Video (`booking.mode`): the Google Meet invitation
    goes to the candidate's email (`has_email`), and the link (`booking.meet_url`, when there is
    one) is on the card below the message, never in the text.

    `kept`: the candidate wanted to move it but no other time suited, so it stays as it was.
    """

    kind: Literal["confirm_booking"] = "confirm_booking"
    booking: Booked
    has_email: bool = True
    """The candidate gave an email, where a video interview's invitation goes."""
    rescheduled: bool = False
    kept: bool = False


class DetailsUpdated(BaseAction):
    """With the interview booked, a detail the candidate corrected ("mi correo está mal", then
    the new one) is updated and the interview stays as it is: one fixed line that says so and
    when it is. `changed` False: nothing changed (the same location, a move turned down, or no
    usable value after a retry). The service rewrites the calendar invitation when the name,
    the phone or the email changed. Sent as written."""

    kind: Literal["details_updated"] = "details_updated"
    field: FieldId
    booking: Booked
    changed: bool = True


class ConfirmMove(BaseAction):
    """With the interview booked, a place of another location: moving there cancels the
    interview and offers times at the new one, so Ana asks first, yes or no. Sent as written."""

    kind: Literal["confirm_move"] = "confirm_move"
    booking: Booked
    from_location_id: str
    location_id: str


class ApplicationSaved(BaseAction):
    """No interview time suits the candidate, or (`none_free`) none is free at their location
    even weeks ahead: the screening ends, qualified, with the application saved. No question
    and no promise of new dates (SAZ-42)."""

    kind: Literal["application_saved"] = "application_saved"
    none_free: bool = False


class OfferPause(BaseAction):
    kind: Literal["offer_pause"] = "offer_pause"


class AskCancel(BaseAction):
    """The candidate asked to cancel their booked interview: "¿La cancelo o prefieres otra
    hora?", with Cancelar and Otra hora buttons, asked once. Sent as written."""

    kind: Literal["ask_cancel"] = "ask_cancel"
    booking: Booked


class End(BaseAction):
    """The conversation ends. `booking_cancelled`: the candidate's interview was dropped (the
    service cancels it), and the reply says so: an abuse close, an opt-out, or (`cancelled`
    reason) the candidate's own request. `cancelled`: that interview, its day named in the
    reply."""

    kind: Literal["end"] = "end"
    reason: EndReason
    booking_cancelled: bool = False
    cancelled: Booked | None = None


type Prompt = Annotated[
    Greet
    | AskField
    | Clarify
    | ConfirmValue
    | ConfirmSummary
    | AskMode
    | OfferSlots
    | ConfirmIdentity,
    Field(discriminator="kind"),
]
"""Actions that ask the candidate something, so they can follow an interruption."""


class WarnPolicy(BaseAction):
    """A new application from a phone or email whose earlier one was closed for a manipulation
    attempt (D11): a fixed warning that another attempt closes this one too, then `follow_up`.
    Sent as written."""

    kind: Literal["warn_policy"] = "warn_policy"
    follow_up: Prompt


class WelcomeBack(BaseAction):
    """A returning candidate carried on in their application (SAZ-47): the fixed welcome back
    ("Hola de nuevo, Lucía, seguimos donde lo dejaste."), then `follow_up`, its pending question
    or new interview times. The welcome is the lead: the whole message is checked as one reply
    (QA batch 3), never the question alone with the welcome put in front afterwards."""

    kind: Literal["welcome_back"] = "welcome_back"
    name: str | None = None
    follow_up: Annotated[Prompt | ApplicationSaved, Field(discriminator="kind")]


class AnswerQuestion(BaseAction):
    """Answer from the FAQ (filled in by the service), then repeat `follow_up`.

    With `topic` set the answer is fixed: `ai_identity` always confirms Ana is an AI.
    """

    kind: Literal["answer_question"] = "answer_question"
    question: str | None
    follow_up: Prompt | None
    topic: QuestionTopic | None = None
    parts: tuple[str, ...] = ()
    """A question that asks several things, one standalone question per part (at most 3):
    each is looked up on its own. Empty for one question."""
    interview_mode: InterviewMode | None = None
    """The mode of the interview the candidate has booked, if any: an answer about the
    interview speaks of that one (a video call is never at the location)."""


class SkipEmail(BaseAction):
    """The screening carries on without the email, with a phone in: the candidate would rather
    not give it (SAZ-72), or it failed its tries. One line says the phone is enough and that,
    without an email, the interview invitation can't be sent (they'll see it in the chat), then
    `follow_up`. Sent as written."""

    kind: Literal["skip_email"] = "skip_email"
    follow_up: Prompt | None


class SkipPhone(BaseAction):
    """The screening carries on without the phone: the candidate would rather not give it with
    an email in (`has_email`), or it failed its tries. One line says so (with no email yet, that
    the email is needed instead), then `follow_up`. Sent as written."""

    kind: Literal["skip_phone"] = "skip_phone"
    has_email: bool
    follow_up: Prompt | None


class Deflect(BaseAction):
    kind: Literal["deflect"] = "deflect"
    reason: DeflectKind
    follow_up: Prompt | None


class DeEscalate(BaseAction):
    """A calm line that acknowledges the frustration, then repeat `follow_up`. `closed`: the
    screening is over, so there is nothing left to finish and no question."""

    kind: Literal["de_escalate"] = "de_escalate"
    follow_up: Prompt | None
    closed: bool = False


class ReEngage(BaseAction):
    kind: Literal["re_engage"] = "re_engage"
    attempt: int
    follow_up: Prompt | None


type Action = Annotated[
    Greet
    | AskField
    | Clarify
    | ConfirmValue
    | CheckDuplicate
    | ResumeExisting
    | ConfirmIdentity
    | WelcomeBack
    | WarnPolicy
    | AnswerQuestion
    | SkipEmail
    | SkipPhone
    | Deflect
    | DeEscalate
    | OfferPause
    | ConfirmSummary
    | Disqualify
    | FindModes
    | AskMode
    | FindSlots
    | OfferSlots
    | BookSlot
    | ConfirmBooking
    | DetailsUpdated
    | ConfirmMove
    | ApplicationSaved
    | ApplicationStatus
    | AskCancel
    | End
    | ReEngage,
    Field(discriminator="kind"),
]
