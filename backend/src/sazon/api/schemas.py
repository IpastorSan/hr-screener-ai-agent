"""Request and response models. They define the OpenAPI schema the web client consumes."""

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sazon.auth import MAX_PASSWORD_LENGTH, password_problem
from sazon.engine.models import (
    Availability,
    CandidateProfile,
    Language,
    Outcome,
    PhoneInput,
    Platform,
    QualityFlag,
    Schedule,
    Sentiment,
    StepId,
)
from sazon.engine.service_areas import load_locations
from sazon.engine.spec import agent
from sazon.engine.text import MAX_MESSAGE_CHARS
from sazon.engine.ui import Ui
from sazon.engine.validators import Country
from sazon.llm.interface import CandidateSummary, Channel
from sazon.storage.models import WEEKDAY_KEYS, CandidateStatus, InterviewMode, ReviewStatus

MAX_MESSAGE_LENGTH = MAX_MESSAGE_CHARS
MAX_NOTE_LENGTH = 2000
MAX_HINT_LENGTH = 64


class ErrorField(BaseModel):
    loc: list[str | int]
    message: str


class ErrorOut(BaseModel):
    """Every error response. `fields` lists invalid request fields, never their values."""

    error: str
    detail: str
    fields: list[ErrorField] | None = None


# --- candidate chat ---


class ContextIn(BaseModel):
    """What the page knows: the job posting's country and location, and browser hints."""

    country: Country | None = None
    location_id: str | None = Field(default=None, max_length=MAX_HINT_LENGTH)
    timezone: str | None = Field(default=None, max_length=MAX_HINT_LENGTH)
    locale: str | None = Field(default=None, max_length=MAX_HINT_LENGTH)


class StartIn(BaseModel):
    channel: Channel = Channel.WEB
    context: ContextIn = ContextIn()
    restart_of: str | None = Field(
        default=None,
        max_length=64,
        description="The conversation the candidate left with the restart button: an "
        "application still open there is closed, so this chat starts a fresh one",
    )


class QuickRepliesAction(BaseModel):
    kind: Literal["quick_replies"]
    value: str = Field(max_length=MAX_HINT_LENGTH)


class MultiSelectAction(BaseModel):
    kind: Literal["multi_select"]
    value: list[Annotated[str, Field(max_length=MAX_HINT_LENGTH)]] = Field(max_length=10)


class SlotsAction(BaseModel):
    kind: Literal["slots"]
    value: str = Field(max_length=MAX_HINT_LENGTH)


class ConfirmAction(BaseModel):
    kind: Literal["confirm"]
    value: bool


class PhoneAction(BaseModel):
    kind: Literal["phone"]
    value: PhoneInput


type WidgetAction = Annotated[
    QuickRepliesAction | MultiSelectAction | SlotsAction | ConfirmAction | PhoneAction,
    Field(discriminator="kind"),
]


class MessageIn(BaseModel):
    """Either free `text` or a widget `action`, never both."""

    text: str | None = Field(default=None, min_length=1, max_length=MAX_MESSAGE_LENGTH)
    action: WidgetAction | None = None

    @model_validator(mode="after")
    def _one_of(self) -> "MessageIn":
        if (self.text is None) == (self.action is None):
            raise ValueError("send exactly one of 'text' or 'action'")
        return self


class ReplyOut(BaseModel):
    """What Ana says, plus the widget to render under it. SSE event `reply`."""

    text: str
    ui: Ui | None = None


class StateOut(BaseModel):
    stage: StepId
    outcome: Outcome | None = None
    language: Language
    country: Country
    ended: bool = False
    restartable: bool = Field(
        default=False,
        description="Closed for required data that could not be resolved (name, license, city "
        "or any contact): the candidate may start a new application right away",
    )
    reopenable: bool = Field(
        default=False,
        description="Just closed at a gate (no valid license): the chat has ended, but the "
        "next message may still reopen the application by contradicting the answer or "
        "correcting a field, so the composer stays open for that one message",
    )
    paused: bool = Field(
        default=False,
        description="The candidate took the offer to pause and has not written since: the chat "
        "stays open, but no nudges until they write again (the nudge endpoint returns 409)",
    )


class StartOut(BaseModel):
    conversation_id: str
    reply: ReplyOut
    state: StateOut


class MessageOut(BaseModel):
    """The turn result. SSE event `done`, or the JSON body with `Accept: application/json`.

    `conversation_id` differs from the one in the URL when a returning candidate was matched
    to their open application (`resumed` is true): continue the chat on the new id.
    `candidate_text` is the candidate's message as the transcript stores it (the label of a
    tapped widget, for example), so the client's bubble matches a reload; null if not stored.
    """

    conversation_id: str
    resumed: bool = False
    candidate_text: str | None = None
    reply: ReplyOut
    state: StateOut


class TranscriptTurn(BaseModel):
    """One message. `ui` is set only on the agent turn still waiting for an answer."""

    role: Literal["agent", "candidate"]
    text: str
    ui: Ui | None = None
    nudge: bool = Field(default=False, description="Ana's re-engagement nudge after a silence")
    created_at: datetime


class NudgeIn(BaseModel):
    attempt: int = Field(description="1 after the first silence, 2 after the next; 2 at most")


class NudgeOut(BaseModel):
    """The nudge Ana sent (or had already sent for this attempt)."""

    reply: ReplyOut
    state: StateOut


class TranscriptOut(BaseModel):
    conversation_id: str
    channel: Channel
    state: StateOut
    turns: list[TranscriptTurn]
    continued_in: str | None = Field(
        default=None,
        description="A returning candidate's application was resumed here and the chat or "
        "call carries on in its own conversation, this id: read that one from now on",
    )


# --- recruiter ---


type SortKey = Literal[
    "last_activity",
    "-last_activity",
    "created_at",
    "-created_at",
    "updated_at",
    "-updated_at",
    "name",
    "-name",
    "experience_years",
    "-experience_years",
    "start_date",
    "-start_date",
]


class Note(BaseModel):
    text: str
    created_at: datetime


MOOD = (
    "The candidate's mood in their latest conversation: the worst label read from their "
    "messages when it is frustrated or confused (frustrated outranks confused), otherwise the "
    "most recent label that is not neutral (positive), otherwise neutral. Widget taps carry no "
    "label. Null until the candidate has written a message the extractor read."
)


class CandidateItem(BaseModel):
    """A row in the recruiter queue. `status` is the screening result, `review_status` the
    recruiter's own progress with the candidate."""

    id: str
    name: str | None
    phone: str | None
    email: str | None
    status: CandidateStatus
    review_status: ReviewStatus
    outcome_reason: str | None
    location_id: str | None
    location_name: str | None
    city: str | None
    country: Country | None
    language: Language | None
    sentiment: Sentiment | None = Field(description=MOOD)
    channel: Channel
    availability: list[Availability] | None
    schedule: list[Schedule] | None
    experience_years: float | None
    platforms: list[Platform] | None
    start_date: date | None
    late_start_days: int | None
    quality_flags: list[QualityFlag]
    talent_pool: bool = Field(
        default=False,
        description="In the talent pool (bolsa de candidatos): agreed to keep their data for "
        "future vacancies",
    )
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    last_activity_at: datetime = Field(
        description="The candidate's latest message or reply in any of their conversations"
    )

    interview_at: datetime | None = Field(
        default=None, description="Start of the booked interview, if any (UTC)."
    )
    interview_local_start: str | None = Field(
        default=None,
        description="The same start, ISO 8601 in the location's time zone, as on the booking.",
    )
    recruiter_id: str | None = Field(
        default=None, description="The recruiter running the booked video interview, if any."
    )
    recruiter_name: str | None = None
    interviewer_name: str | None = Field(
        default=None,
        description="In person: the location's manager who runs the interview (read-only for "
        "recruiters; `recruiter_*` are empty then)",
    )
    interviewer_email: str | None = None
    interview_mode: InterviewMode | None = Field(
        default=None, description="How the booked interview happens: in person or video call."
    )


class StatusCounts(BaseModel):
    """Candidates per screening status for the page's filters, ignoring `status` itself."""

    all: int
    in_progress: int
    qualified: int
    disqualified: int
    abandoned: int
    withdrawn: int


class CandidatePage(BaseModel):
    items: list[CandidateItem]
    total: int
    page: int
    page_size: int
    counts: StatusCounts


class LocationOut(BaseModel):
    """A Sazón kitchen candidates can be hired for."""

    location_id: str
    name: str
    city: str
    country: Country


class SourceOut(BaseModel):
    """What retrieval found for a question: `faq:<id>` or `doc:<id>#<section>`, and `label` to
    show (`FAQ: <question>` or `<document> > <section>` with a chevron)."""

    source: str
    label: str
    score: float | None = None


class TurnOut(BaseModel):
    """A stored turn. `extraction` is what the model read from a candidate turn, `action` what
    the engine decided for an agent or system turn. Both are the engine's models as JSON."""

    id: int
    role: Literal["agent", "candidate", "system"]
    text: str | None
    ui: Ui | None
    extraction: dict[str, Any] | None
    action: dict[str, Any] | None
    model: str | None
    latency_ms: int | None
    cost_usd: float | None
    fallback_used: bool
    nudge: bool = False
    sources: list[SourceOut] = []
    """Agent turns that answered a question: the snippets the reply was written from, best
    first. Recruiters only; the candidate never sees them."""
    created_at: datetime


class ConversationOut(BaseModel):
    id: str
    channel: Channel
    stage: StepId
    outcome: Outcome | None
    disqualify_reason: str | None
    drop_off_stage: StepId | None
    quality_flags: list[QualityFlag]
    context: ContextIn
    started_at: datetime
    ended_at: datetime | None
    last_activity_at: datetime
    recovered_by_nudge: bool = False
    elevenlabs_conversation_id: str | None = None
    """A voice call's conversation in ElevenLabs, for its recording and transcript there."""
    turns: list[TurnOut]
    faq_misses: list[str] = []
    """Questions nothing in the FAQ or the documents answered, redacted, oldest first."""


class BookingOut(BaseModel):
    """The candidate's booked interview. `local_start` is ISO 8601 in the location's time zone."""

    slot_id: str
    starts_at: datetime
    ends_at: datetime
    local_start: str
    location_id: str
    location_name: str | None
    address: str | None
    booked_at: datetime
    mode: InterviewMode | None = None
    meet_url: str | None = None
    recruiter_id: str | None = None
    recruiter_name: str | None = None
    interviewer_name: str | None = Field(
        default=None,
        description="In person: the location's manager who runs the interview (read-only for "
        "recruiters; `recruiter_*` are empty then)",
    )
    interviewer_email: str | None = None


class CandidateDetail(CandidateItem):
    booking: BookingOut | None = None
    profile: CandidateProfile
    summary: CandidateSummary | None
    notes: list[Note]
    conversations: list[ConversationOut]


class CandidatePatch(BaseModel):
    status: ReviewStatus | None = None
    note: str | None = Field(default=None, min_length=1, max_length=MAX_NOTE_LENGTH)

    @model_validator(mode="after")
    def _something(self) -> "CandidatePatch":
        if self.status is None and self.note is None:
            raise ValueError("send 'status', 'note' or both")
        return self


class ProfileExport(CandidateProfile):
    """Every field of the screening, with the matched location spelled out."""

    model_config = ConfigDict(extra="forbid")

    city: str | None = None
    location_name: str | None = None


class ScreeningExport(BaseModel):
    candidate_id: str
    status: CandidateStatus
    review_status: ReviewStatus
    outcome_reason: str | None
    country: Country | None
    language: Language | None
    sentiment: Sentiment | None = Field(description=MOOD)
    quality_flags: list[QualityFlag]
    profile: ProfileExport
    summary: CandidateSummary | None
    notes: list[Note]
    conversations: list[ConversationOut]
    exported_at: datetime


# --- recruiters and sign-in ---

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HOUR = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
WorkingHours = dict[str, list[tuple[str, str]]]


class LoginIn(BaseModel):
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class DevLoginIn(BaseModel):
    email: str = Field(min_length=1, max_length=254)


class CalendarOut(BaseModel):
    """A recruiter's Google Calendar connection. `revoked`: Google no longer accepts the
    grant and the recruiter must connect again."""

    status: Literal["connected", "revoked", "disconnected"] = "disconnected"
    google_email: str | None = None
    connected_at: datetime | None = None


class AuthProvidersOut(BaseModel):
    """What the sign-in page and the calendar button can offer."""

    google: bool = Field(description="Google sign-in and Calendar are configured")
    domain: str = Field(description="The Workspace domain allowed to sign in with Google")
    scheduling_source: Literal["fixed", "google", "fake"]


class MeOut(BaseModel):
    """The signed-in recruiter."""

    id: str
    name: str
    email: str
    is_admin: bool
    country: Country
    timezone: str
    calendar: CalendarOut = Field(default_factory=CalendarOut)


class RecruiterOut(MeOut):
    active: bool
    location_ids: list[str]
    interview_modes: list[InterviewMode]
    working_hours: WorkingHours = Field(
        description="Weekday (`mon`..`sun`) to `[start, end]` blocks, HH:MM local time"
    )
    max_interviews_per_day: int
    scope: dict[str, list[str]] = Field(
        description="The candidates they interview beyond the locations: field id to options "
        "(`AgentSpecOut.interview_scope`); a field not named, any answer"
    )
    has_password: bool
    created_at: datetime
    updated_at: datetime


def _check_email(value: str) -> str:
    value = value.strip().lower()
    if not EMAIL.match(value):
        raise ValueError("not an email address")
    return value


def _check_password(value: str | None) -> str | None:
    if value is not None and (problem := password_problem(value)):
        raise ValueError(problem)
    return value


def _check_timezone(value: str | None) -> str | None:
    if value is None:
        return value
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown time zone") from exc
    return value


def _check_locations(value: list[str] | None) -> list[str] | None:
    if value is None:
        return value
    unknown = sorted(set(value) - set(load_locations()))
    if unknown:
        raise ValueError(f"unknown location ids: {', '.join(unknown[:5])}")
    return list(dict.fromkeys(value))


def _check_scope(value: dict[str, list[str]] | None) -> None:
    """Fields the agent scopes interviewers on, each with options it has."""
    scoped = {field.id: field for field in agent().scoped}
    for field, options in (value or {}).items():
        if field not in scoped:
            raise ValueError(f"no interview scope on {field}: one of {sorted(scoped)}")
        unknown = sorted(set(options) - set(scoped[field].choices))
        if unknown:
            raise ValueError(f"{field} has no options {', '.join(unknown[:5])}")


def _check_hours(value: WorkingHours | None) -> WorkingHours | None:
    if value is None:
        return value
    for day, blocks in value.items():
        if day not in WEEKDAY_KEYS:
            raise ValueError(f"unknown weekday {day!r}; use {', '.join(WEEKDAY_KEYS)}")
        for start, end in blocks:
            if not (HOUR.match(start) and HOUR.match(end)) or start >= end:
                raise ValueError(f"{day}: each block is [HH:MM, HH:MM] with start before end")
    return value


class RecruiterFields(BaseModel):
    """What an admin can set on a recruiter. Validates every field it is given."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    password: str | None = Field(default=None, description="A new password; at least 12 characters")
    is_admin: bool | None = None
    timezone: str | None = None
    location_ids: list[str] | None = Field(default=None, max_length=100)
    interview_modes: list[InterviewMode] | None = Field(default=None, min_length=1)
    working_hours: WorkingHours | None = None
    max_interviews_per_day: int | None = Field(default=None, ge=1, le=40)
    scope: dict[str, list[str]] | None = None

    @model_validator(mode="after")
    def _fields(self) -> "RecruiterFields":
        if self.interview_modes is not None and InterviewMode.ONSITE in self.interview_modes:
            raise ValueError("recruiters interview by video; in person is the location manager's")
        _check_password(self.password)
        _check_timezone(self.timezone)
        self.location_ids = _check_locations(self.location_ids)
        _check_hours(self.working_hours)
        _check_scope(self.scope)
        return self


class RecruiterCreate(RecruiterFields):
    name: str = Field(min_length=1, max_length=100)  # pyright: ignore[reportIncompatibleVariableOverride]
    email: str = Field(max_length=254)
    country: Country
    location_ids: list[str] = Field(default_factory=list, max_length=100)  # pyright: ignore[reportIncompatibleVariableOverride]
    interview_modes: list[InterviewMode] = Field(  # pyright: ignore[reportIncompatibleVariableOverride]
        default_factory=lambda: [InterviewMode.VIDEO], min_length=1
    )

    @model_validator(mode="after")
    def _email(self) -> "RecruiterCreate":
        self.email = _check_email(self.email)
        return self


class RecruiterPatch(RecruiterFields):
    active: bool | None = None


class InterviewItem(BaseModel):
    """An interview on a recruiter's agenda. `local_start` is ISO 8601 in the location's zone.
    Video: theirs to run (`recruiter_*`). In person: at one of their locations, run by its
    manager (`interviewer_*`), for them to see only."""

    booking_id: str
    candidate_id: str
    candidate_name: str | None
    phone: str | None
    email: str | None
    status: CandidateStatus
    starts_at: datetime
    ends_at: datetime
    local_start: str
    location_id: str
    location_name: str | None
    address: str | None
    mode: InterviewMode | None
    meet_url: str | None
    recruiter_id: str | None
    recruiter_name: str | None
    interviewer_name: str | None = Field(
        default=None,
        description="In person: the location's manager who runs the interview (read-only for "
        "recruiters; `recruiter_*` are empty then)",
    )
    interviewer_email: str | None = None
    calendar_issue: str | None = Field(
        default=None,
        description="Set when the recruiter moved the event in Google Calendar to a time our "
        "rules would not offer: `past`, `all_day`, `outside_hours`, `clash`, `busy`, `daily_max`",
    )
