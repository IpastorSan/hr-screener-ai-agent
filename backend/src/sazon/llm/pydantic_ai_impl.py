"""The LLM layer on Pydantic AI, routed through OpenRouter with zero data retention."""

import asyncio
import json
import logging
import re
import time
from collections.abc import Sequence
from datetime import date

import httpx2
from openai import AsyncOpenAI
from opentelemetry import trace
from pydantic import BaseModel, Field
from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.models.openrouter import (
    OpenRouterModel,
    OpenRouterModelSettings,
    OpenRouterProviderConfig,
    OpenRouterReasoning,
)
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.settings import ModelSettings

from sazon import metrics
from sazon.config import Settings, get_settings
from sazon.engine.actions import Action, AnswerQuestion, WelcomeBack
from sazon.engine.models import CandidateProfile, Language, QuestionTopic, Stage
from sazon.engine.spec import agent
from sazon.engine.state import ConversationState
from sazon.engine.validators import Country
from sazon.llm.extraction import output_model, to_turn
from sazon.llm.fallback_templates import (
    COUNTRY_LABELS,
    UNKNOWN_REST,
    answer_lead,
    fallback_extraction,
    fallback_reply,
    fallback_summary,
    is_fixed,
    is_unknown_answer,
    key_fields,
    profile_flags,
    register_of,
    spoken_action,
    unanswered_parts,
    welcome_back,
)
from sazon.llm.interface import (
    FIXED_REPLY_MODEL,
    CallRecord,
    CandidateSummary,
    Channel,
    ChatTurn,
    Emit,
    ExtractResult,
    Reply,
    ReplyContext,
    ReplyResult,
    Role,
    SummaryResult,
)
from sazon.llm.prompting import prompt
from sazon.llm.reply_checks import SENTENCE_END, names_country, reply_problem, strip_symbols

logger = logging.getLogger(__name__)

DEFAULT_MODEL: str = Settings.model_fields["llm_responder"].default
OPENROUTER_PREFIX = "openrouter:"
PROVIDER_PREFERENCES: OpenRouterProviderConfig = {"zdr": True, "data_collection": "deny"}
HISTORY_TURNS = 6
# The responder's style rule is 300 characters; the cap only stops runaway output.
MAX_TOKENS = {Role.EXTRACTOR: 800, Role.RESPONDER: 400, Role.SUMMARIZER: 500}
# Output tokens added for a model run with reasoning on, which counts against max_tokens.
REASONING_TOKENS = 2000
EFFORTS = ("none", "minimal", "low", "medium", "high")
RESPOND_ATTEMPTS = 2
DELIMITERS = (
    "<candidate_message>",
    "</candidate_message>",
    "<history>",
    "</history>",
    "<transcript>",
    "</transcript>",
)


class AnswerDraft(BaseModel):
    """An FAQ answer: whether the snippets answered the question, and the message."""

    answered: bool = Field(
        description="True only if the FAQ snippets answer what the candidate asked."
    )
    text: str = Field(description="The message the candidate will read.")
    answered_parts: list[int] | None = Field(
        default=None,
        description="When the snippets come in parts: the numbers of the parts the text answers.",
    )


class SummaryDraft(BaseModel):
    lines: list[str] = Field(max_length=3, description="Up to 3 short summary lines.")
    flags: list[str] = Field(default_factory=list, description="Notes the profile does not show.")


def parse_model(spec: str) -> tuple[str, str | None]:
    """`[openrouter:]vendor/model[@effort]` to the OpenRouter slug and reasoning effort.

    Empty means the default model. Without an effort (or with `@none`) reasoning is off.
    """
    spec = spec.strip() or DEFAULT_MODEL
    slug, _, effort = spec.removeprefix(OPENROUTER_PREFIX).partition("@")
    vendor = slug.split("/", 1)[0]
    if "/" not in slug or ":" in vendor:
        raise ValueError(f"Expected an OpenRouter model such as {DEFAULT_MODEL!r}, got {spec!r}")
    if effort and effort not in EFFORTS:
        raise ValueError(f"Unknown reasoning effort {effort!r} in {spec!r}; use one of {EFFORTS}")
    return slug, None if effort in ("", "none") else effort


def model_slug(spec: str) -> str:
    return parse_model(spec)[0]


def join_prompts(*parts: str) -> str:
    """Instructions made of several prompts ("respond.md" plus the voice rules)."""
    return "\n\n".join(part.strip() for part in parts)


def quote(text: str) -> str:
    """Candidate text as a JSON string literal, with our delimiters removed."""
    for delimiter in DELIMITERS:
        text = text.replace(delimiter, "")
    return json.dumps(text, ensure_ascii=False)


def openrouter_provider(
    settings: Settings | None = None,
    *,
    http_client: httpx2.AsyncClient | None = None,
    max_retries: int = 1,
) -> OpenRouterProvider:
    """The one OpenRouter client every model role shares."""
    settings = settings or get_settings()
    client = AsyncOpenAI(
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
        http_client=http_client,
        max_retries=max_retries,
    )
    return OpenRouterProvider(openai_client=client)


class CostReportingModel(WrapperModel):
    """Puts OpenRouter's reported cost on the model request span (`gen_ai.usage.cost`), where
    Langfuse reads it: OpenRouter slugs are not in Langfuse's price list."""

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        response = await super().request(messages, model_settings, model_request_parameters)
        cost = (response.provider_details or {}).get("cost")
        if cost is not None:
            trace.get_current_span().set_attribute("gen_ai.usage.cost", float(cost))
        return response


def openrouter_model(
    slug: str, provider: OpenRouterProvider, *, max_tokens: int, effort: str | None = None
) -> Model:
    """A model on OpenRouter with zero data retention, no data collection and usage (cost)
    reported on every response. `effort` turns reasoning on (it is never returned)."""
    settings = OpenRouterModelSettings(
        max_tokens=max_tokens,
        openrouter_provider=PROVIDER_PREFERENCES,
        openrouter_usage={"include": True},
    )
    if effort:
        # Reasoning tokens count against max_tokens.
        settings["max_tokens"] = max_tokens + REASONING_TOKENS
        settings["openrouter_reasoning"] = OpenRouterReasoning(
            effort=effort,  # pyright: ignore[reportArgumentType]
            exclude=True,
        )
    return CostReportingModel(OpenRouterModel(slug, provider=provider, settings=settings))


class PydanticAILLM:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx2.AsyncClient | None = None,
        max_retries: int = 1,
    ) -> None:
        self.settings = settings or get_settings()
        self.provider = openrouter_provider(
            self.settings, http_client=http_client, max_retries=max_retries
        )
        specs = {
            Role.EXTRACTOR: parse_model(self.settings.llm_extractor),
            Role.RESPONDER: parse_model(self.settings.llm_responder),
            Role.SUMMARIZER: parse_model(self.settings.llm_summarizer),
        }
        self.models = {role: slug for role, (slug, _) in specs.items()}
        for role, slug in self.models.items():
            metrics.declare_model(role.value, slug)
        self.efforts = {role: effort for role, (_, effort) in specs.items()}

        def model(role: Role) -> Model:
            return openrouter_model(
                self.models[role],
                self.provider,
                max_tokens=MAX_TOKENS[role],
                effort=self.efforts[role],
            )

        def run_settings(role: Role) -> ModelSettings:
            if self.efforts[role]:
                return {}
            # Thinking spends the output budget and leaks into the reply ("Wait, the context...").
            if role == Role.RESPONDER:
                return {"thinking": False}
            return {"thinking": False, "temperature": 0}

        # Prompted JSON rather than a tool call: through OpenRouter, tool calls came back with the
        # open `fields` dict empty (GPT-6 Luna on every case, GLM-5.3 Flash and DeepSeek V4.1
        # Flash on 10-26%) and native JSON schema dropped it on Claude (SAZ-24 benchmark).
        self.extractor = Agent(
            model(Role.EXTRACTOR),
            output_type=PromptedOutput(output_model(agent())),
            instructions=prompt("extract.md"),
            model_settings=run_settings(Role.EXTRACTOR),
        )
        respond = prompt("respond.md")
        instructions = {
            Channel.WEB: respond,
            Channel.VOICE: join_prompts(respond, prompt("respond_voice.md")),
        }
        answer = prompt("respond_answer.md")
        responder = model(Role.RESPONDER)
        self.responders = {
            channel: Agent(
                responder,
                output_type=str,
                instructions=text,
                model_settings=run_settings(Role.RESPONDER),
            )
            for channel, text in instructions.items()
        }
        self.answerers = {
            channel: Agent(
                responder,
                output_type=PromptedOutput(AnswerDraft),
                instructions=join_prompts(text, answer),
                model_settings=run_settings(Role.RESPONDER),
            )
            for channel, text in instructions.items()
        }
        self.summarizer = Agent(
            model(Role.SUMMARIZER),
            output_type=SummaryDraft,
            instructions=prompt("summarize.md"),
            model_settings=run_settings(Role.SUMMARIZER),
        )

    async def extract(
        self,
        history: Sequence[ChatTurn],
        state: ConversationState,
        message: str,
        *,
        today: date | None = None,
    ) -> ExtractResult:
        prompt = extract_prompt(history, state, message, today or date.today())
        output, call = await self._run(
            Role.EXTRACTOR, self.extractor, prompt, self.settings.llm_extract_timeout_s
        )
        extraction = fallback_extraction(state.language) if output is None else to_turn(output)
        return ExtractResult(extraction=extraction, call=call)

    async def respond(self, action: Action, ctx: ReplyContext) -> ReplyResult:
        """Phrase `action`. A reply that fails the reply checks is retried once with the
        problem named, then the template is sent.

        An FAQ answer also reports whether the snippets answered the question (`answered`). Its
        template sends the top snippet only when `ctx.faq_send_as_written`; otherwise the
        unknown answer, with `answered` False. A question with no snippets gets the unknown
        answer and the pending question as written, with no model."""
        if isinstance(action, AnswerQuestion) and action.topic == QuestionTopic.AI_IDENTITY:
            return await self._confirm_ai(action, ctx)
        if isinstance(action, WelcomeBack):
            return await self._welcome_back(action, ctx)
        if is_fixed(action) or is_unknown_answer(action, ctx):
            call = CallRecord(role=Role.RESPONDER, model=FIXED_REPLY_MODEL)
            return ReplyResult(
                reply=Reply(text=fallback_reply(action, ctx), ui=action.ui), call=call
            )
        reference = fallback_reply(action, ctx)
        prompt = respond_prompt(action, ctx, reference)
        faq = isinstance(action, AnswerQuestion) and bool(ctx.faq_snippets)
        # Checked as one question would be: the line for the parts Ana can't answer is not
        # a reason to allow a longer reply; the answer covers fewer parts instead.
        checked_against = fallback_reply(action, ctx.model_copy(update={"faq_parts": ()}))
        calls: list[CallRecord] = []
        text = ""
        answered: bool | None = None
        missed: tuple[str, ...] = ()
        problem: str | None = None
        for _ in range(RESPOND_ATTEMPTS):
            attempt = prompt if problem is None else retry_prompt(prompt, problem)
            if faq:
                draft, call = await self._run(
                    Role.RESPONDER,
                    self.answerers[ctx.channel],
                    attempt,
                    self.settings.llm_respond_timeout_s,
                )
                output = draft.text if draft is not None else None
                answered = draft.answered if draft is not None else None
                if draft is not None and ctx.faq_parts and draft.answered_parts == []:
                    answered = False
                if answered is False:
                    # Near-topic passages that answer nothing: the fixed unknown line and the
                    # question, as with no passage at all, never the model's own wording of it.
                    calls.append(call)
                    text = fallback_reply(action, _without_faq(ctx))
                    break
                if draft is not None and output is not None:
                    missed = _missed_parts(ctx, draft.answered_parts)
                    if missed or unanswered_parts(ctx):
                        rest = UNKNOWN_REST[register_of(ctx.language, ctx.country)]
                        output = with_line_before_question(output, rest)
            else:
                output, call = await self._run(
                    Role.RESPONDER,
                    self.responders[ctx.channel],
                    attempt,
                    self.settings.llm_respond_timeout_s,
                )
            calls.append(call)
            if output is None:
                break
            output = strip_symbols(output)
            problem = reply_problem(
                output,
                ctx.language,
                country=ctx.country,
                action=action,
                reference=checked_against,
                voice=ctx.channel == Channel.VOICE,
                quotes=ctx.faq_snippets,
            )
            if (
                problem is None
                and faq
                and ctx.faq_country
                and not names_country(output, ctx.faq_country)
            ):
                problem = (
                    f"the snippets are about {COUNTRY_LABELS[ctx.faq_country]}, not the "
                    "candidate's country: say which country the answer is about"
                )
            if problem is None:
                text = output
                break
            logger.warning("Responder reply discarded: %s", problem)
        call = merge_calls(calls)
        if not text:
            text = reference
            error = call.error or f"reply discarded: {problem or ''}"
            call = call.model_copy(update={"fallback_used": True, "error": error})
            answered = None
            missed = ()
            if faq and not ctx.faq_send_as_written:
                # A near-topic passage sent as written would answer another question.
                text = fallback_reply(action, _without_faq(ctx))
                answered = False
        return ReplyResult(
            reply=Reply(text=text, ui=action.ui), call=call, answered=answered, missed_parts=missed
        )

    async def respond_stream(self, action: Action, ctx: ReplyContext, emit: Emit) -> ReplyResult:
        """`respond`, handing each sentence to `emit` as soon as it is written and checked.

        For voice, where the first words matter more than the last. A sentence that fails the
        reply checks stops the stream: with nothing said yet the reference text is said
        instead; after a partial reply, the reference's question is added when none was asked.
        """
        answers = isinstance(action, AnswerQuestion) and bool(ctx.faq_snippets or action.topic)
        whole = answers or isinstance(action, WelcomeBack)
        if whole or is_fixed(action) or is_unknown_answer(action, ctx):
            # Fixed answers and FAQ answers (which report whether they answered) come whole.
            result = await self.respond(action, ctx)
            emit(result.reply.text)
            return result
        reference = fallback_reply(action, ctx)
        prompt = respond_prompt(action, ctx, reference)
        agent = self.responders[ctx.channel]
        model = self.models[Role.RESPONDER]
        started = time.perf_counter()
        spoken: list[str] = []
        problem: str | None = None
        call = CallRecord(role=Role.RESPONDER, model=model)
        try:
            async with asyncio.timeout(self.settings.llm_respond_timeout_s):
                async with agent.run_stream(prompt) as run:
                    pending = ""
                    async for delta in run.stream_text(delta=True, debounce_by=None):
                        pending += delta
                        sentences, pending = split_sentences(pending)
                        problem = _say(sentences, spoken, emit, ctx, action, reference)
                        if problem:
                            break
                    if not problem:
                        problem = _say([pending], spoken, emit, ctx, action, reference)
                    usage = run.usage
                    details = run.response.provider_details or {}
                    cost = details.get("cost")
                    call = call.model_copy(
                        update={
                            "model": run.response.model_name or model,
                            "provider": details.get("downstream_provider"),
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,
                            "cost_usd": float(cost) if cost is not None else None,
                        }
                    )
        except Exception as exc:
            problem = f"{type(exc).__name__}: {exc}"[:300]
            logger.warning("LLM %s stream from %s failed: %s", Role.RESPONDER, model, problem)
            metrics.count_openrouter_error(exc)
        call = call.model_copy(update={"latency_ms": _elapsed_ms(started)})
        if not spoken:
            emit(reference)
            error = problem or "reply discarded: empty"
            call = call.model_copy(update={"fallback_used": True, "error": error})
            return ReplyResult(reply=Reply(text=reference, ui=action.ui), call=call)
        if problem:
            logger.warning("Responder stream cut short: %s", problem)
            question = SENTENCE_END.split(reference)[-1]
            if not any("?" in sentence for sentence in spoken) and question.endswith("?"):
                emit(question)
                spoken.append(question)
            call = call.model_copy(update={"error": "reply cut short: " + problem})
        return ReplyResult(reply=Reply(text=" ".join(spoken), ui=action.ui), call=call)

    async def _welcome_back(self, action: WelcomeBack, ctx: ReplyContext) -> ReplyResult:
        """The fixed welcome back, then the follow-up as the model phrases it. The whole message
        is checked as one reply, the welcome as its lead (QA batch 3): one that fails is sent as
        the template, welcome and follow-up both."""
        lead = welcome_back(action.language, action.country, action.name)
        # Without the candidate's message: the yes to the match question needs no answer.
        follow_up = await self.respond(
            action.follow_up, ctx.model_copy(update={"last_message": None})
        )
        text = f"{lead} {follow_up.reply.text}"
        reference = fallback_reply(action, ctx)
        call = follow_up.call
        problem = reply_problem(
            text,
            ctx.language,
            country=ctx.country,
            action=action,
            reference=reference,
            voice=ctx.channel == Channel.VOICE,
        )
        if problem is not None:
            logger.warning("Welcome back discarded: %s", problem)
            text = reference
            error = f"reply discarded: {problem}"
            call = call.model_copy(update={"fallback_used": True, "error": error})
        return ReplyResult(reply=Reply(text=text, ui=action.ui), call=call)

    async def _confirm_ai(self, action: AnswerQuestion, ctx: ReplyContext) -> ReplyResult:
        """ "Are you an AI?" gets a fixed yes; only the pending question is phrased by the model."""
        lead = answer_lead(action, register_of(ctx.language, ctx.country))
        if action.follow_up is None:
            call = CallRecord(role=Role.RESPONDER, model=FIXED_REPLY_MODEL)
            return ReplyResult(reply=Reply(text=lead, ui=action.ui), call=call)
        # Without the candidate's message, so the model can't answer the question a second time.
        follow_up = await self.respond(
            action.follow_up, ctx.model_copy(update={"last_message": None})
        )
        text = f"{lead} {follow_up.reply.text}"
        return ReplyResult(reply=Reply(text=text, ui=action.ui), call=follow_up.call)

    async def summarize(
        self,
        transcript: Sequence[ChatTurn],
        profile: CandidateProfile,
        *,
        language: Language = Language.ES,
        country: Country = "ES",
        today: date | None = None,
    ) -> SummaryResult:
        prompt = summarize_prompt(transcript, profile, language, country, today or date.today())
        output, call = await self._run(
            Role.SUMMARIZER, self.summarizer, prompt, self.settings.llm_summarize_timeout_s
        )
        if output is None:
            summary = fallback_summary(profile, language, country)
            return SummaryResult(summary=summary, call=call)
        summary = CandidateSummary(
            lines=tuple(line.strip() for line in output.lines[:3] if line.strip()),
            key_fields=key_fields(profile, language, country),
            flags=(*profile_flags(profile), *output.flags),
        )
        return SummaryResult(summary=summary, call=call)

    async def _run[T](
        self, role: Role, agent: Agent[None, T], prompt: str, limit_s: float
    ) -> tuple[T | None, CallRecord]:
        model = self.models[role]
        started = time.perf_counter()
        try:
            async with asyncio.timeout(limit_s):
                result = await agent.run(prompt)
        except Exception as exc:
            # Any failure falls back, as the `LLM` protocol promises: HTTP errors, timeouts and
            # also output that fails validation, which the OpenRouter counter leaves out.
            error = f"{type(exc).__name__}: {exc}"[:300]
            logger.warning("LLM %s call to %s failed: %s", role, model, error)
            metrics.count_openrouter_error(exc)
            call = CallRecord(
                role=role,
                model=model,
                latency_ms=_elapsed_ms(started),
                fallback_used=True,
                error=error,
            )
            return None, call
        response = result.response
        details = response.provider_details or {}
        usage = result.usage
        cost = details.get("cost")
        call = CallRecord(
            role=role,
            model=response.model_name or model,
            provider=details.get("downstream_provider"),
            latency_ms=_elapsed_ms(started),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=float(cost) if cost is not None else None,
        )
        return result.output, call


def extract_prompt(
    history: Sequence[ChatTurn], state: ConversationState, message: str, today: date
) -> str:
    context = {
        "today": f"{today.isoformat()} ({today.strftime('%A')})",
        "stage": state.stage,
        "awaiting_field": state.awaiting,
        "country": state.country,
        "conversation_language": state.language.value,
    }
    pending = state.pending_confirmation
    if pending is not None and agent().field(pending.field).voice_read_back:
        # The voice read-backs: a yes agrees with what Ana just checked.
        context["read_back"] = pending.field
    if state.offered_slots:
        context["offered_slots"] = [
            {"id": slot.id, "label": slot.label} for slot in state.offered_slots
        ]
    if state.booking is not None:
        context["booked_interview"] = state.booking.label
    choosing = state.stage in (Stage.MODE, Stage.SCHEDULING) or state.booking is not None
    if len(state.available_modes) > 1 and choosing:
        context["interview_modes"] = [mode.value for mode in state.available_modes]
        mode = state.profile.interview_mode
        context["interview_mode"] = mode.value if mode else None
    turns = "\n".join(f"{turn.role}: {quote(turn.text)}" for turn in history[-HISTORY_TURNS:])
    return (
        f"<context>\n{json.dumps(context, ensure_ascii=False, indent=1)}\n</context>\n"
        f"<history>\n{turns}\n</history>\n"
        f"<candidate_message>{quote(message)}</candidate_message>"
    )


def respond_prompt(action: Action, ctx: ReplyContext, reference: str) -> str:
    context = {
        "language": ctx.language.value,
        "country": ctx.country,
        "channel": ctx.channel.value,
        "simplify": ctx.simplify,
        "persona": ctx.persona,
        "today": ctx.today.isoformat(),
        "candidate_first_name": ctx.candidate_name.split()[0] if ctx.candidate_name else None,
    }
    if ctx.faq_country:
        context["faq_country"] = ctx.faq_country
    exclude = {"ui": True, "follow_up": {"ui": True}}
    if ctx.channel == Channel.VOICE:
        # Interview hours only in words, as they are to be said; the day of the month stays in
        # digits (SAZ-48).
        spoken = spoken_action(action, register_of(ctx.language, ctx.country))
        data = _without_starts(spoken.model_dump(mode="json", exclude=exclude))
        action_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        action_json = action.model_dump_json(exclude=exclude)
    parts = [
        f"<context>\n{json.dumps(context, ensure_ascii=False, indent=1)}\n</context>",
        f"<action>\n{action_json}\n</action>",
    ]
    if ctx.faq_parts:
        parts.append(f"<faq>\n{_faq_by_part(ctx)}\n</faq>")
    elif ctx.faq_snippets:
        snippets = "\n".join(f"- {snippet}" for snippet in ctx.faq_snippets)
        parts.append(f"<faq>\n{snippets}\n</faq>")
    if ctx.last_message:
        parts.append(f"<candidate_message>{quote(ctx.last_message)}</candidate_message>")
    parts.append(f"<reference>\n{reference}\n</reference>")
    return "\n".join(parts)


NOTHING_FOUND = "- (nothing found)"
OWN_UNKNOWN = re.compile(
    r"\b(?:no (?:tengo|tenemos|dispongo de|cuento con)|(?:don't|do not|i have no))\b"
    r"[^.?!]*\b(?:informaci[oó]n|information|datos|details)\b",
    re.IGNORECASE,
)
"""A sentence saying there is no information on something, in the responder's own words."""


def _faq_by_part(ctx: ReplyContext) -> str:
    """The snippets under each part of the question, numbered from 1."""
    lines: list[str] = []
    for number, part in enumerate(ctx.faq_parts, 1):
        lines.append(f"Part {number}: {part.question}")
        lines.extend([f"- {snippet}" for snippet in part.snippets] or [NOTHING_FOUND])
    return "\n".join(lines)


def _without_faq(ctx: ReplyContext) -> ReplyContext:
    """`ctx` as if retrieval found nothing: its template is the unknown answer."""
    return ctx.model_copy(update={"faq_snippets": (), "faq_parts": ()})


def _missed_parts(ctx: ReplyContext, answered: Sequence[int] | None) -> tuple[str, ...]:
    """The parts with snippets the answer leaves unanswered, by the answerer's own count of the
    parts it answered (none when it gave no count)."""
    if answered is None:
        return ()
    return tuple(
        part.question
        for number, part in enumerate(ctx.faq_parts, 1)
        if number not in answered and part.snippets
    )


def with_line_before_question(text: str, line: str) -> str:
    """`line` before the reply's closing question, or at its end when it asks none. A sentence
    of the reply's own saying it has no information goes: the fixed line says it."""
    if line in text:
        return text
    sentences = [
        sentence
        for sentence in SENTENCE_END.split(text.strip())
        if sentence.rstrip('" ').endswith("?") or not OWN_UNKNOWN.search(sentence)
    ]
    if sentences and sentences[-1].rstrip('" ').endswith("?"):
        return " ".join([*sentences[:-1], line, sentences[-1]])
    return " ".join([*sentences, line])


def _without_starts(data: object) -> object:
    """The dumped action without the slots' and booking's `startsAt` ("2026-10-22T09:00"):
    by voice the model gets the day and time only as the words to say."""
    if isinstance(data, dict):
        return {key: _without_starts(value) for key, value in data.items() if key != "startsAt"}
    if isinstance(data, list):
        return [_without_starts(value) for value in data]
    return data


def retry_prompt(prompt: str, problem: str) -> str:
    """The same request, naming what was wrong with the previous draft."""
    return (
        f"{prompt}\n<previous_draft_rejected>\nYour previous draft was rejected: {problem}. "
        "Write the message again following every rule.\n</previous_draft_rejected>"
    )


def summarize_prompt(
    transcript: Sequence[ChatTurn],
    profile: CandidateProfile,
    language: Language,
    country: Country = "ES",
    today: date | None = None,
) -> str:
    context: dict[str, str] = {"summary_language": language.value, "country": country}
    if today is not None:
        context["today"] = _with_weekday(today)
    if profile.start_date is not None:
        context["start_date"] = _with_weekday(profile.start_date)
    turns = "\n".join(f"{turn.role}: {quote(turn.text)}" for turn in transcript)
    return (
        f"<context>\n{json.dumps(context, ensure_ascii=False, indent=1)}\n</context>\n"
        f"<profile>\n{profile.model_dump_json(exclude_none=True)}\n</profile>\n"
        f"<transcript>\n{turns}\n</transcript>"
    )


def _with_weekday(day: date) -> str:
    return f"{day.isoformat()} ({day.strftime('%A')})"


def split_sentences(text: str) -> tuple[list[str], str]:
    """Complete sentences, and the unfinished rest still being written."""
    parts = SENTENCE_END.split(text)
    return [part for part in parts[:-1] if part.strip()], parts[-1]


def _say(
    sentences: Sequence[str],
    spoken: list[str],
    emit: Emit,
    ctx: ReplyContext,
    action: Action,
    reference: str,
) -> str | None:
    """Emit each sentence that keeps the reply valid; the problem with the first that doesn't."""
    for sentence in sentences:
        text = strip_symbols(sentence)
        if not text:
            continue
        problem = reply_problem(
            " ".join([*spoken, text]),
            ctx.language,
            country=ctx.country,
            action=action,
            reference=reference,
            partial=True,
            voice=ctx.channel == Channel.VOICE,
        )
        if problem:
            return problem
        emit(text)
        spoken.append(text)
    return None


def merge_calls(calls: Sequence[CallRecord]) -> CallRecord:
    """One record for a reply that took several attempts: the last call, summed usage."""
    if len(calls) == 1:
        return calls[0]
    costs = [call.cost_usd for call in calls if call.cost_usd is not None]
    return calls[-1].model_copy(
        update={
            "latency_ms": sum(call.latency_ms for call in calls),
            "input_tokens": _total([call.input_tokens for call in calls]),
            "output_tokens": _total([call.output_tokens for call in calls]),
            "cost_usd": sum(costs) if costs else None,
        }
    )


def _total(values: Sequence[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
