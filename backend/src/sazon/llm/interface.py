"""The boundary between the screening service and any language model."""

from collections.abc import Callable, Sequence
from datetime import date
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from sazon.engine.actions import Action
from sazon.engine.models import CandidateProfile, Language, TurnExtraction
from sazon.engine.spec import agent
from sazon.engine.state import ConversationState
from sazon.engine.ui import Ui
from sazon.engine.validators import Country

FAQ_ANSWER_MARK = "\nA: "


def persona_name() -> str:
    """The name the agent in use goes by ("Ana")."""
    persona = agent().persona
    return persona.name if persona else ""


# The `model` of a reply sent as written by design: no model was called.
FIXED_REPLY_MODEL = "template"


def faq_snippet(question: str, answer: str) -> str:
    """How a retrieved FAQ entry reaches the responder."""
    return f"Q: {question}{FAQ_ANSWER_MARK}{answer}"


def faq_answer(snippet: str) -> str:
    """The answer part of a `faq_snippet`."""
    return snippet.partition(FAQ_ANSWER_MARK)[2] or snippet


class Role(StrEnum):
    EXTRACTOR = "extractor"
    RESPONDER = "responder"
    SUMMARIZER = "summarizer"


class Channel(StrEnum):
    WEB = "web"
    VOICE = "voice"


class ChatTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["candidate", "assistant"]
    text: str


class CallRecord(BaseModel):
    """Cost and routing of one model call. `fallback_used` means no model output was used."""

    model_config = ConfigDict(frozen=True)

    role: Role
    model: str
    provider: str | None = None
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    fallback_used: bool = False
    error: str | None = None


class FaqPart(BaseModel):
    """One part of a question that asks several things and the snippets found for it (none:
    the fixed line for the parts Ana can't answer covers it)."""

    model_config = ConfigDict(frozen=True)

    question: str
    snippets: tuple[str, ...] = ()


class ReplyContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel: Channel = Channel.WEB
    language: Language
    country: Country
    simplify: bool = False
    persona: str = Field(default_factory=persona_name)
    faq_snippets: tuple[str, ...] = ()
    faq_send_as_written: bool = False
    """The first snippet is a close FAQ entry: the template may send it as written when the
    responder fails. Otherwise the fallback is the unknown answer (and an FAQ miss)."""
    faq_parts: tuple[FaqPart, ...] = ()
    """A question that asks several things: `faq_snippets` grouped by part. Empty for one."""
    faq_country: Country | None = None
    """The snippets come from the other country's documents: the question named it ("¿Y en
    México?" in a Spanish conversation), so the answer says which country it is about."""
    today: date
    candidate_name: str | None = None
    last_message: str | None = None

    @classmethod
    def for_action(
        cls,
        action: Action,
        *,
        today: date,
        channel: Channel = Channel.WEB,
        faq_snippets: Sequence[str] = (),
        faq_send_as_written: bool = False,
        faq_parts: Sequence[FaqPart] = (),
        faq_country: Country | None = None,
        candidate_name: str | None = None,
        last_message: str | None = None,
    ) -> "ReplyContext":
        return cls(
            channel=channel,
            language=action.language,
            country=action.country,
            simplify=action.simplify,
            faq_snippets=tuple(faq_snippets),
            faq_send_as_written=faq_send_as_written,
            faq_parts=tuple(faq_parts),
            faq_country=faq_country,
            today=today,
            candidate_name=candidate_name,
            last_message=last_message,
        )


class Reply(BaseModel):
    """Text from the responder; `ui` is the engine's widget, passed through unchanged."""

    model_config = ConfigDict(frozen=True)

    text: str
    ui: Ui | None = None


class CandidateSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    lines: tuple[str, ...] = Field(max_length=3)
    key_fields: dict[str, str]
    flags: tuple[str, ...] = ()


class ExtractResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    extraction: TurnExtraction
    call: CallRecord


class ReplyResult(BaseModel):
    """`answered`: for an FAQ answer, whether the snippets answered the question (None when
    unknown, e.g. the template was sent); for one that asks several things, any part of it.
    `missed_parts`: the parts the answer says it can't answer, with or without snippets."""

    model_config = ConfigDict(frozen=True)

    reply: Reply
    call: CallRecord
    answered: bool | None = None
    missed_parts: tuple[str, ...] = ()


class SummaryResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: CandidateSummary
    call: CallRecord


class LLM(Protocol):
    """Never raises on model failure: results fall back and set `call.fallback_used`."""

    async def extract(
        self,
        history: Sequence[ChatTurn],
        state: ConversationState,
        message: str,
        *,
        today: date | None = None,
    ) -> ExtractResult: ...

    async def respond(self, action: Action, ctx: ReplyContext) -> ReplyResult: ...

    async def summarize(
        self,
        transcript: Sequence[ChatTurn],
        profile: CandidateProfile,
        *,
        language: Language = Language.ES,
        country: Country = "ES",
        today: date | None = None,
    ) -> SummaryResult:
        """`today` is the candidate's date, against which relative dates were resolved."""
        ...


type Emit = Callable[[str], None]
"""Receives a reply as it is written, a sentence or more at a time."""


@runtime_checkable
class StreamingResponder(Protocol):
    """An LLM that can hand over a reply sentence by sentence (the voice channel, SAZ-16).

    `emit` gets each piece once it passed the same checks as a whole reply; the result holds
    the full text, which is what was emitted. Never raises on model failure.
    """

    async def respond_stream(
        self, action: Action, ctx: ReplyContext, emit: Emit
    ) -> ReplyResult: ...
