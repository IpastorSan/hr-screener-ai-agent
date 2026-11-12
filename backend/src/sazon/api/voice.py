"""Voice routes (SAZ-16): the custom LLM endpoint ElevenLabs Agents calls, and the call setup.

- `POST /v1/chat/completions`: OpenAI chat completions, streamed as SSE chunks then `[DONE]`.
  ElevenLabs appends `/chat/completions` to the configured custom LLM URL, so the agent is set
  up with `https://<DOMAIN>/v1`. Authenticated with `VOICE_LLM_SECRET`.
- `POST /api/conversations/{id}/voice`: what the call page needs to start an ElevenLabs
  session on one of our conversations (dynamic variables, extra body, voice, optional token).
"""

import asyncio
import json
import logging
import time
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from sazon.api.deps import Service, limit_messages
from sazon.api.schemas import ErrorOut
from sazon.channels.voice import (
    CONVERSATION_VAR,
    END_CALL_TOOL,
    FILLER,
    LANGUAGE_TOOL,
    NOT_HEARD,
    RATE_LIMITED,
    UNKNOWN_CONVERSATION,
    ChatCompletionRequest,
    Completion,
    TurnCache,
    after_tool_call,
    language_switch,
    latest_user_turn,
    offers_tool,
    opening_line,
    register_language,
    remember_elevenlabs_id,
    run_turn,
    session_of,
    spoken_language,
    still_there,
)
from sazon.channels.voice import authorized as secret_ok
from sazon.channels.voice import ended as conversation_ended
from sazon.config import Settings, get_settings
from sazon.guards import TokenBucket
from sazon.llm.fallback_templates import register_of
from sazon.llm.interface import Channel
from sazon.observability import record
from sazon.service import ScreeningService
from sazon.storage.models import Conversation
from sazon.storage.repo import state_of

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice"])
OPENING_VAR = "opening_line"
TOKEN_TIMEOUT_S = 5.0


def voice_settings(request: Request) -> Settings:
    """`app.state.voice_settings` when a test sets it, else the environment."""
    return getattr(request.app.state, "voice_settings", None) or get_settings()


def turn_cache(request: Request) -> TurnCache:
    cache = getattr(request.app.state, "voice_turns", None)
    if cache is None:
        cache = request.app.state.voice_turns = TurnCache()
    return cache


# --- the custom LLM ---


@router.post(
    "/v1/chat/completions",
    response_model=None,
    responses={401: {"model": ErrorOut}},
    summary="Custom LLM for ElevenLabs Agents (OpenAI chat completions)",
)
async def chat_completions(
    body: ChatCompletionRequest, request: Request, service: Service
) -> StreamingResponse | JSONResponse:
    """One spoken turn. Only the latest `user` message is read; our database holds the call.

    With no user message yet, the opening line. A silent one ("...", ElevenLabs' turn
    timeout) is no turn: a short "are you still there?", nothing stored.

    The conversation comes from `elevenlabs_extra_body.sazon_conversation_id`, the
    `X-Sazon-Conversation-Id` header or a `sazon_conversation_id=<id>` line in the system
    prompt. An unknown one gets a spoken apology, never an error: ElevenLabs would retry it.
    With `stream: true` (what ElevenLabs sends) the reply streams as `chat.completion.chunk`
    events, sentence by sentence, then `data: [DONE]`. When the screening has ended and the
    agent offers the `end_call` tool, a call to it follows the text. When the turn changed the
    conversation's language and the agent offers `language_detection`, a call to it follows the
    text instead, so the next turn is heard in that language's voice (SAZ-59).
    """
    settings = voice_settings(request)
    if not secret_ok(request.headers, settings.voice_llm_secret.get_secret_value()):
        raise HTTPException(401, "Missing or wrong voice secret.")
    started = time.perf_counter()
    session = session_of(body, request.headers)
    turn = latest_user_turn(body.messages)
    words = turn[1] if turn else ""
    conversation = (
        await asyncio.to_thread(service.repo.get_conversation, session.conversation_id)
        if session.conversation_id
        else None
    )
    language = register_language(conversation) or spoken_language(session, words)
    completion = Completion(body.model)
    count = turn[0] if turn else 0
    if conversation is not None and (
        after_tool_call(body) or turn_cache(request).hung_up(conversation.id, count)
    ):
        # The reply that called a tool was said (the goodbye before end_call, or the reply
        # before language_detection): nothing more, however often we are asked.
        record("voice_after_end_call")
        return _silence(completion, body.stream)

    async def pieces() -> AsyncIterator[str]:
        if conversation is None:
            record("voice_unknown_conversation")
            yield UNKNOWN_CONVERSATION[language]
            return
        await asyncio.to_thread(
            remember_elevenlabs_id, service, conversation.id, session.elevenlabs_conversation_id
        )
        if _rate_limited(request, conversation.id):
            yield RATE_LIMITED[language]
            return
        if turn is None:
            yield (
                await asyncio.to_thread(opening_line, service, conversation) or NOT_HEARD[language]
            )
            return
        if not words:
            record("voice_silence")
            yield still_there(conversation)
            return
        async for piece in run_turn(service, turn_cache(request), conversation.id, *turn):
            yield piece

    hang_up = offers_tool(body, END_CALL_TOOL)
    switches = offers_tool(body, LANGUAGE_TOOL) and bool(words)

    if not body.stream:  # ElevenLabs always streams; this answers curl and the tests
        text = " ".join([piece async for piece in pieces()])
        _measure(started, started, conversation)
        return JSONResponse(completion.whole(text))

    filler_after = settings.voice_filler_after_s

    async def events() -> AsyncIterator[str]:
        first: float | None = None
        source = pieces()
        if filler_after > 0 and conversation is not None and words:
            source = with_filler(source, filler_after, FILLER[language])
        async for piece in source:
            first = first or time.perf_counter()
            yield completion.text(piece, lead_space=not completion.first_pending)
        if (
            hang_up
            and conversation is not None
            and await asyncio.to_thread(_ended, service, conversation.id, request)
        ):
            turn_cache(request).hang_up(conversation.id, count)
            yield completion.end_call()
            yield completion.stop("tool_calls")
        elif (
            switches
            and conversation is not None
            and (
                switch := await asyncio.to_thread(
                    language_switch,
                    service,
                    turn_cache(request),
                    session,
                    conversation.id,
                    count,
                    language,
                )
            )
        ):
            # The reply is said in the old voice; the next turn in the new one.
            record("voice_language_switch", language=switch.value)
            yield completion.switch_language(switch)
            yield completion.stop("tool_calls")
        else:
            yield completion.stop()
        yield completion.done()
        _measure(started, first or started, conversation)

    return _sse(events())


def _sse(events: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _silence(completion: Completion, stream: bool) -> StreamingResponse | JSONResponse:
    """An empty completion: no text, finish `stop`."""
    if not stream:
        return JSONResponse(completion.whole(""))

    async def events() -> AsyncIterator[str]:
        yield completion.stop()
        yield completion.done()

    return _sse(events())


async def with_filler(
    source: AsyncIterator[str], after_s: float, filler: str
) -> AsyncIterator[str]:
    """ElevenLabs' advice for a slow first token: say a short buffer ending in "... " so the
    speech carries on naturally. Only when the reply is not ready within `after_s`."""
    iterator = aiter(source)
    first = asyncio.ensure_future(anext(iterator))
    done, _ = await asyncio.wait({first}, timeout=after_s)
    if not done:
        yield filler
    try:
        yield await first
    except StopAsyncIteration:
        return
    async for piece in iterator:
        yield piece


def _to_voice(service: ScreeningService, conversation_id: str) -> tuple[Conversation, str]:
    """`start_voice`'s reads and the channel change, with the opening line. Blocking: run it
    in a thread."""
    conversation = service.repo.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(404, "Conversation not found.")
    line = opening_line(service, conversation)
    if line is None:
        raise HTTPException(409, "The conversation has ended.")
    if conversation.channel != Channel.VOICE.value:
        conversation.channel = Channel.VOICE.value
        service.repo.save(conversation)
    return conversation, line


def _ended(service: ScreeningService, conversation_id: str, request: Request) -> bool:
    return conversation_ended(service, turn_cache(request).resolve(conversation_id))


def _rate_limited(request: Request, conversation_id: str) -> bool:
    """The per-minute message limit, per conversation: every call comes from ElevenLabs'
    servers, so the caller's IP says nothing about the candidate."""
    bucket: TokenBucket | None = getattr(request.app.state, "message_limit", None)
    if bucket is None or bucket.take(f"voice:{conversation_id}") is None:
        return False
    record("rate_limited", bucket="voice")
    return True


def _measure(started: float, first: float, conversation: Any) -> None:
    now = time.perf_counter()
    record(
        "voice_turn",
        first_chunk_ms=round((first - started) * 1000),
        total_ms=round((now - started) * 1000),
        known=conversation is not None,
    )


# --- call setup ---


class VoiceSessionOut(BaseModel):
    """Everything `startSession` needs. `conversation_token` is set only for a private agent
    (with `ELEVENLABS_API_KEY` on the server); a public agent starts with `agent_id` alone."""

    conversation_id: str
    agent_id: str | None
    conversation_token: str | None = None
    language: str
    voice_id: str | None = None
    opening_line: str
    dynamic_variables: dict[str, str]
    custom_llm_extra_body: dict[str, str]


@router.post(
    "/api/conversations/{conversation_id}/voice",
    response_model=VoiceSessionOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 429: {"model": ErrorOut}},
    dependencies=[Depends(limit_messages)],
    tags=["candidate"],
)
async def start_voice(conversation_id: str, request: Request, service: Service) -> VoiceSessionOut:
    """Move a conversation to voice and return the ElevenLabs session settings.

    Works for a new voice conversation (Ana opens with her greeting) and for a chat switched to
    voice (she opens with the pending question). 409 when nothing is left to answer.

    Under the conversation lock: a chat turn still in flight finishes first, so the opening
    line is the question it asked."""
    async with service.conversation_lock(conversation_id):
        conversation, line = await asyncio.to_thread(_to_voice, service, conversation_id)
    state = state_of(conversation)
    register = register_of(state.language, state.country)
    settings = voice_settings(request)
    voices = {
        "es-ES": settings.elevenlabs_voice_es,
        "es-MX": settings.elevenlabs_voice_mx,
        "en": settings.elevenlabs_voice_en,
    }
    agent_id = settings.elevenlabs_agent_id.strip() or None
    if agent_id and not settings.voice_llm_url.strip():
        # ElevenLabs sends the call's turns to the URL the agent was last set up with; when
        # that is another API (the deployed one, for a local page without a tunnel), every
        # turn there is "no encuentro tu solicitud".
        logger.warning(
            "VOICE_LLM_URL is unset: the call's turns go to the custom LLM URL agent %s "
            "already has, maybe not this API (deploy/README.md, Voice, Test locally)",
            agent_id,
        )
    token = await asyncio.to_thread(conversation_token, settings) if agent_id else None
    return VoiceSessionOut(
        conversation_id=conversation.id,
        agent_id=agent_id,
        conversation_token=token,
        language=state.language.value,
        voice_id=voices[register].strip() or None,
        opening_line=line,
        dynamic_variables={CONVERSATION_VAR: conversation.id, OPENING_VAR: line},
        custom_llm_extra_body={CONVERSATION_VAR: conversation.id, "language": state.language.value},
    )


def conversation_token(settings: Settings) -> str | None:
    """A WebRTC conversation token for a private agent, or None (public agent, or no key)."""
    key = settings.elevenlabs_api_key.get_secret_value().strip()
    if not key:
        return None
    query = urllib.parse.urlencode({"agent_id": settings.elevenlabs_agent_id.strip()})
    url = f"{settings.elevenlabs_api_url.rstrip('/')}/v1/convai/conversation/token?{query}"
    request = urllib.request.Request(url, headers={"xi-api-key": key})
    try:
        with urllib.request.urlopen(request, timeout=TOKEN_TIMEOUT_S) as response:
            return json.loads(response.read()).get("token")
    except Exception as exc:
        logger.warning("ElevenLabs conversation token failed: %s", type(exc).__name__)
        return None
