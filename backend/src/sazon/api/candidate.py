"""Candidate chat: start a conversation, send turns (SSE or JSON), reload the transcript."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, TypeAdapter

from sazon.api.deps import Guard, Service, limit_conversations, limit_messages
from sazon.api.schemas import (
    ConfirmAction,
    ErrorOut,
    MessageIn,
    MessageOut,
    NudgeIn,
    NudgeOut,
    PhoneAction,
    ReplyOut,
    StartIn,
    StartOut,
    StateOut,
    TranscriptOut,
    TranscriptTurn,
    WidgetAction,
)
from sazon.engine import ConversationContext
from sazon.engine import start as engine_start
from sazon.engine.spec import agent
from sazon.engine.ui import Ui
from sazon.llm.fallback_templates import pack, register_of
from sazon.llm.interface import Channel
from sazon.observability import record
from sazon.service import NudgeRejected, Reply, ScreeningService, WidgetAnswer, is_ended
from sazon.storage.models import Conversation, Turn, TurnRole
from sazon.storage.repo import Repo, state_of

router = APIRouter(prefix="/api/conversations", tags=["candidate"])

UI: TypeAdapter[Ui | None] = TypeAdapter(Ui | None)  # pyright: ignore[reportArgumentType]
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorOut}}
SSE_DOC: dict[str, Any] = {
    "description": (
        "`text/event-stream` (default) or JSON with `Accept: application/json`. "
        "SSE events, in order: `reply` (ReplyOut), then `done` (MessageOut). "
        "An `error` event (ErrorOut) replaces `done` if the turn fails."
    ),
    "content": {
        "text/event-stream": {"schema": {"type": "string"}},
        "application/json": {"schema": {"$ref": "#/components/schemas/MessageOut"}},
    },
}


@router.post(
    "",
    response_model=StartOut,
    responses={429: {"model": ErrorOut}, 503: {"model": ErrorOut}},
    dependencies=[Depends(limit_conversations)],
)
async def start_conversation(body: StartIn, service: Service, guard: Guard) -> StartOut:
    """Open a chat. Ana's greeting asks for consent with a yes/no widget.

    `restart_of`: the chat the candidate left with "Empezar de nuevo"; an application still
    open there closes, so the same phone or email starts a fresh one here.

    With the daily model budget spent, nothing is stored: `conversation_id` is empty and the
    reply asks the candidate to come back later, with `state.ended` true. 429 past the per-IP
    limit on new chats."""
    context = ConversationContext(**body.context.model_dump())
    if guard is not None and await asyncio.to_thread(guard.over_budget):
        return _busy(context)
    if body.restart_of:
        await service.restart(body.restart_of)
    reply = await service.start(body.channel, context)
    if not reply.conversation_id:
        raise HTTPException(503, "The chat could not be started. Please try again.")
    conversation = await asyncio.to_thread(_conversation, service.repo, reply.conversation_id)
    return StartOut(
        conversation_id=reply.conversation_id,
        reply=ReplyOut(text=reply.text, ui=reply.ui),
        state=state_out(conversation),
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageOut,
    responses={200: SSE_DOC, **NOT_FOUND, 429: {"model": ErrorOut}},
    dependencies=[Depends(limit_messages)],
)
async def send_message(
    conversation_id: str, body: MessageIn, request: Request, service: Service
) -> StreamingResponse | JSONResponse:
    """Send free `text` or a widget `action` and get Ana's answer.

    Streams Server-Sent Events unless the client asks for `application/json`:
    - `reply`: `{text, ui}` — what Ana says and the widget to render.
    - `done`: `{conversation_id, resumed, reply, state}` — the final event of a turn.
    - `error`: `{error, detail}` — the turn failed; nothing else follows.
    """
    await asyncio.to_thread(_conversation, service.repo, conversation_id)
    message = _message(body)
    if _wants_json(request):
        reply = await service.handle_turn(conversation_id, message, channel=Channel.WEB)
        out = await asyncio.to_thread(message_out, service.repo, conversation_id, reply)
        return JSONResponse(out.model_dump(mode="json"))

    async def events() -> AsyncIterator[str]:
        try:
            reply = await service.handle_turn(conversation_id, message, channel=Channel.WEB)
            out = await asyncio.to_thread(message_out, service.repo, conversation_id, reply)
        except Exception:
            yield sse("error", ErrorOut(error="error", detail="Something went wrong."))
            return
        yield sse("reply", out.reply)
        yield sse("done", out)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/{conversation_id}/nudge",
    response_model=NudgeOut,
    responses={**NOT_FOUND, 409: {"model": ErrorOut}},
    dependencies=[Depends(limit_messages)],
)
async def nudge(conversation_id: str, body: NudgeIn, service: Service) -> NudgeOut:
    """Re-engage a candidate who went quiet with the chat open. The browser keeps the timer:
    attempt 1 after 3 minutes of silence, attempt 2 after 15 more.

    Asking again for an attempt already sent since the candidate last wrote returns that nudge.
    409 when the chat has ended (or has been idle past the abandonment limit), the candidate
    paused it (`state.paused`), the attempt is above 2, or the previous attempt was not sent.
    """
    await asyncio.to_thread(_conversation, service.repo, conversation_id)
    try:
        reply = await service.nudge(conversation_id, body.attempt)
    except NudgeRejected as exc:
        raise HTTPException(409, str(exc)) from exc
    conversation = await asyncio.to_thread(_conversation, service.repo, conversation_id)
    return NudgeOut(reply=ReplyOut(text=reply.text, ui=reply.ui), state=state_out(conversation))


@router.get("/{conversation_id}", response_model=TranscriptOut, responses=NOT_FOUND)
async def get_transcript(conversation_id: str, service: Service) -> TranscriptOut:
    """The chat as the candidate saw it, to restore it after a reload. Only the last turn,
    when it is Ana's and the chat is still open, carries `ui`: the widget waiting for an answer.
    A booked-interview card is display only and always comes back.

    Coming back to an application abandoned for silence reopens it where it stopped; coming
    back after the recruiter's calendar cancelled the interview offers new times."""
    await asyncio.to_thread(_conversation, service.repo, conversation_id)
    await service.resume_if_idle(conversation_id)
    await service.reoffer_if_pending(conversation_id)
    return await asyncio.to_thread(transcript, service, conversation_id)


def transcript(service: ScreeningService, conversation_id: str) -> TranscriptOut:
    """`get_transcript`'s reads. Blocking: run it in a thread."""
    repo = service.repo
    conversation = _conversation(repo, conversation_id)
    state = state_out(conversation)
    shown = repo.shown_turns(conversation_id)
    waiting = shown[-1] if shown and shown[-1].role == TurnRole.AGENT and not state.ended else None
    turns = [
        TranscriptTurn(
            role="agent" if turn.role == TurnRole.AGENT else "candidate",
            text=turn.text or "",
            ui=UI.validate_python(turn.ui) if turn is waiting or _booking(turn) else None,
            nudge=turn.nudge,
            created_at=turn.created_at,
        )
        for turn in shown
    ]
    return TranscriptOut(
        conversation_id=conversation.id,
        channel=Channel(conversation.channel),
        state=state,
        turns=turns,
        continued_in=service.continued_in(conversation),
    )


# --- helpers ---


def _busy(context: ConversationContext) -> StartOut:
    """The come-back-later reply while the daily model budget is spent. No model, no record."""
    state, _ = engine_start(context)
    record("chat_turned_away", reason="budget")
    return StartOut(
        conversation_id="",
        reply=ReplyOut(text=pack(register_of(state.language, state.country)).busy, ui=None),
        state=StateOut(
            stage=state.stage, language=state.language, country=state.country, ended=True
        ),
    )


def _booking(turn: Turn) -> bool:
    return turn.ui is not None and turn.ui.get("kind") == "booking"


def sse(event: str, data: BaseModel) -> str:
    return f"event: {event}\ndata: {data.model_dump_json()}\n\n"


def state_out(conversation: Conversation) -> StateOut:
    state = state_of(conversation)
    ended = is_ended(state)
    return StateOut(
        stage=state.stage,
        outcome=state.outcome,
        language=state.language,
        country=state.country,
        ended=ended,
        restartable=agent().unresolved(state.disqualify_reason),
        reopenable=ended and state.reopenable,
        paused=state.paused,
    )


def message_out(repo: Repo, conversation_id: str, reply: Reply) -> MessageOut:
    current = repo.get_conversation(reply.conversation_id) or _conversation(repo, conversation_id)
    return MessageOut(
        conversation_id=current.id,
        resumed=current.id != conversation_id,
        candidate_text=reply.candidate_text,
        reply=ReplyOut(text=reply.text, ui=reply.ui),
        state=state_out(current),
    )


def _conversation(repo: Repo, conversation_id: str) -> Conversation:
    conversation = repo.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(404, "Conversation not found.")
    return conversation


def _message(body: MessageIn) -> str | WidgetAnswer:
    if body.text is not None:
        return body.text
    action: WidgetAction | None = body.action
    if isinstance(action, ConfirmAction):
        return WidgetAnswer(value="yes" if action.value else "no", kind=action.kind)
    if isinstance(action, PhoneAction):
        return WidgetAnswer(value=action.value, kind=action.kind)
    if action is None:
        raise HTTPException(422, "Send 'text' or 'action'.")
    return WidgetAnswer(value=action.value, kind=action.kind)


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/event-stream" not in accept
