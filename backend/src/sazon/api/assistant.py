"""Recruiter assistant ("Preguntar a Ana"): a question in, a short answer with insights out."""

import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from sazon.api.deps import CurrentRecruiter, Guard, require_recruiter
from sazon.api.schemas import ErrorOut
from sazon.config import get_settings
from sazon.observability import record
from sazon.recruiter_assistant import (
    MAX_QUESTION_CHARS,
    MAX_TEXT_CHARS,
    AskAnswer,
    Assistant,
    AssistantError,
    HistoryTurn,
    OfflineAssistant,
    RecruiterAssistant,
)
from sazon.recruiters import location_scope
from sazon.service import ScreeningService

logger = logging.getLogger(__name__)

UNAVAILABLE = "El asistente no está disponible ahora. Inténtalo de nuevo."

router = APIRouter(
    prefix="/api/assistant",
    tags=["recruiter"],
    dependencies=[Depends(require_recruiter)],
    responses={503: {"model": ErrorOut}},
)


class AskTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS * 4)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    history: list[AskTurn] = Field(default_factory=list, max_length=10)


def get_assistant(request: Request) -> Assistant:
    """One assistant per app, built on first use: offline with `SAZON_FAKE_LLM=1`, otherwise on
    the screening model's OpenRouter client."""
    assistant: Assistant | None = getattr(request.app.state, "assistant", None)
    if assistant is None:
        service: ScreeningService = request.app.state.service
        if get_settings().sazon_fake_llm:
            assistant = OfflineAssistant(service.repo, clock=service.clock)
        else:
            provider = getattr(service.llm, "provider", None)
            assistant = RecruiterAssistant(service.repo, provider=provider, clock=service.clock)
        request.app.state.assistant = assistant
    return assistant


@router.post("", response_model=AskAnswer, response_model_exclude_none=True)
async def ask(
    body: AskIn,
    me: CurrentRecruiter,
    assistant: Annotated[Assistant, Depends(get_assistant)],
    guard: Guard,
) -> AskAnswer:
    """Answer a recruiter's question about candidates or the pipeline with a short text and
    typed insights (stat, candidates, bar, funnel), from the candidates at the recruiter's
    locations (every location for an admin). Read-only. 503 while the daily model budget
    is spent."""
    if guard is not None and await asyncio.to_thread(guard.over_budget):
        record("assistant_turned_away", reason="budget")
        raise HTTPException(503, UNAVAILABLE)
    history = [HistoryTurn(role=turn.role, text=turn.text) for turn in body.history]
    try:
        result = await assistant.ask(body.question, history, location_ids=location_scope(me))
    except AssistantError as exc:
        raise HTTPException(503, UNAVAILABLE) from exc
    if guard is not None:
        guard.add(result.cost_usd)
    logger.info(
        "Recruiter assistant: model=%s tools=%s cost=%s latency_ms=%s dropped=%d",
        result.model,
        ",".join(result.tool_calls),
        result.cost_usd,
        result.latency_ms,
        len(result.dropped),
    )
    return result.answer
