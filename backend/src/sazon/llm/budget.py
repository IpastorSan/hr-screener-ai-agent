"""The daily spend cap applied to model calls: past it, chats carry on without a model."""

import asyncio
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from sazon.engine.actions import Action
from sazon.engine.models import CandidateProfile, Language
from sazon.engine.state import ConversationState
from sazon.engine.validators import Country
from sazon.llm.interface import (
    LLM,
    ChatTurn,
    Emit,
    ExtractResult,
    ReplyContext,
    ReplyResult,
    StreamingResponder,
    SummaryResult,
)
from sazon.llm.offline import OfflineLLM

BUDGET_ERROR = "daily model budget reached"


class BudgetedLLM:
    """Calls `primary` while `over_budget()` is false. Past the cap, the keyword extractor reads
    messages and replies and summaries come from the fallback templates (`fallback_used`).

    `over_budget` may read the database, so it runs in a thread, off the event loop."""

    def __init__(self, primary: LLM, over_budget: Callable[[], bool]) -> None:
        self.primary = primary
        self.over_budget = over_budget
        self.offline = OfflineLLM()

    async def _over_budget(self) -> bool:
        return await asyncio.to_thread(self.over_budget)

    def __getattr__(self, name: str) -> Any:
        # `provider` and anything else callers read off the real model client.
        return getattr(self.primary, name)

    async def extract(
        self,
        history: Sequence[ChatTurn],
        state: ConversationState,
        message: str,
        *,
        today: date | None = None,
    ) -> ExtractResult:
        if not await self._over_budget():
            return await self.primary.extract(history, state, message, today=today)
        return await self.offline.extract(history, state, message, today=today)

    async def respond(self, action: Action, ctx: ReplyContext) -> ReplyResult:
        if not await self._over_budget():
            return await self.primary.respond(action, ctx)
        result = await self.offline.respond(action, ctx)
        # `actions` records every action for tests; in a long-lived process it would only grow.
        self.offline.actions.clear()
        call = result.call.model_copy(update={"fallback_used": True, "error": BUDGET_ERROR})
        return result.model_copy(update={"call": call})

    async def respond_stream(self, action: Action, ctx: ReplyContext, emit: Emit) -> ReplyResult:
        if not await self._over_budget() and isinstance(self.primary, StreamingResponder):
            return await self.primary.respond_stream(action, ctx, emit)
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
        if not await self._over_budget():
            return await self.primary.summarize(
                transcript, profile, language=language, country=country, today=today
            )
        result = await self.offline.summarize(
            transcript, profile, language=language, country=country, today=today
        )
        call = result.call.model_copy(update={"fallback_used": True, "error": BUDGET_ERROR})
        return result.model_copy(update={"call": call})
