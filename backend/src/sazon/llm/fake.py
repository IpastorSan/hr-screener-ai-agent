"""A scripted LLM for service and API tests: queued extractions, template replies."""

from collections import deque
from collections.abc import Iterable, Sequence
from datetime import date

from sazon.engine.actions import Action, AnswerQuestion
from sazon.engine.models import CandidateProfile, Language, TurnExtraction
from sazon.engine.state import ConversationState
from sazon.engine.validators import Country
from sazon.llm.fallback_templates import fallback_extraction, fallback_reply, fallback_summary
from sazon.llm.interface import (
    CallRecord,
    ChatTurn,
    Emit,
    ExtractResult,
    Reply,
    ReplyContext,
    ReplyResult,
    Role,
    SummaryResult,
)

FAKE_MODEL = "fake"


class FakeLLM:
    """Pops queued extractions and replies; when a queue is empty it behaves like a failed call."""

    def __init__(
        self,
        extractions: Iterable[TurnExtraction] = (),
        replies: Iterable[str] = (),
        *,
        faq_answered: bool | None = None,
    ) -> None:
        """`faq_answered` is what an FAQ answer reports about its snippets."""
        self.extractions = deque(extractions)
        self.replies = deque(replies)
        self.faq_answered = faq_answered
        self.messages: list[str] = []
        self.actions: list[Action] = []

    def queue(self, *extractions: TurnExtraction) -> None:
        self.extractions.extend(extractions)

    def queue_replies(self, *replies: str) -> None:
        self.replies.extend(replies)

    async def extract(
        self,
        history: Sequence[ChatTurn],
        state: ConversationState,
        message: str,
        *,
        today: date | None = None,
    ) -> ExtractResult:
        self.messages.append(message)
        if self.extractions:
            return ExtractResult(extraction=self.extractions.popleft(), call=_call(Role.EXTRACTOR))
        return ExtractResult(
            extraction=fallback_extraction(state.language),
            call=_call(Role.EXTRACTOR, fallback_used=True),
        )

    async def respond(self, action: Action, ctx: ReplyContext) -> ReplyResult:
        self.actions.append(action)
        text = self.replies.popleft() if self.replies else fallback_reply(action, ctx)
        answered = self.faq_answered if isinstance(action, AnswerQuestion) else None
        return ReplyResult(
            reply=Reply(text=text, ui=action.ui), call=_call(Role.RESPONDER), answered=answered
        )

    async def respond_stream(self, action: Action, ctx: ReplyContext, emit: Emit) -> ReplyResult:
        result = await self.respond(action, ctx)
        emit(result.reply.text)
        return result

    async def summarize(
        self,
        transcript: Sequence[ChatTurn],
        profile: CandidateProfile,
        *,
        language: Language = Language.ES,
        country: Country = "ES",
        today: date | None = None,
    ) -> SummaryResult:
        return SummaryResult(
            summary=fallback_summary(profile, language, country), call=_call(Role.SUMMARIZER)
        )


def _call(role: Role, *, fallback_used: bool = False) -> CallRecord:
    return CallRecord(role=role, model=FAKE_MODEL, fallback_used=fallback_used)
