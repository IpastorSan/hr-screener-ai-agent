from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

type FieldId = str
"""A screening field, by the id the agent spec gives it (`spec.FieldSpec.id`): the name the
extractor reports it under and the key of the per-field counters in the state."""


class Availability(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    WEEKENDS = "weekends"


class Schedule(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    FLEXIBLE = "flexible"


def schedule_list(value: object) -> object:
    """A stored schedule from before it became a list (SAZ-41): "morning" -> ["morning"]."""
    if isinstance(value, str):
        return [value]
    return value


class Platform(StrEnum):
    GLOVO = "glovo"
    UBER_EATS = "uber_eats"
    JUST_EAT = "just_eat"
    RAPPI = "rappi"
    DIDI_FOOD = "didi_food"
    DELIVEROO = "deliveroo"
    AMAZON_FLEX = "amazon_flex"
    OTHER = "other"


class InterviewMode(StrEnum):
    """How the interview happens: at the location, or a Google Meet video call. The same values
    as `sazon.storage.models.InterviewMode`, which the engine can't import."""

    ONSITE = "onsite"
    VIDEO = "video"


class Language(StrEnum):
    ES = "es"
    EN = "en"


class MatrixLanguage(StrEnum):
    ES = "es"
    EN = "en"
    MIXED = "mixed"


class Sentiment(StrEnum):
    NEUTRAL = "neutral"
    POSITIVE = "positive"
    CONFUSED = "confused"
    FRUSTRATED = "frustrated"


class Intent(StrEnum):
    ANSWER = "answer"
    QUESTION = "question"
    SMALL_TALK = "small_talk"
    OFF_TOPIC = "off_topic"
    ABUSE = "abuse"
    OPT_OUT = "opt_out"
    CONFIRM = "confirm"
    CORRECTION = "correction"
    UNCLEAR = "unclear"


class QuestionTopic(StrEnum):
    """Questions the engine answers itself, whatever the FAQ or the model would say."""

    AI_IDENTITY = "ai_identity"


type StepId = str
"""A funnel step: one of the engine's own (`Stage`), or the `funnel_step` an agent spec gives
its fields while they are asked ("license" for riders, "levels" for teachers)."""


class Stage(StrEnum):
    """The funnel steps every agent has, around its fields' own (`StepId`)."""

    CONSENT = "consent"
    CONFIRM = "confirm"
    MODE = "mode"
    SCHEDULING = "scheduling"
    DONE = "done"


class Outcome(StrEnum):
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"
    ABANDONED = "abandoned"
    DECLINED_CONSENT = "declined_consent"
    WITHDRAWN = "withdrawn"
    """Retirada: the candidate qualified, booked an interview and then cancelled it (or opted
    out with it booked). Neither a qualification nor a disqualification."""


class QualityFlag(StrEnum):
    UNRESOLVED_FIELD = "unresolved_field"
    FRUSTRATION = "frustration"
    ABUSE = "abuse"
    UNKNOWN_FAQ = "unknown_faq"
    INJECTION_SUSPECTED = "injection_suspected"
    GATE_FLAG = "gate_flag"
    """A gate let an answer through for the recruiter to check (a certificate being requested)."""


class CandidateProfile(BaseModel):
    """Validated screening data. A field is None until the candidate answers it.

    The attributes below are the riders' and those every agent shares (the name, the contacts,
    the location, the start date, the interview mode). Another agent's fields are kept by their
    ids, as extra attributes (`spec.FieldSpec.attrs`); a date among them is read back as one.
    """

    model_config = ConfigDict(extra="allow")

    full_name: str | None = None
    phone: str | None = Field(default=None, description="Mobile number in E.164 format.")
    email: str | None = None
    has_license: bool | None = None
    location_id: str | None = None
    place_said: str | None = Field(
        default=None,
        description=(
            "The zone or colonia the candidate named that matched `location_id`, as the service "
            'areas spell it ("Roma" for the Cuauhtémoc location); None when they named the '
            "location or the city itself."
        ),
    )
    availability: list[Availability] | None = None
    schedule: list[Schedule] | None = Field(
        default=None, description="One or more blocks; `flexible` stands alone."
    )
    experience_years: float | None = Field(default=None, ge=0)
    platforms: list[Platform] | None = None
    platform_other: str | None = None
    start_date: date | None = None
    late_start_days: int | None = Field(
        default=None, description="Days until the start date when it is more than 60 days out."
    )
    interview_mode: InterviewMode | None = Field(
        default=None,
        description="The interview mode the candidate chose, or the only one there is.",
    )
    experience_unverified: bool | None = Field(
        default=None,
        description=(
            "The candidate confirmed more years than a platform they named has existed in the "
            "country: kept, for the recruiter to check."
        ),
    )

    @field_validator("schedule", mode="before")
    @classmethod
    def _schedule_list(cls, value: object) -> object:
        """A profile stored before SAZ-41 holds one schedule as a string; read it as a list."""
        return schedule_list(value)

    @model_validator(mode="after")
    def _extra_dates(self) -> Self:
        """An agent's own `date` field, stored as an ISO string, read back as a date."""
        from sazon.engine.spec import FieldType, agent  # the spec reads the profile

        extra = self.__pydantic_extra__ or {}
        for name, value in extra.items():
            spec = agent().by_attr.get(name)
            if spec is not None and spec.type == FieldType.DATE and isinstance(value, str):
                extra[name] = date.fromisoformat(value)
        return self


class PhoneInput(BaseModel):
    """A number typed into the phone widget, with the country prefix the candidate picked."""

    country: Literal["ES", "MX"]
    number: str


class FieldCandidate(BaseModel):
    value: str | bool | float | list[str] | PhoneInput = Field(
        description="The value as the candidate stated it, lightly normalized; its type is the "
        "field's (`llm.extraction`)."
    )
    confidence: float = Field(
        ge=0, le=1, description="How sure you are that the candidate meant this value, 0 to 1."
    )


MAX_SUB_QUESTIONS = 3
"""The parts of a multi-part question retrieval looks up; the reply limits fit no more."""


class TurnExtraction(BaseModel):
    intent: Intent = Field(description="What the candidate is doing in this message.")
    fields: dict[FieldId, FieldCandidate] = Field(
        default_factory=dict,
        description="Field values stated in this message, by field id. The extractor writes "
        "them as an object with an attribute per field (`llm.extraction.output_model`).",
    )
    language: Language = Field(description="Language the candidate wrote in.")
    matrix_language: MatrixLanguage = Field(
        description=(
            "Grammatical frame of the message. A Spanish sentence with English words "
            "inserted ('puedo full time') is 'es'."
        )
    )
    english_ratio: float = Field(ge=0, le=1, description="Share of words that are English, 0 to 1.")
    explicit_language_request: Language | None = Field(
        default=None,
        description="Set only if the candidate explicitly asks to switch language.",
    )
    code_switched: bool = Field(description="True if the message mixes Spanish and English.")
    sentiment: Sentiment
    question: str | None = Field(
        default=None,
        description=(
            "The candidate's question, if they asked one, written to stand on its own: a "
            "follow-up that leans on earlier turns names what it is about."
        ),
    )
    sub_questions: list[str] = Field(
        default_factory=list,
        description=(
            "Only when the message asks several different things: each one as its own "
            f"standalone question, at most {MAX_SUB_QUESTIONS}. Empty for a single question."
        ),
    )
    question_topic: QuestionTopic | None = Field(
        default=None,
        description=(
            "'ai_identity' when the question asks whether Ana is an AI, a bot, a robot "
            "or a real person."
        ),
    )
    slot: str | None = Field(
        default=None,
        description=(
            "Only when interview slots are offered or one is booked: the `id` of the offered "
            "slot the candidate picks, 'none' if none of the offered times suit them, 'change' "
            "if they want to move or can't make the interview already booked. Otherwise null."
        ),
    )
    declines_field: bool = Field(
        default=False,
        description=(
            "True when the candidate refuses to give the data Ana asked for, whatever the field "
            "('no quiero dar mi correo', 'prefiero no decirlo'). Not a wish to stop the "
            "application."
        ),
    )
    mode: str | None = Field(
        default=None,
        description=(
            "Only when the context lists `interview_modes`: 'onsite' if the candidate wants the "
            "interview in person, 'video' if by video call (Google Meet), including a change of "
            "mind ('mejor por video'). Otherwise null."
        ),
    )

    @field_validator("sub_questions", mode="after")
    @classmethod
    def _parts(cls, value: list[str]) -> list[str]:
        """Distinct, non-empty parts, at most `MAX_SUB_QUESTIONS`; one part is no split."""
        parts = list(dict.fromkeys(part.strip() for part in value if part and part.strip()))
        return parts[:MAX_SUB_QUESTIONS] if len(parts) > 1 else []
