"""Tables. Datetimes are aware UTC; JSON columns hold the engine's pydantic models dumped."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Column, Index, UniqueConstraint, text
from sqlmodel import Field, SQLModel


def new_id() -> str:
    return uuid4().hex


class CandidateStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"
    ABANDONED = "abandoned"
    WITHDRAWN = "withdrawn"
    """Retirada: cancelled the booked interview (`Outcome.WITHDRAWN`). Closed, as a disqualified
    application is: a new chat with the same phone or email starts a fresh application."""


RESTARTED = "restarted"
"""`Candidate.outcome_reason` of an application closed by the restart button (QA batch 3)."""


class ReviewStatus(StrEnum):
    """What the recruiter has done with a candidate, separate from the screening outcome."""

    NEW = "new"
    CONTACTED = "contacted"
    HIRED = "hired"
    REJECTED = "rejected"


class TurnRole(StrEnum):
    AGENT = "agent"
    CANDIDATE = "candidate"
    SYSTEM = "system"


class Candidate(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    phone: str | None = Field(default=None, index=True, description="E.164")
    email: str | None = Field(default=None, index=True)
    profile: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    status: str = Field(default=CandidateStatus.IN_PROGRESS, index=True)
    outcome_reason: str | None = None
    quality_flags: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    sentiment: str | None = Field(
        default=None,
        description="The mood of the latest conversation (`SentimentStats.mood`): its worst "
        "label when frustrated or confused, else the latest non-neutral one, else neutral",
    )
    language: str | None = None
    country: str | None = None
    channel: str
    location_id: str | None = Field(default=None, index=True)
    posting_location_id: str | None = Field(
        default=None,
        index=True,
        description="The location of the job posting the application started from, when the "
        "posting named one: places a candidate with no location of their own (closed before the "
        "city, or out of area) in a recruiter's scope (`recruiters.in_scope`)",
    )
    late_start_days: int | None = None
    talent_pool: bool = Field(
        default=False,
        description="Agreed to keep their data in the talent pool (bolsa de candidatos) for "
        "future vacancies: asked when there is no location in their area",
    )
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class Conversation(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    candidate_id: str | None = Field(default=None, foreign_key="candidate.id", index=True)
    channel: str
    state: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    context: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    outcome: str | None = None
    quality_flags: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    drop_off_stage: str | None = None
    sentiment: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSON),
        description="`SentimentStats`: label counts, peak and latest non-neutral label of the "
        "messages the extractor read; widget taps carry none",
    )
    recovered_by_nudge: bool = Field(
        default=False, description="The candidate wrote again after a re-engagement nudge"
    )
    started_at: datetime = Field(index=True)
    ended_at: datetime | None = None
    last_activity_at: datetime
    elevenlabs_conversation_id: str | None = Field(
        default=None, description="The ElevenLabs Agents conversation of a voice call"
    )


class Turn(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: str = Field(foreign_key="conversation.id", index=True)
    role: str
    text: str | None = None
    ui: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    extraction: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    action: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    latency_ms: int | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    fallback_used: bool = False
    nudge: bool = False
    sources: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))
    """Agent turns answering a question: what retrieval found, as `{source, label, score}`.
    For recruiters only; never sent to the candidate."""
    created_at: datetime = Field(index=True)


class FaqMiss(SQLModel, table=True):
    """A candidate question the FAQ did not cover, kept to grow it. The text is redacted."""

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: str = Field(foreign_key="conversation.id", index=True)
    question: str
    language: str
    country: str
    created_at: datetime


class Summary(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    conversation_id: str = Field(foreign_key="conversation.id", index=True, unique=True)
    body: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    model: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    fallback_used: bool = False
    created_at: datetime


class CandidateReview(SQLModel, table=True):
    """The recruiter's status and notes for a candidate. No row means status `new`."""

    candidate_id: str = Field(foreign_key="candidate.id", primary_key=True)
    status: str = Field(default=ReviewStatus.NEW, index=True)
    notes: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    updated_at: datetime


class BookingStatus(StrEnum):
    """`rescheduled` marks a booking replaced by a newer one for the same candidate."""

    BOOKED = "booked"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"


class InterviewMode(StrEnum):
    ONSITE = "onsite"
    VIDEO = "video"


WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class Recruiter(SQLModel, table=True):
    """A member of the hiring team who signs in to the dashboard and runs interviews.

    `working_hours` maps a weekday key (`mon`..`sun`) to `[["09:00", "14:00"], ...]` in the
    recruiter's local time. Recruiters run the video interviews; `interview_modes` is
    `["video"]` (in-person interviews are the location managers', SAZ-64). `password_hash`
    is argon2id; empty for an account that can only sign in with SSO. `session_version` goes
    up on a password change or deactivation, which ends every session issued before."""

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    email: str = Field(unique=True, index=True, description="Lowercase")
    password_hash: str | None = None
    is_admin: bool = False
    active: bool = True
    country: str = Field(description="ES or MX")
    timezone: str
    location_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    interview_modes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    working_hours: dict[str, list[list[str]]] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=False)
    )
    scope: dict[str, list[str]] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
        description="Beyond the locations, the candidates they interview by the answers to the "
        "agent's `interview.scope` fields, field id to options (a teacher's subjects); a field "
        "not named, any answer",
    )
    max_interviews_per_day: int = 15
    session_version: int = 0
    created_at: datetime
    updated_at: datetime


class CredentialStatus(StrEnum):
    CONNECTED = "connected"
    REVOKED = "revoked"
    """Google refused the refresh token (revoked by the user, expired, password change): the
    recruiter must connect again."""


class GoogleCredential(SQLModel, table=True):
    """A recruiter's Google Calendar grant. The refresh token is Fernet-encrypted with
    `TOKEN_ENCRYPTION_KEY`; access tokens live only in memory. Disconnecting revokes the grant
    at Google and deletes the row."""

    recruiter_id: str = Field(foreign_key="recruiter.id", primary_key=True)
    google_email: str
    google_subject: str = Field(description="The ID token's `sub`")
    calendar_id: str = "primary"
    scopes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    refresh_token_encrypted: str
    status: str = Field(default=CredentialStatus.CONNECTED)
    connected_at: datetime
    updated_at: datetime


class InterviewSlot(SQLModel, table=True):
    """An interview time at a location: the key a booking points at. Offered times come from
    the recruiters' availability (`scheduling.availability`); a row is created the first time
    a time is offered. `capacity` and `booked_count` are unused since SAZ-38 (a recruiter takes
    one interview at a time) and stay for older rows."""

    __table_args__ = (UniqueConstraint("location_id", "starts_at"),)

    id: str = Field(default_factory=new_id, primary_key=True)
    location_id: str = Field(index=True)
    starts_at: datetime = Field(index=True)
    ends_at: datetime
    capacity: int = 2
    booked_count: int = 0


class Booking(SQLModel, table=True):
    """At most one `booked` row per candidate, enforced by a partial unique index.

    A video interview's `recruiter_id` is the recruiter who runs it, the event on their
    calendar. An in-person one is run by the location's manager (`interviewer_email`, copied
    at booking time); its `recruiter_id` is the account whose calendar holds the event, the
    `ONSITE_ORGANIZER_EMAIL` one (None without it, when there is no calendar)."""

    __table_args__ = (
        Index(
            "ix_booking_one_active",
            "candidate_id",
            unique=True,
            sqlite_where=text("status = 'booked'"),
            postgresql_where=text("status = 'booked'"),
        ),
    )

    id: str = Field(default_factory=new_id, primary_key=True)
    candidate_id: str = Field(foreign_key="candidate.id", index=True)
    conversation_id: str = Field(foreign_key="conversation.id", index=True)
    slot_id: str = Field(foreign_key="interviewslot.id", index=True)
    status: str = Field(default=BookingStatus.BOOKED, index=True)
    recruiter_id: str | None = Field(default=None, foreign_key="recruiter.id", index=True)
    mode: str | None = Field(default=None, description="`InterviewMode`")
    interviewer_email: str | None = Field(
        default=None,
        description="In person: the location manager who runs it, a guest of the event",
    )
    meet_url: str | None = None
    google_event_id: str | None = None
    google_calendar_id: str | None = None
    cancelled_by: str | None = Field(
        default=None,
        description="`candidate`, `recruiter` or `manager` (in person, SAZ-65) when a calendar "
        "change cancelled it (SAZ-40)",
    )
    calendar_issue: str | None = Field(
        default=None,
        description="`MoveIssue`: the recruiter moved the event in their calendar to a time our "
        "rules would not offer (shown to them); cleared by a later valid move",
    )
    created_at: datetime
    updated_at: datetime


class CalendarChannel(SQLModel, table=True):
    """A Google Calendar push channel (`events.watch`) on a recruiter's calendar (SAZ-40).

    Google calls the webhook with the channel id, our secret token and its resource id; only
    the token's SHA-256 is stored. `sync_token` is where the next incremental `events.list`
    starts; it belongs to the calendar, so a renewal copies it to the new channel. A recruiter
    has one channel, two for a moment while one is renewed."""

    id: str = Field(primary_key=True, description="The channel id we chose")
    recruiter_id: str = Field(foreign_key="recruiter.id", index=True)
    calendar_id: str
    resource_id: str
    token_hash: str
    expires_at: datetime
    sync_token: str | None = None
    created_at: datetime
