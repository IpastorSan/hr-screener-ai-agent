"""One screening turn for any channel: sanitize, extract, step, fill, respond, persist."""

import asyncio
import logging
import re
import traceback
import weakref
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, TypeAdapter

from sazon import metrics, telemetry
from sazon.engine import (
    Action,
    AnswerQuestion,
    ApplicationStatus,
    BookSlot,
    CheckDuplicate,
    ConversationContext,
    ConversationState,
    DuplicateChecked,
    End,
    EndReason,
    FindModes,
    FindSlots,
    LookupResult,
    ModesFound,
    OfferSlots,
    ResumeExisting,
    SlotBooked,
    SlotsFound,
    abandon,
    awaits_reoffer,
    awaits_talent_pool_answer,
    current_prompt,
    on_lookup,
    re_engage,
    reoffer,
    reopen,
    restart,
    resume,
)
from sazon.engine import start as engine_start
from sazon.engine import step as engine_step
from sazon.engine.actions import (
    ApplicationSaved,
    AskField,
    AskMode,
    Clarify,
    ConfirmIdentity,
    ConfirmSummary,
    ConfirmValue,
    DeEscalate,
    Deflect,
    DetailsUpdated,
    Greet,
    WelcomeBack,
)
from sazon.engine.language import value_only
from sazon.engine.models import (
    FieldCandidate,
    FieldId,
    Intent,
    InterviewMode,
    Language,
    MatrixLanguage,
    Outcome,
    PhoneInput,
    QualityFlag,
    Sentiment,
    Stage,
    TurnExtraction,
)
from sazon.engine.modes import read_mode, resolve_mode
from sazon.engine.service_areas import get_location, match_city
from sazon.engine.slots import CHANGE, NONE_WORK, Booked, TimeRequest
from sazon.engine.spec import YES_NO_TYPES, FieldType, agent
from sazon.engine.spec import Channel as ContactChannel
from sazon.engine.state import (
    Closed,
    CountrySource,
    DeclinedConsent,
    DuplicateMatch,
    InterviewFix,
    add_quality_flag,
)
from sazon.engine.text import (
    asks_to_cancel,
    asks_to_repeat,
    asks_why,
    field_to_fix,
    gives_up,
    mask_id_numbers,
    named_place,
    names_day_and_month,
    opens_with_yes,
    refuses_value,
    sanitize_input,
)
from sazon.engine.turn import MAX_ABUSE_STRIKES
from sazon.engine.ui import (
    CANCEL,
    CANCEL_OPTIONS,
    CONFIRM_ANSWERS,
    DIAL_COUNTRIES,
    RESCHEDULE,
    YES_NO,
    Ui,
    mode_label,
    option_label,
)
from sazon.engine.validators import (
    LICENSE_IN_PROGRESS,
    LICENSE_NOT_VALID,
    Country,
    ReasonCode,
    Valid,
    correct_email_domain,
    license_affirmed,
    license_reading,
    license_unsure,
    reads_as_license_answer,
    validate_location,
    years_need_words,
)
from sazon.llm.fallback_templates import (
    RETRY,
    fallback_reply,
    overview,
    register_of,
    welcome_back_with_times,
)
from sazon.llm.interface import (
    LLM,
    CallRecord,
    Channel,
    ChatTurn,
    Emit,
    FaqPart,
    ReplyContext,
    ReplyResult,
    StreamingResponder,
)
from sazon.observability import record
from sazon.pii import redact
from sazon.rag.index import (
    Part,
    Retriever,
    Snippet,
    citations,
    default_index,
    merge_parts,
    retrieve_parts,
    send_as_written,
)
from sazon.reengagement.idle import ABANDON_AFTER, MAX_NUDGES, abandoned_since, is_stale
from sazon.scheduling import service as scheduling
from sazon.scheduling.resolve import read_choice
from sazon.sentiment import SentimentStats
from sazon.storage.models import (
    RESTARTED,
    Candidate,
    CandidateStatus,
    Conversation,
    FaqMiss,
    Summary,
    Turn,
    TurnRole,
)
from sazon.storage.models import (
    InterviewMode as StoredMode,
)
from sazon.storage.repo import UNIT_OPEN, Repo, Unit, state_of, to_utc

logger = logging.getLogger(__name__)

type Clock = Callable[[], datetime]

DEFAULT_TIMEZONE = "Europe/Madrid"
MAX_VOICE_PARTS = 2
"""By voice, a question that asks several things is answered for its first two parts at most:
a spoken turn stays short."""
SUMMARY_LANGUAGE = Language.ES
SCHEDULING_ACTIONS = frozenset({"ask_mode", "offer_slots", "confirm_booking", "application_saved"})
TYPED_EMAIL = re.compile(r"[^\s@,;<>()]+@[^\s@,;<>()]+")
# What a typed yes or no to the license question may come back as when the extractor reports
# no license answer in it.
CREDENTIAL_ANSWER_INTENTS = frozenset(
    {Intent.ANSWER, Intent.CONFIRM, Intent.UNCLEAR, Intent.CORRECTION}
)
TERMINAL = frozenset(
    {Outcome.QUALIFIED, Outcome.DISQUALIFIED, Outcome.ABANDONED, Outcome.WITHDRAWN}
)
STATUS_OF = {
    Outcome.QUALIFIED: CandidateStatus.QUALIFIED,
    Outcome.DISQUALIFIED: CandidateStatus.DISQUALIFIED,
    Outcome.ABANDONED: CandidateStatus.ABANDONED,
    Outcome.WITHDRAWN: CandidateStatus.WITHDRAWN,
}
# An abandonment that ended on one of these was the candidate's choice: the chat link stays closed.
FINAL_END_REASONS = frozenset({EndReason.OPT_OUT, EndReason.ABUSE, EndReason.DECLINED_CONSENT})
# English only: it answers a turn on a conversation that cannot be loaded, which the API has
# already refused with a 404, so only an erasure racing the turn reaches it.
NOTHING_UNDERSTOOD = "Sorry, something went wrong. Could you say that again?"
# What may follow the process overview, said right after consent (SAZ-47): a question.
AFTER_OVERVIEW = (
    AskField,
    Clarify,
    ConfirmValue,
    ConfirmSummary,
    AnswerQuestion,
    Deflect,
    DeEscalate,
)
# The agent actions after which a call hangs up (SAZ-47), with or without a booking.
CALL_CLOSING_ACTIONS = frozenset({"end", "application_status"})
# What a resumed application can carry on with after the welcome back: its pending question,
# new interview times, or the line that it is saved when none is free.
WELCOMED = (
    Greet,
    AskField,
    Clarify,
    ConfirmValue,
    ConfirmSummary,
    AskMode,
    OfferSlots,
    ConfirmIdentity,
    ApplicationSaved,
)

MIN_NAME_PART = 3
UI_ADAPTER: TypeAdapter[Ui] = TypeAdapter(Ui)  # pyright: ignore[reportArgumentType]


def utc_now() -> datetime:
    return datetime.now(UTC)


class WidgetAnswer(BaseModel):
    """A tap on a chat widget: an option value, several values, or the phone entry.

    `kind` is the widget tapped, when known; it picks the words stored for the candidate.
    """

    model_config = ConfigDict(frozen=True)

    value: str | list[str] | PhoneInput
    kind: str | None = None


class Reply(BaseModel):
    """What a channel shows. `conversation_id` changes when a returning candidate is resumed.

    `candidate_text` is the candidate's message as stored in the transcript, such as the label
    of a tapped option; None when nothing was stored.
    """

    model_config = ConfigDict(frozen=True)

    conversation_id: str
    text: str
    candidate_text: str | None = None
    ui: Ui | None = None
    ended: bool = False
    outcome: Outcome | None = None


@dataclass(frozen=True)
class _Message:
    """A candidate's message as read: the text stored and shown, and what was extracted."""

    text: str
    extraction: TurnExtraction
    call: CallRecord | None = None
    """The extraction's model call; None for a widget tap."""
    suspicious: bool = False
    fix: FieldId | InterviewFix | None = None
    widget: bool = False


@dataclass(frozen=True)
class _Repeat:
    """The message only asks Ana to say `said` again."""

    said: Turn
    text: str


@dataclass(frozen=True)
class _AgentTurn:
    """Ana's reply: `text` for the channel, `stored` for the transcript (None when the call
    hangs up without a word), with the state after it and the passages it cites."""

    state: ConversationState
    text: str
    stored: str | None
    call: CallRecord | None
    snippets: tuple[Snippet, ...]


class NudgeRejected(Exception):
    """The nudge can't be sent: the conversation ended or is paused, or the attempt is out of
    order."""


class ScreeningService:
    def __init__(
        self,
        llm: LLM,
        repo: Repo,
        clock: Clock = utc_now,
        faq: Retriever | None = None,
        scheduling_config: scheduling.SchedulingConfig | None = None,
    ) -> None:
        """`faq` defaults to the local index over the packaged FAQ; `scheduling_config` to the
        configured source (`SCHEDULING_SOURCE`)."""
        self.llm = llm
        self.repo = repo
        self.clock = clock
        self.faq = faq if faq is not None else default_index()
        self.scheduling = scheduling_config or _scheduling_from_settings(repo)
        # One turn at a time per conversation, so two messages can't overwrite each other's
        # state. In-process: enough for one API instance. On Postgres each turn's unit of work
        # also holds the conversation's advisory lock (`Unit.begin`), for several instances;
        # this lock stays in front of it, so turns that meet in one process queue in memory.
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._unit_slots: asyncio.Semaphore | None = None

    def conversation_lock(self, conversation_id: str) -> asyncio.Lock:
        lock = self._locks.get(conversation_id)
        if lock is None:
            lock = self._locks[conversation_id] = asyncio.Lock()
        return lock

    async def start(
        self, channel: Channel = Channel.WEB, context: ConversationContext | None = None
    ) -> Reply:
        try:
            return await self._start(channel, context or ConversationContext())
        except Exception as exc:
            _log_failure(exc, None, ())
            state, greet = engine_start(context)
            return Reply(
                conversation_id="", text=_template(greet, state, self.clock()), ui=greet.ui
            )

    async def handle_turn(
        self,
        conversation_id: str,
        message: str | WidgetAnswer,
        *,
        channel: Channel | None = None,
        emit: Emit | None = None,
    ) -> Reply:
        """Never raises: an unexpected error re-sends the pending question from templates.

        `channel` moves the conversation to that channel from this turn on (a candidate who
        switches between chat and voice). `emit` receives the reply as the responder writes it,
        before the turn is stored; the returned `text` is still the whole reply.

        Traced as one `candidate-turn`: sanitize, extract, engine.step, fill_action, respond,
        persist. One turn at a time per conversation (see `conversation_lock`), stored in one
        transaction at its end (see `_unit`): a turn that fails stores nothing."""
        async with self.conversation_lock(conversation_id):
            with telemetry.start_trace("candidate-turn", session_id=conversation_id) as trace:
                try:
                    reply = await self._handle_turn(conversation_id, message, trace, channel, emit)
                except Exception as exc:
                    telemetry.record_error(trace.span, type(exc).__name__)
                    reply = await self._failure_reply(conversation_id, exc)
                trace.tag(f"outcome:{reply.outcome.value}" if reply.outcome else "outcome:open")
                trace.io(output=reply.text)
                return reply

    async def nudge(self, conversation_id: str, attempt: int) -> Reply:
        """Re-engage a candidate who went quiet with the chat open: `attempt` 1, then 2.

        Idempotent per attempt within one silence: asking again returns the stored nudge.
        Raises `LookupError` for an unknown conversation and `NudgeRejected` when the chat has
        ended, is paused (the candidate took the pause offer and has not written since) or has
        no consent yet, the attempt is above `MAX_NUDGES` or the previous attempt was not
        sent. A conversation idle past the abandonment limit is closed here instead.
        """
        async with self.conversation_lock(conversation_id):
            with telemetry.start_trace("candidate-nudge", session_id=conversation_id) as trace:
                trace.tag(f"nudge:{attempt}")
                async with self._unit(conversation_id) as unit:
                    result = await self._nudge(unit, attempt, trace)
                if isinstance(result, NudgeRejected):
                    raise result  # after the abandonment is stored
                return result

    async def _nudge(
        self, unit: Unit, attempt: int, trace: telemetry.TraceHandle
    ) -> "Reply | NudgeRejected":
        """The nudge, or the rejection to raise once the unit is committed."""
        conversation = unit.root
        if conversation is None:
            raise LookupError("unknown conversation")
        trace.user(conversation.candidate_id)
        if not 1 <= attempt <= MAX_NUDGES:
            raise NudgeRejected(f"attempt must be between 1 and {MAX_NUDGES}")
        if await self._close_if_stale(unit, conversation):
            return NudgeRejected("the conversation has been abandoned")
        state = state_of(conversation)
        if is_ended(state) or current_prompt(state) is None:
            raise NudgeRejected("the conversation has ended")
        if state.paused:
            raise NudgeRejected("the candidate paused the conversation")
        if not state.consented:
            # No application exists to be "half done", and the notice is not to be hurried.
            raise NudgeRejected("no nudge before the privacy notice is accepted")
        sent = nudges_since_last_message(unit.turns(conversation.id))
        if attempt in sent:
            turn = sent[attempt]
            ui = turn.ui
            return Reply(
                conversation_id=conversation.id,
                text=turn.text or "",
                ui=UI_ADAPTER.validate_python(ui) if ui is not None else None,
                outcome=state.outcome,
            )
        if attempt != len(sent) + 1:
            raise NudgeRejected(f"nudge {attempt} before nudge {len(sent) + 1}")
        now = self.clock()
        state, action = re_engage(state, attempt)
        telemetry.add_known_pii(_pii(state))
        text, call = await self._respond(conversation, state, action, None, ())
        with telemetry.span("persist"):
            self._add_turn(
                unit,
                conversation.id,
                TurnRole.AGENT,
                now,
                text=text,
                action=action,
                call=call,
                nudge=True,
            )
            conversation.last_activity_at = to_utc(now)
            unit.save(conversation)
        metrics.NUDGES_SERVED.labels(str(attempt)).inc()
        trace.io(output=text)
        return Reply(
            conversation_id=conversation.id, text=text, ui=action.ui, outcome=state.outcome
        )

    async def close_if_stale(self, conversation_id: str) -> bool:
        """Write down the abandonment of a conversation idle past the limit, with its summary.

        It is dated when the limit passed, not when this runs, so it matches what readers saw.
        """
        async with self._unit(conversation_id) as unit:
            if unit.root is None:
                return False
            return await self._close_if_stale(unit, unit.root)

    async def _close_if_stale(self, unit: Unit, conversation: Conversation) -> bool:
        state = state_of(conversation)
        if not is_stale(conversation, state, self.clock()):
            return False
        moment = abandoned_since(conversation.last_activity_at)
        state = abandon(state)
        conversation.state = _dump(state)
        _count_outcome(conversation.outcome, state.outcome)
        conversation.outcome = state.outcome
        conversation.drop_off_stage = state.drop_off_stage
        conversation.ended_at = moment
        unit.save(conversation)
        candidate = unit.candidate(conversation.candidate_id)
        if candidate is not None and candidate.status == CandidateStatus.IN_PROGRESS:
            candidate.status = CandidateStatus.ABANDONED
            candidate.closed_at = moment
            unit.save(candidate)
        await self._summarize_if_done(unit, conversation, state, moment)
        return True

    async def close_stale(self) -> int:
        """Close every conversation idle past the limit. Returns how many were closed."""
        cutoff = to_utc(self.clock()) - ABANDON_AFTER
        closed = 0
        for conversation in await self._off_loop(self.repo.stale_candidates, cutoff):
            closed += await self.close_if_stale(conversation.id)
        return closed

    async def resume_if_idle(self, conversation_id: str) -> bool:
        """A candidate back on the chat link of an application abandoned for silence carries on
        where it stopped. Opt-outs and abuse stay closed. Waits for a turn in flight: both
        rewrite the state."""
        async with self.conversation_lock(conversation_id), self._unit(conversation_id) as unit:
            if unit.root is None:
                return False
            return self._resume_if_idle(unit, unit.root)

    def _resume_if_idle(self, unit: Unit, conversation: Conversation) -> bool:
        """`resume_if_idle` within a turn's unit."""
        state = state_of(conversation)
        if state.outcome != Outcome.ABANDONED or state.opted_out:
            return False
        if not abandoned_idle(unit.turns(conversation.id)):
            return False
        return self._reopen(unit, conversation, state, self.clock()).outcome is None

    async def restart(self, conversation_id: str) -> bool:
        """The restart button ("Empezar de nuevo") on this conversation: an application still
        open closes for good (QA batch 3), so a new chat with the same phone or email starts a
        fresh one instead of resuming it. Left out of the analytics: neither abandoned nor
        disqualified. A closed application (a gate, qualified) is left as it is, and so is the
        one a chat that handed over to it found (`duplicate_of`). True when one was closed."""
        async with self.conversation_lock(conversation_id), self._unit(conversation_id) as unit:
            conversation = unit.root
            if conversation is None:
                return False
            before = state_of(conversation)
            state = restart(before)
            if state == before:
                return False
            now = self.clock()
            end = End(reason=EndReason.RESTARTED, language=state.language, country=state.country)
            self._add_turn(unit, conversation.id, TurnRole.SYSTEM, now, action=end)
            conversation.state = _dump(state)
            conversation.outcome = state.outcome
            conversation.drop_off_stage = state.drop_off_stage
            conversation.ended_at = conversation.ended_at or to_utc(now)
            unit.save(conversation)
            candidate = unit.candidate(conversation.candidate_id)
            if candidate is not None:
                candidate.status = CandidateStatus.ABANDONED
                candidate.outcome_reason = RESTARTED
                candidate.closed_at = to_utc(now)
                candidate.updated_at = to_utc(now)
                unit.save(candidate)
            record("application_restarted")
            return True

    async def reoffer_if_pending(self, conversation_id: str) -> bool:
        """A candidate back on the chat after the recruiter's calendar cancelled their
        interview (SAZ-40) is offered new times. True when an offer was added."""
        async with self.conversation_lock(conversation_id), self._unit(conversation_id) as unit:
            conversation = unit.root
            if conversation is None or not awaits_reoffer(state_of(conversation)):
                return False
            now = self.clock()
            state, find = reoffer(state_of(conversation))
            state, action = await self._off_loop(
                self._schedule, unit, conversation, state, find, now
            )
            if isinstance(action, OfferSlots):
                action = action.model_copy(update={"reason": None})  # the notice said why
            text = _template(action, state, now, Channel(conversation.channel))
            self._add_turn(unit, conversation.id, TurnRole.AGENT, now, text=text, action=action)
            conversation.state = _dump(state)
            unit.save(conversation)
            return True

    @asynccontextmanager
    async def _unit(self, conversation_id: str | None = None) -> AsyncIterator[Unit]:
        """The unit of work of one turn (SAZ-84), off the event loop: its snapshot is read
        when the block starts, its writes are committed in one transaction when the block ends,
        and rolled back when it raises or is cancelled; its `after_commit` callbacks run once
        it is committed. `conversation_id` None opens one for a new conversation.

        The model calls run between the read and the write, with no transaction open on
        SQLite. A process that dies mid-turn stores nothing of it: the candidate's message is
        not in the transcript and gets no reply, so they send it again (docs/deployment.md,
        "One turn, one transaction")."""
        unit = self.repo.unit(conversation_id)
        async with self._unit_slot():
            token = UNIT_OPEN.set(True)
            try:
                await asyncio.to_thread(unit.begin)
                yield unit
                await asyncio.to_thread(unit.commit)
            finally:
                # Its own thread call even when cancelled: it waits for one still running.
                await asyncio.to_thread(unit.close)
                UNIT_OPEN.reset(token)
        for callback in unit.callbacks:
            await self._off_loop(callback)

    def _unit_slot(self) -> AbstractAsyncContextManager[object]:
        """A place among the units this process holds open at once (`Repo.max_units`), so
        they can't take every pooled connection and leave none for the bookings they wait on.
        Unlimited on SQLite."""
        limit = self.repo.max_units
        if limit is None:
            return nullcontext()
        if self._unit_slots is None:
            self._unit_slots = asyncio.Semaphore(limit)
        return self._unit_slots

    async def _off_loop[T](self, work: Callable[..., T], *args: Any) -> T:
        """Blocking database or calendar work, in a thread. On an in-memory database it takes
        turns with the units, which share its one connection (`Repo.serial`)."""
        return await asyncio.to_thread(self._serially, work, *args)

    def _serially[T](self, work: Callable[..., T], *args: Any) -> T:
        with self.repo.serial:
            return work(*args)

    async def fill_action(
        self,
        state: ConversationState,
        action: Action,
        unit: Unit | None = None,
        conversation_id: str | None = None,
    ) -> tuple[ConversationState, Action, tuple[Part, ...]]:
        """Fill an FAQ or document answer (SAZ-19, SAZ-29) with what retrieval finds. Interview
        modes and slots are filled in `_schedule`.

        Returns the state, the action with any data filled in, and what retrieval found: one
        part for one question, one per part for a question that asks several things (QA-BR),
        each looked up on its own. The snippets' text goes to the responder, their sources onto
        the agent turn for recruiters. A question nothing covers gets the unknown answer, a
        quality flag and a `FaqMiss` (saved in `unit`, given a conversation); so does each part
        nothing covers.
        """
        if isinstance(action, AnswerQuestion) and action.question and action.topic is None:
            # By voice the parts past the first two are not looked up: the fixed line for the
            # parts Ana can't answer covers them, and they are no FAQ miss.
            cut = MAX_VOICE_PARTS if state.voice else len(action.parts)
            looked_up = action.parts[:cut]
            skipped = tuple(Part(question=part, snippets=()) for part in action.parts[cut:])
            with telemetry.span("fill_action.faq") as current:
                parts = await asyncio.to_thread(
                    retrieve_parts,
                    self.faq,
                    looked_up or (action.question,),
                    action.language,
                    action.country,
                )
                current.set_attribute("sazon.faq.parts", len(parts))
                current.set_attribute("sazon.faq.hits", len(merge_parts(parts)))
            if any(part.snippets for part in parts):
                for part in parts:
                    if not part.snippets:
                        state = self.faq_miss(state, action, unit, conversation_id, part.question)
                return state, action, parts + skipped
            state = self.faq_miss(state, action, unit, conversation_id)
        return state, action, ()

    def faq_miss(
        self,
        state: ConversationState,
        action: AnswerQuestion,
        unit: Unit | None,
        conversation_id: str | None,
        question: str | None = None,
    ) -> ConversationState:
        """A question the FAQ does not answer: a quality flag and a redacted `FaqMiss` row.
        `question`: the part of the action's question that is not answered, if only one."""
        metrics.FAQ_MISS.inc()
        state = add_quality_flag(state, QualityFlag.UNKNOWN_FAQ)
        masked = redact(question or action.question or "", _pii(state))
        logger.info("Unanswered candidate question: %s", masked)
        if unit is not None and conversation_id is not None:
            unit.save(
                FaqMiss(
                    conversation_id=conversation_id,
                    question=masked,
                    language=action.language.value,
                    country=action.country,
                    created_at=to_utc(self.clock()),
                )
            )
        return state

    # --- turn flow ---

    async def _start(self, channel: Channel, context: ConversationContext) -> Reply:
        with telemetry.start_trace("candidate-start") as trace:
            async with self._unit() as unit:
                reply = await self._open(unit, channel, context, trace)
            trace.io(output=reply.text)
            return reply

    async def _open(
        self,
        unit: Unit,
        channel: Channel,
        context: ConversationContext,
        trace: telemetry.TraceHandle,
    ) -> Reply:
        now = self.clock()
        state, greet = engine_start(context)
        conversation = unit.save(
            Conversation(
                channel=channel.value,
                state=_dump(state),
                context=context.model_dump(mode="json"),
                started_at=to_utc(now),
                last_activity_at=to_utc(now),
            )
        )
        metrics.CONVERSATIONS_STARTED.labels(state.country, channel.value).inc()
        trace.set(telemetry.SESSION_ID, conversation.id)
        trace.set("sazon.conversation_id", conversation.id)
        trace.tag(f"country:{state.country}", f"language:{state.language.value}")
        trace.tag(f"channel:{channel.value}")
        text, call = await self._respond(conversation, state, greet, None, ())
        with telemetry.span("persist"):
            self._add_turn(
                unit, conversation.id, TurnRole.AGENT, now, text=text, action=greet, call=call
            )
        return Reply(conversation_id=conversation.id, text=text, ui=greet.ui)

    async def _handle_turn(
        self,
        conversation_id: str,
        message: str | WidgetAnswer,
        trace: telemetry.TraceHandle,
        channel: Channel | None = None,
        emit: Emit | None = None,
    ) -> Reply:
        async with self._unit(conversation_id) as unit:
            return await self._turn(unit, message, trace, channel, emit)

    async def _turn(
        self,
        unit: Unit,
        message: str | WidgetAnswer,
        trace: telemetry.TraceHandle,
        channel: Channel | None = None,
        emit: Emit | None = None,
    ) -> Reply:
        """Read the message, let the engine decide, store the candidate's turn, fill the action,
        reply, store Ana's turn. Asking to repeat, declining consent and a returning candidate
        leave early and store their own turns. Everything is stored when `unit` commits."""
        conversation = unit.root
        if conversation is None:
            raise LookupError("unknown conversation")
        now = self.clock()
        self._resume_if_idle(unit, conversation)
        if channel is not None:
            conversation.channel = channel.value
        voice = conversation.channel == Channel.VOICE.value
        before = state_of(conversation).model_copy(update={"voice": voice})
        today = local_today(before, now)
        trace.user(conversation.candidate_id)
        trace.tag(f"country:{before.country}", f"language:{before.language.value}")
        trace.tag(f"channel:{conversation.channel}")
        telemetry.add_known_pii(_pii(before))

        read = await self._read_message(unit, conversation, before, message, trace, today)
        trace.io(input=read.text)
        if isinstance(read, _Repeat):
            return self._repeat(unit, conversation, before, read.said, read.text, now, emit)

        state, action, extraction = self._decide(before, read, today)
        telemetry.add_known_pii(_pii(state))
        if state.outcome == Outcome.DECLINED_CONSENT:
            return await self._decline(unit, conversation, state, action, now, emit)

        stored_text = self._persist_candidate_turn(
            unit, conversation, state, read, extraction, now, trace
        )
        state, action, found = await self._fill(unit, conversation, before, state, action, now)
        by_model = not read.widget and not (read.call is not None and read.call.fallback_used)
        label = extraction.sentiment if by_model and state.consented else None
        if before.outcome in TERMINAL and state.outcome is None:
            self._reopen_candidate(unit, conversation, now)  # the message undid a gate close
        if isinstance(action, ResumeExisting):
            reply = await self._resume(unit, conversation, state, action, now, label, emit)
            return reply.model_copy(update={"candidate_text": stored_text})

        said = await self._reply(
            unit, conversation, before, state, action, stored_text, found, emit
        )
        await self._persist_agent_turn(unit, conversation, action, said, now, label)
        if _contact_changed(action) and conversation.candidate_id is not None:
            # The invitation goes to the email they have now, once it is stored.
            unit.after_commit(
                partial(
                    scheduling.refresh_event,
                    self.repo,
                    conversation.candidate_id,
                    self.scheduling,
                )
            )
        return Reply(
            conversation_id=conversation.id,
            text=said.text,
            candidate_text=stored_text,
            ui=action.ui,
            ended=is_ended(said.state),
            outcome=said.state.outcome,
        )

    async def _read_message(
        self,
        unit: Unit,
        conversation: Conversation,
        state: ConversationState,
        message: str | WidgetAnswer,
        trace: telemetry.TraceHandle,
        today: date,
    ) -> "_Message | _Repeat":
        """Step 1: a widget tap is read without a model; a typed or spoken message is sanitized
        and then extracted, unless it only asks Ana to say her last message again."""
        widget = widget_extraction(state, message) if isinstance(message, WidgetAnswer) else None
        if widget is not None:
            extraction, text = widget
            trace.tag("input:widget")
            return _Message(text=text, extraction=extraction, widget=True)
        raw = message if isinstance(message, str) else _widget_text(message)
        with telemetry.span("sanitize") as current:
            text, suspicious = sanitize_input(raw)
            text = mask_id_numbers(text)
            fix = fix_reading(state, text)
            current.set_attribute("sazon.injection_suspected", suspicious)
        turns = unit.turns(conversation.id)
        repeat = not suspicious and asks_to_repeat(text)
        if repeat and (said := last_said(turns)) is not None:
            return _Repeat(said=said, text=text)
        history = chat_history(turns)
        with telemetry.span("extract") as current:
            result = await self.llm.extract(history, state, text, today=today)
            extraction, call = result.extraction, result.call
            _annotate_call(current, call)
            # Before validation: a rejected name is still a name.
            telemetry.add_known_pii(_extracted_pii(extraction))
            telemetry.set_io(current, input=text, output=extraction.model_dump_json())
        metrics.observe_call(call)
        return _Message(text=text, extraction=extraction, call=call, suspicious=suspicious, fix=fix)

    def _decide(
        self, state: ConversationState, read: "_Message", today: date
    ) -> tuple[ConversationState, Action, TurnExtraction]:
        """Step 2: the deterministic readings of a typed answer (a slot, a mode, a licence) over
        the model's, then the engine's step. Returns the extraction the engine saw."""
        extraction = read.extraction
        request: TimeRequest | None = None
        cancel = False
        with telemetry.span("engine.step") as current:
            if not read.widget and _scheduling(state):
                extraction, request = slot_reading(state, extraction, read.text, today)
            if not read.widget and _choosing_mode(state):
                extraction = with_mode_choice(state, extraction, read.text)
            if not read.widget:
                extraction = with_consent(state, extraction, read.text)
                extraction = with_credential_reading(state, extraction, read.text)
                extraction = with_refusal(state, extraction, read.text)
                extraction = with_named_place(state, extraction, read.text)
                extraction = with_place_reading(state, extraction, read.text)
                extraction = without_echoed_credential(read.fix, extraction, read.text)
                extraction = with_years_words(extraction, read.text)
                extraction = with_email_as_typed(extraction, read.text)
                extraction = with_date_ahead(extraction, read.text, today)
                extraction, cancel = cancel_reading(state, extraction, read.text)
            understood = extraction.slot is not None or extraction.mode is not None
            # A correction read by code needs no extractor: "mi correo está mal" is enough.
            understood = understood or request is not None or read.fix is not None
            # A detected injection always reaches the engine: a model that refused to read the
            # message (a content filter on Morse, say) must not let it through unanswered.
            outage = read.call is not None and read.call.fallback_used
            if outage and not understood and not read.suspicious:
                action = _current_prompt(state)
                current.set_attribute("sazon.repeated_prompt", True)
            else:
                state, action = engine_step(
                    state,
                    extraction,
                    today=today,
                    suspicious=read.suspicious,
                    fix=read.fix,
                    value_only=not read.widget and value_only(read.text),
                    time_request=request,
                    cancel_request=cancel,
                )
            current.set_attribute("sazon.action", action.kind)
            current.set_attribute("sazon.stage", state.stage)
        return state, action, extraction

    def _persist_candidate_turn(
        self,
        unit: Unit,
        conversation: Conversation,
        state: ConversationState,
        read: "_Message",
        extraction: TurnExtraction,
        now: datetime,
        trace: telemetry.TraceHandle,
    ) -> str | None:
        """Step 3: the candidate's turn, with its text and extraction only once they consented;
        the candidate record at consent. Returns the text stored."""
        stored_text = read.text if state.consented else None
        if last_turn_is_nudge(unit.turns(conversation.id)):
            if not conversation.recovered_by_nudge:
                metrics.NUDGE_RECOVERIES.inc()
            conversation.recovered_by_nudge = True
        self._add_turn(
            unit,
            conversation.id,
            TurnRole.CANDIDATE,
            now,
            text=stored_text,
            extraction=extraction if state.consented else None,
            call=read.call,
        )
        if state.consented and conversation.candidate_id is None:
            conversation.candidate_id = self._new_candidate(unit, conversation, now).id
        trace.user(conversation.candidate_id)
        return stored_text

    async def _fill(
        self,
        unit: Unit,
        conversation: Conversation,
        before: ConversationState,
        state: ConversationState,
        action: Action,
        now: datetime,
    ) -> tuple[ConversationState, Action, tuple[Part, ...]]:
        """Step 4: what the action needs before Ana can say it: the duplicate check, interview
        modes, slots and bookings, cancelling a booking the candidate gave up, FAQ passages."""
        with telemetry.span("fill_action") as current:
            if isinstance(action, CheckDuplicate):
                with telemetry.span("fill_action.duplicate"):
                    state, action = await self._off_loop(
                        self._check_duplicate, unit, conversation, state, action, now
                    )
            if isinstance(action, FindModes | FindSlots | BookSlot):
                with telemetry.span("fill_action.slots"):
                    # Calendars are network calls: off the event loop.
                    state, action = await self._off_loop(
                        self._schedule, unit, conversation, state, action, now
                    )
            if before.booking is not None and state.booking is None:
                by = "candidate" if _candidate_cancelled(action) else None
                await self._off_loop(self._cancel_booking, conversation, now, by)
            state, action, found = await self.fill_action(state, action, unit, conversation.id)
            current.set_attribute("sazon.action", action.kind)
        return state, action, found

    async def _reply(
        self,
        unit: Unit,
        conversation: Conversation,
        before: ConversationState,
        state: ConversationState,
        action: Action,
        stored_text: str | None,
        found: tuple[Part, ...],
        emit: Emit | None,
    ) -> "_AgentTurn":
        """Step 5: Ana's words for the action, after the process overview when the candidate has
        just consented. Nothing on a call that has already said goodbye. A question that asks
        several things gets its snippets grouped by part, and its sources cover every part."""
        snippets = merge_parts(found)
        lead: str | None = None
        if not before.consented and state.consented and isinstance(action, AFTER_OVERVIEW):
            lead = overview(state.language, state.country, before.voice)
            if emit is not None:
                emit(lead)
        turns = unit.turns(conversation.id)
        if before.voice and isinstance(action, End) and call_over(turns, before):
            return _AgentTurn(state=state, text="", stored=None, call=None, snippets=snippets)
        result = await self._respond_result(
            conversation,
            state,
            action,
            stored_text,
            tuple(snippet.text for snippet in snippets),
            emit,
            send_as_written=send_as_written(snippets),
            parts=_faq_parts(found),
            faq_country=next((part.country for part in found if part.snippets), None),
        )
        text = result.reply.text if lead is None else f"{lead} {result.reply.text}"
        if isinstance(action, AnswerQuestion) and snippets and result.answered is False:
            # The passages found were near the topic but did not answer it (SAZ-20).
            state = self.faq_miss(state, action, unit, conversation.id)
            snippets = ()
        elif isinstance(action, AnswerQuestion):
            for question in result.missed_parts:
                state = self.faq_miss(state, action, unit, conversation.id, question)
        return _AgentTurn(state=state, text=text, stored=text, call=result.call, snippets=snippets)

    async def _persist_agent_turn(
        self,
        unit: Unit,
        conversation: Conversation,
        action: Action,
        said: "_AgentTurn",
        now: datetime,
        label: Sentiment | None,
    ) -> None:
        """Step 6: Ana's turn with its sources, the state, and the summary once finished."""
        with telemetry.span("persist"):
            self._add_turn(
                unit,
                conversation.id,
                TurnRole.AGENT,
                now,
                text=said.stored,
                action=action,
                call=said.call,
                sources=citations(said.snippets) or None,
            )
            self._save_state(unit, conversation, said.state, now, label)
            await self._summarize_if_done(unit, conversation, said.state, now)

    def last_said(self, conversation_id: str) -> Turn | None:
        """Ana's message the candidate is answering: the last one shown, if it is hers. Reads
        the database: for callers outside a turn."""
        return last_said(self.repo.turns(conversation_id))

    def _repeat(
        self,
        unit: Unit,
        conversation: Conversation,
        state: ConversationState,
        said: Turn,
        text: str,
        now: datetime,
        emit: Emit | None,
    ) -> Reply:
        """ "¿A las qué?", "¿cómo?", "sorry?" (SAZ-48): Ana says her last message again, word
        for word, with the same widget. No model reads the request and the engine does not
        step: it is no answer, no failed answer and no question for the FAQ."""
        stored_text = text if state.consented else None
        self._add_turn(unit, conversation.id, TurnRole.CANDIDATE, now, text=stored_text)
        metrics.TURNS.labels(TurnRole.AGENT.value).inc()
        unit.save(
            Turn(
                conversation_id=conversation.id,
                role=TurnRole.AGENT.value,
                text=said.text,
                ui=said.ui,
                action=said.action,
                created_at=to_utc(now),
            )
        )
        self._save_state(unit, conversation, state, now)
        reply = said.text or ""
        if emit is not None:
            emit(reply)
        record("repeat_request")
        return Reply(
            conversation_id=conversation.id,
            text=reply,
            candidate_text=stored_text,
            ui=UI_ADAPTER.validate_python(said.ui) if said.ui is not None else None,
            ended=is_ended(state),
            outcome=state.outcome,
        )

    def _schedule(
        self,
        unit: Unit,
        conversation: Conversation,
        state: ConversationState,
        action: FindModes | FindSlots | BookSlot,
        now: datetime,
    ) -> tuple[ConversationState, Action]:
        """Run the engine's mode and slot lookups and bookings until it has something to say.
        Blocking: the lookups read the database and the calendars, and a booking commits on its
        own (`scheduling.book`). Run it in a thread.

        Modes and slots only ever come from the scheduling service, never from a model.
        """
        next_action: Action = action
        scope = agent().interview_scope(state.profile)  # who may interview them
        while isinstance(next_action, FindModes | FindSlots | BookSlot):
            self._add_turn(unit, conversation.id, TurnRole.SYSTEM, now, action=next_action)
            if isinstance(next_action, FindModes):
                offered = scheduling.available_modes(
                    self.repo, next_action.location_id, config=self.scheduling, scope=scope
                )
                modes = [InterviewMode(mode.value) for mode in offered]
                result: LookupResult = ModesFound(request=next_action, modes=modes)
            elif isinstance(next_action, FindSlots):
                found = scheduling.lookup(
                    self.repo,
                    next_action.location_id,
                    next_action.schedule,
                    next_action.start_date,
                    now,
                    exclude=next_action.exclude,
                    mode=_stored_mode(next_action.mode),
                    config=self.scheduling,
                    query=next_action.query,
                    avoid_days=next_action.avoid_days,
                    scope=scope,
                )
                slots = [scheduling.slot_view(slot, state.language) for slot in found.slots]
                result = SlotsFound(request=next_action, slots=slots, miss=found.miss)
            else:
                booked = self._book(conversation, next_action.slot_id, state, now, scope)
                result = SlotBooked(request=next_action, booked=booked)
            state, next_action = on_lookup(state, result)
        return state, next_action

    def _book(
        self,
        conversation: Conversation,
        slot_id: str,
        state: ConversationState,
        now: datetime,
        scope: scheduling.Scope,
    ) -> Booked | None:
        if conversation.candidate_id is None:
            return None
        try:
            booking = scheduling.book(
                self.repo,
                conversation.candidate_id,
                conversation.id,
                slot_id,
                now,
                mode=_stored_mode(state.profile.interview_mode),
                config=self.scheduling,
                scope=scope,
            )
        except scheduling.SlotUnavailable:
            return None
        slot = scheduling.get_slot(self.repo, slot_id)
        if slot is None:
            return None
        return scheduling.booked_view(slot, state.language, booking)

    def _cancel_booking(
        self, conversation: Conversation, now: datetime, by: str | None = None
    ) -> None:
        """Cancel the candidate's booking and delete its calendar event, as the recruiter's
        calendar does: `by` candidate when they cancelled it or opted out."""
        if conversation.candidate_id is None:
            return
        booking = scheduling.active_booking(self.repo, conversation.candidate_id)
        if booking is not None:
            scheduling.cancel(self.repo, booking.id, now, config=self.scheduling, by=by)

    def _check_duplicate(
        self,
        unit: Unit,
        conversation: Conversation,
        state: ConversationState,
        action: CheckDuplicate,
        now: datetime,
    ) -> tuple[ConversationState, Action]:
        """Look up an open application with the phone or email, and the earlier ones closed
        for a manipulation attempt (D11). A match is not resumed yet: the engine asks first
        whether to carry on as that person (D10), with its first name and, once it has a
        location, in its country's register. Blocking: run it in a thread."""
        self._add_turn(unit, conversation.id, TurnRole.SYSTEM, now, action=action)
        exclude = (conversation.candidate_id, *state.declined_applications)
        found = unit.read(
            lambda session: self.repo.find_open_applications(
                action.phone, action.email, now, exclude=exclude, session=session
            )
        )
        match = next(
            (match for existing in found if (match := self._match(unit, existing, action))), None
        )
        closes = unit.read(
            lambda session: self.repo.count_policy_closes(
                action.phone, action.email, exclude=(conversation.candidate_id,), session=session
            )
        )
        return on_lookup(state, DuplicateChecked(match=match, policy_closes=closes))

    def _match(
        self, unit: Unit, existing: Candidate, action: CheckDuplicate
    ) -> DuplicateMatch | None:
        """`existing` as an application this conversation may resume, or None. An abandoned
        one that can't reopen does not count, nor a chat still waiting for its own answer to
        "¿Quieres continuar como...?" (it has no application of its own yet)."""
        previous = unit.read(
            lambda session: self.repo.application_conversation(existing.id, session=session)
        )
        held = state_of(previous) if previous is not None else None
        if held is None or held.pending_identity is not None:
            return None
        if existing.status == CandidateStatus.ABANDONED and reopen(held).outcome is not None:
            return None
        name = held.profile.full_name or (existing.profile or {}).get("full_name")
        located = held.country_source == CountrySource.LOCATION
        return DuplicateMatch(
            application_id=existing.id,
            name=str(name).split()[0] if name else None,
            matched_by="phone" if action.phone and existing.phone == action.phone else "email",
            country=held.country if located else None,
        )

    async def _resume(
        self,
        unit: Unit,
        conversation: Conversation,
        state: ConversationState,
        action: ResumeExisting,
        now: datetime,
        sentiment: Sentiment | None = None,
        emit: Emit | None = None,
    ) -> Reply:
        """Link this conversation to the open application and carry on from its current state
        (SAZ-47), in this conversation's language and register and on its channel.

        A qualified application with its interview booked gets its status and a goodbye in
        this conversation, which ends. An open one, or a qualified one saved without an
        interview (SAZ-72), gets a welcome back and its pending question or new interview
        times, and the chat carries on in it. The application's state is read from its own
        conversation, never from a stub linked to it by an earlier return."""
        self._save_state(unit, conversation, state, now, sentiment)  # the provisional candidate's
        previous = await self._off_loop(unit.application_conversation, action.application_id)
        provisional = conversation.candidate_id
        conversation.candidate_id = action.application_id
        unit.save(conversation)
        if provisional and provisional != action.application_id:
            unit.delete_candidate(provisional)

        lead = _template(action, state, now)
        if previous is None:
            self._add_turn(unit, conversation.id, TurnRole.AGENT, now, text=lead, action=action)
            if emit is not None:
                emit(lead)
            return Reply(conversation_id=conversation.id, text=lead, ended=True)
        previous_state = state_of(previous)
        if previous_state.outcome == Outcome.ABANDONED:
            previous_state = self._reopen(unit, previous, previous_state, now)
        saved = previous_state.stage == Stage.DONE
        previous_state, follow = resume(
            previous_state, state, today=local_today(previous_state, now)
        )
        if isinstance(follow, ApplicationStatus):
            follow = await self._off_loop(self._status_in_language, follow)
            channel = Channel(conversation.channel)
            text = _template(follow, state, now, channel)
            if emit is not None:
                emit(text)
            if follow.booking is None or channel == Channel.VOICE:
                self._add_turn(unit, conversation.id, TurnRole.AGENT, now, text=text, action=follow)
                return Reply(conversation_id=conversation.id, text=text, ui=follow.ui, ended=True)
            # A booked interview: the chat carries on in the application's conversation, which
            # holds the booking, so "¿la puedo cambiar?" moves it there.
            previous.channel = conversation.channel
            self._add_turn(unit, previous.id, TurnRole.AGENT, now, text=text, action=follow)
            self._save_state(unit, previous, previous_state, now)
            return Reply(
                conversation_id=previous.id,
                text=text,
                ui=follow.ui,
                ended=is_ended(previous_state),
                outcome=previous_state.outcome,
            )

        self._add_turn(unit, conversation.id, TurnRole.AGENT, now, text=lead, action=action)
        if isinstance(follow, FindModes | FindSlots | BookSlot):
            previous_state, follow = await self._off_loop(
                self._schedule, unit, previous, previous_state, follow, now
            )
            if isinstance(follow, OfferSlots):
                follow = follow.model_copy(update={"reason": None})  # the welcome says why
        previous.channel = conversation.channel  # the candidate carries on where they are now
        name = previous_state.profile.full_name
        said: Action = follow
        if saved and isinstance(follow, OfferSlots) and follow.slots:
            # Saved without an interview: one fixed line with the new times (QA batch 2).
            voice = conversation.channel == Channel.VOICE.value
            text, call = welcome_back_with_times(follow, name, voice=voice), None
        elif isinstance(follow, WELCOMED):
            # The welcome is the lead of one reply, checked whole (QA batch 3).
            said = WelcomeBack(
                name=name,
                follow_up=follow,
                ui=follow.ui,
                language=follow.language,
                country=follow.country,
                simplify=follow.simplify,
            )
            text, call = await self._respond(previous, previous_state, said, None, ())
        else:
            text, call = await self._respond(previous, previous_state, follow, None, ())
        self._add_turn(unit, previous.id, TurnRole.AGENT, now, text=text, action=said, call=call)
        self._save_state(unit, previous, previous_state, now)
        return Reply(
            conversation_id=previous.id,
            text=text,
            ui=follow.ui,
            ended=is_ended(previous_state),
            outcome=previous_state.outcome,
        )

    def _status_in_language(self, status: ApplicationStatus) -> ApplicationStatus:
        """The booked interview's day and time in the language the candidate speaks now.
        Blocking: run it in a thread."""
        booking = status.booking
        slot = scheduling.get_slot(self.repo, booking.slot_id) if booking else None
        if booking is None or slot is None:
            return status
        label = scheduling.booked_view(slot, status.language).label
        booking = booking.model_copy(update={"label": label})
        return status.model_copy(update={"booking": booking, "ui": booking})

    def _reopen_candidate(self, unit: Unit, conversation: Conversation, now: datetime) -> None:
        """A closed application is open again: the candidate is in progress and the summary
        is written again when the screening ends."""
        conversation.ended_at = None
        unit.delete_summary(conversation.id)
        candidate = unit.candidate(conversation.candidate_id)
        if candidate is not None:
            candidate.status = CandidateStatus.IN_PROGRESS
            candidate.outcome_reason = None
            candidate.closed_at = None
            candidate.updated_at = to_utc(now)
            unit.save(candidate)

    def continued_in(self, conversation: Conversation) -> str | None:
        """The conversation a returning candidate's chat or call moved to when this one resumed
        their application (`_resume`): the application's own. None when it did not move: no
        resume, no conversation holds the application, or a voice call told the status of a
        closed or booked one here and hung up. Reads the database: for callers outside a turn."""
        application = state_of(conversation).duplicate_of
        if application is None:
            return None
        if (self.last_agent_action(conversation.id) or {}).get("kind") == "application_status":
            return None
        holder = self.repo.application_conversation(application)
        return holder.id if holder is not None and holder.id != conversation.id else None

    def last_agent_action(self, conversation_id: str) -> dict[str, Any] | None:
        """The action of Ana's last message in the conversation, as stored. Reads the database:
        for callers outside a turn."""
        turn = self.repo.last_turn(conversation_id, TurnRole.AGENT)
        return turn.action if turn is not None else None

    def call_over(self, conversation_id: str, state: ConversationState) -> bool:
        """A call in this state should hang up (SAZ-47): nothing is left to answer, or Ana's
        last turn said goodbye or closed the application, even with an interview booked. Reads
        the database: for callers outside a turn."""
        return is_ended(state) or closes_call(self.last_agent_action(conversation_id))

    def _reopen(
        self, unit: Unit, conversation: Conversation, state: ConversationState, now: datetime
    ) -> ConversationState:
        """An abandoned application under 30 days old carries on where it stopped."""
        state = reopen(state)
        if state.outcome is not None:
            return state
        conversation.state = _dump(state)
        conversation.outcome = None
        conversation.drop_off_stage = None
        self._reopen_candidate(unit, conversation, now)
        unit.save(conversation)
        return state

    async def _decline(
        self,
        unit: Unit,
        conversation: Conversation,
        state: ConversationState,
        action: Action,
        now: datetime,
        emit: Emit | None = None,
    ) -> Reply:
        """Declined consent keeps only a bare conversation record for the consent-decline
        rate: its id, channel, timestamps and outcome. No turns, no candidate, and no context,
        country or language."""
        text, _ = await self._respond(conversation, state, action, None, (), emit)
        # The conversation first: it no longer points at the candidate deleted after it.
        unit.save(conversation)
        unit.delete_turns(conversation.id)
        unit.delete_faq_misses(conversation.id)
        if conversation.candidate_id is not None:
            unit.delete_candidate(conversation.candidate_id)
            conversation.candidate_id = None
        bare = ConversationState(phase=Closed(variant=DeclinedConsent()))
        conversation.state = bare.model_dump(mode="json", include={"state_version", "phase"})
        conversation.context = {}
        _count_outcome(conversation.outcome, Outcome.DECLINED_CONSENT)
        conversation.outcome = Outcome.DECLINED_CONSENT
        conversation.quality_flags = []
        conversation.drop_off_stage = None
        conversation.recovered_by_nudge = False
        conversation.elevenlabs_conversation_id = None
        conversation.ended_at = to_utc(now)
        conversation.last_activity_at = to_utc(now)
        return Reply(
            conversation_id=conversation.id,
            text=text,
            ended=True,
            outcome=Outcome.DECLINED_CONSENT,
        )

    async def _respond(
        self,
        conversation: Conversation,
        state: ConversationState,
        action: Action,
        last_message: str | None,
        snippets: Sequence[str],
        emit: Emit | None = None,
    ) -> tuple[str, CallRecord]:
        result = await self._respond_result(
            conversation, state, action, last_message, snippets, emit
        )
        return result.reply.text, result.call

    async def _respond_result(
        self,
        conversation: Conversation,
        state: ConversationState,
        action: Action,
        last_message: str | None,
        snippets: Sequence[str],
        emit: Emit | None = None,
        *,
        send_as_written: bool = False,
        parts: Sequence[FaqPart] = (),
        faq_country: Country | None = None,
    ) -> ReplyResult:
        ctx = ReplyContext.for_action(
            action,
            today=local_today(state, self.clock()),
            channel=Channel(conversation.channel),
            faq_snippets=snippets,
            faq_send_as_written=send_as_written,
            faq_parts=parts,
            faq_country=faq_country,
            candidate_name=state.profile.full_name,
            last_message=last_message,
        )
        with telemetry.span("respond", **{"sazon.action": action.kind}) as current:
            if emit is None:
                result = await self.llm.respond(action, ctx)
            elif isinstance(self.llm, StreamingResponder):
                result = await self.llm.respond_stream(action, ctx, emit)
            else:
                result = await self.llm.respond(action, ctx)
                emit(result.reply.text)
            _annotate_call(current, result.call)
            telemetry.set_io(current, output=result.reply.text)
        metrics.observe_call(result.call)
        return result

    async def _summarize_if_done(
        self, unit: Unit, conversation: Conversation, state: ConversationState, now: datetime
    ) -> None:
        if state.outcome not in TERMINAL:
            return
        if await self._off_loop(unit.has_summary, conversation.id):
            return
        # Interview times are shown on their own and can still change: keep them out. So is an
        # insult the candidate got past (D12): it stays in the transcript, not in the summary.
        transcript = chat_history(
            unit.turns(conversation.id),
            skip=SCHEDULING_ACTIONS,
            drop_recovered_insult=state.abuse_strikes < MAX_ABUSE_STRIKES,
        )
        with telemetry.span("summarize") as current:
            result = await self.llm.summarize(
                transcript,
                state.profile,
                language=SUMMARY_LANGUAGE,
                country=state.country,
                today=local_today(state, now),
            )
            _annotate_call(current, result.call)
        call = result.call
        metrics.observe_call(call)
        unit.save(
            Summary(
                conversation_id=conversation.id,
                body=result.summary.model_dump(mode="json"),
                model=call.model,
                latency_ms=call.latency_ms,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                cost_usd=call.cost_usd,
                fallback_used=call.fallback_used,
                created_at=to_utc(now),
            )
        )

    # --- persistence ---

    def _add_turn(
        self,
        unit: Unit,
        conversation_id: str,
        role: TurnRole,
        now: datetime,
        *,
        text: str | None = None,
        extraction: TurnExtraction | None = None,
        action: Action | None = None,
        call: CallRecord | None = None,
        nudge: bool = False,
        sources: list[dict[str, Any]] | None = None,
    ) -> Turn:
        ui = action.ui if action is not None else None
        metrics.TURNS.labels(role.value).inc()
        return unit.save(
            Turn(
                conversation_id=conversation_id,
                role=role.value,
                text=text,
                ui=ui.model_dump(mode="json") if ui is not None else None,
                extraction=extraction.model_dump(mode="json") if extraction else None,
                action=action.model_dump(mode="json") if action is not None else None,
                latency_ms=call.latency_ms if call else None,
                model=call.model if call else None,
                input_tokens=call.input_tokens if call else None,
                output_tokens=call.output_tokens if call else None,
                cost_usd=call.cost_usd if call else None,
                fallback_used=call.fallback_used if call else False,
                nudge=nudge,
                sources=sources,
                created_at=to_utc(now),
            )
        )

    def _new_candidate(self, unit: Unit, conversation: Conversation, now: datetime) -> Candidate:
        return unit.save(
            Candidate(
                channel=conversation.channel,
                posting_location_id=_known_location(conversation.context.get("location_id")),
                created_at=to_utc(now),
                updated_at=to_utc(now),
            )
        )

    def _save_state(
        self,
        unit: Unit,
        conversation: Conversation,
        state: ConversationState,
        now: datetime,
        sentiment: Sentiment | None = None,
    ) -> None:
        """`sentiment`: the label of the candidate message this turn, None when the extractor did
        not read one (a widget tap, a fallback extraction)."""
        moment = to_utc(now)
        stats = SentimentStats.load(conversation.sentiment)
        if sentiment is not None:
            stats = stats.record(sentiment)
            conversation.sentiment = stats.dump()
        conversation.state = _dump(state)
        _count_outcome(conversation.outcome, state.outcome)
        flags = [flag.value for flag in state.quality_flags]
        for flag in set(flags) - set(conversation.quality_flags or ()):
            metrics.QUALITY_FLAGS.labels(flag).inc()
        conversation.outcome = state.outcome
        conversation.quality_flags = flags
        conversation.drop_off_stage = state.drop_off_stage
        conversation.last_activity_at = moment
        if state.stage == Stage.DONE and conversation.ended_at is None:
            conversation.ended_at = moment
        unit.save(conversation)
        candidate = unit.candidate(conversation.candidate_id)
        if candidate is None:
            return
        profile = state.profile
        candidate.profile = profile.model_dump(mode="json")
        candidate.phone = profile.phone
        candidate.email = profile.email
        candidate.location_id = profile.location_id
        candidate.late_start_days = profile.late_start_days
        candidate.talent_pool = state.talent_pool_opt_in is True
        candidate.quality_flags = [flag.value for flag in state.quality_flags]
        if stats.mood is not None:
            candidate.sentiment = stats.mood.value
        candidate.language = state.language.value
        candidate.country = state.country
        status = STATUS_OF.get(state.outcome) if state.outcome else None
        if status is not None:
            candidate.status = status
            candidate.outcome_reason = state.disqualify_reason
            candidate.closed_at = candidate.closed_at or moment
        candidate.updated_at = moment
        unit.save(candidate)

    # --- failures ---

    async def _failure_reply(self, conversation_id: str, exc: Exception) -> Reply:
        state: ConversationState | None = None
        channel = Channel.WEB
        try:
            conversation = await self._off_loop(self.repo.get_conversation, conversation_id)
            if conversation is not None:
                state = state_of(conversation)
                channel = Channel(conversation.channel)
        except Exception:
            state = None
        _log_failure(exc, conversation_id, _pii(state) if state else ())
        if state is None:
            return Reply(conversation_id=conversation_id, text=NOTHING_UNDERSTOOD)
        try:
            prompt = _current_prompt(state)
            register = register_of(state.language, state.country)
            text = f"{RETRY[register]} {_template(prompt, state, self.clock(), channel)}"
            return Reply(conversation_id=conversation_id, text=text, ui=prompt.ui)
        except Exception:
            return Reply(conversation_id=conversation_id, text=NOTHING_UNDERSTOOD)


# --- helpers ---


def chat_history(
    turns: Sequence[Turn],
    skip: frozenset[str] = frozenset(),
    *,
    drop_recovered_insult: bool = False,
) -> list[ChatTurn]:
    """The turns a model reads: those with text, without the engine's own steps, nor the
    actions in `skip`."""
    if drop_recovered_insult:
        turns = without_recovered_insult(turns)
    history: list[ChatTurn] = []
    for turn in turns:
        if turn.text is None or turn.role == TurnRole.SYSTEM:
            continue
        if turn.action is not None and _kind(turn.action) in skip:
            continue
        role = "candidate" if turn.role == TurnRole.CANDIDATE else "assistant"
        history.append(ChatTurn(role=role, text=turn.text))
    return history


def shown(turns: Sequence[Turn]) -> list[Turn]:
    """The turns a reader sees (`Repo.shown_turns`)."""
    return [turn for turn in turns if turn.text is not None and turn.role != TurnRole.SYSTEM]


def last_said(turns: Sequence[Turn]) -> Turn | None:
    """Ana's message the candidate is answering: the last one shown, if it is hers."""
    seen = shown(turns)
    return seen[-1] if seen and seen[-1].role == TurnRole.AGENT else None


def nudges_since_last_message(turns: Sequence[Turn]) -> dict[int, Turn]:
    """The nudges sent since the candidate last wrote, by attempt."""
    nudges: dict[int, Turn] = {}
    for turn in reversed(shown(turns)):
        if turn.role == TurnRole.CANDIDATE:
            break
        if turn.nudge and turn.action is not None:
            nudges.setdefault(int(turn.action.get("attempt", 0)), turn)
    return nudges


def last_turn_is_nudge(turns: Sequence[Turn]) -> bool:
    seen = shown(turns)
    return bool(seen) and seen[-1].nudge


def last_agent_action(turns: Sequence[Turn]) -> dict[str, Any] | None:
    """The action of Ana's last message, as stored."""
    agent = [turn for turn in turns if turn.role == TurnRole.AGENT]
    return agent[-1].action if agent else None


def call_over(turns: Sequence[Turn], state: ConversationState) -> bool:
    """`ScreeningService.call_over` over the turns in hand."""
    return is_ended(state) or closes_call(last_agent_action(turns))


def abandoned_idle(turns: Sequence[Turn]) -> bool:
    """Abandoned by going quiet: Ana's last word was not an opt-out or abuse ending. An
    opt-out followed by more messages is told apart by the state's `opted_out`."""
    action = last_agent_action(turns)
    if action is None or action.get("kind") != "end":
        return True
    return action.get("reason") not in FINAL_END_REASONS


def local_today(state: ConversationState, now: datetime) -> date:
    """Today in the candidate's location, else the browser's timezone, else Madrid."""
    zone = ZoneInfo(DEFAULT_TIMEZONE)
    location = get_location(state.profile.location_id) if state.profile.location_id else None
    names = [location.timezone if location else None, state.context.timezone]
    for name in names:
        if not name:
            continue
        try:
            zone = ZoneInfo(name)
            break
        except (ZoneInfoNotFoundError, ValueError):
            continue
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(zone).date()


def _scheduling_from_settings(repo: Repo) -> scheduling.SchedulingConfig:
    from sazon.config import get_settings

    return scheduling.scheduling_from(get_settings(), repo)


def closes_call(action: dict[str, Any] | None) -> bool:
    """A call hangs up after this agent action (SAZ-47), with or without a booking: a goodbye,
    a closed application's status, or a close that asks nothing more (not one that asks about
    the talent pool, as out of area does)."""
    if not action:
        return False
    kind = action.get("kind")
    if kind == "disqualify":
        return not agent().talent_pool(action.get("reason"))
    return kind in CALL_CLOSING_ACTIONS


CONTACT_TYPES = frozenset({FieldType.PERSON_NAME, FieldType.CONTACT})


def _contact_changed(action: Action) -> bool:
    """A name, phone or email corrected after booking: the calendar invitation names them."""
    if not isinstance(action, DetailsUpdated) or not action.changed:
        return False
    return agent().field(action.field).type in CONTACT_TYPES


def is_ended(state: ConversationState) -> bool:
    """Nothing left to answer. A booked interview keeps the chat open (replying here moves it),
    and so does a talent-pool question still waiting for its yes or no."""
    return state.closed is not None and not awaits_talent_pool_answer(state)


def _scheduling(state: ConversationState) -> bool:
    return state.stage == Stage.SCHEDULING or state.booking is not None


def _choosing_mode(state: ConversationState) -> bool:
    """The candidate can pick or switch the interview mode: there are two, and the interview is
    being chosen or booked."""
    return len(state.available_modes) > 1 and (state.stage == Stage.MODE or _scheduling(state))


def _stored_mode(mode: InterviewMode | None) -> StoredMode:
    return StoredMode(mode.value) if mode is not None else StoredMode.ONSITE


def with_mode_choice(
    state: ConversationState, extraction: TurnExtraction, text: str
) -> TurnExtraction:
    """Settle the interview mode the candidate meant: our own reading of their words first,
    the extractor's hint when ours finds none. Only a mode on offer at the location is kept."""
    available = state.available_modes
    ours = resolve_mode(text)
    hint = read_mode(extraction.mode)
    choice = ours if ours in available else hint if hint in available else None
    return extraction.model_copy(update={"mode": choice.value if choice else None})


def with_credential_reading(
    state: ConversationState, extraction: TurnExtraction, text: str
) -> TurnExtraction:
    """The candidate's own words about a credential, over the extractor's reading.

    A license the words say does not count (a learner's permit, expired, being renewed,
    suspended, still getting it) is not a yes, and a no records why. A hedged no ("creo que no,
    no estoy segura") is asked again, however sure the extractor was. And a typed yes or no to
    the credential's question that the extractor did not report as one (a bare "Sí" read as a
    confirm with no field) is the answer, as the buttons are. Words that clearly claim a valid
    license ("tengo driver license vigente") are a yes over the extractor's no; a no the words
    leave open ("No está vencida") stays the extractor's."""
    for field in agent().of_type(FieldType.CREDENTIAL):
        extraction = _credential_reading(state, extraction, text, field.id)
    return extraction


def _credential_reading(
    state: ConversationState, extraction: TurnExtraction, text: str, field: FieldId
) -> TurnExtraction:
    candidate = extraction.fields.get(field)
    words = agent().field(field).words
    if candidate is None:
        if not _awaits(state, field) or extraction.intent not in CREDENTIAL_ANSWER_INTENTS:
            return extraction
        if not (reads_as_license_answer(text, words) or license_affirmed(text, words)):
            return extraction
        fields = {**extraction.fields, field: FieldCandidate(value=text, confidence=1.0)}
        intent = Intent.ANSWER if extraction.intent != Intent.CORRECTION else extraction.intent
        return extraction.model_copy(update={"fields": fields, "intent": intent})
    reading = license_reading(text, words)
    if reading is not None:
        value = (
            LICENSE_IN_PROGRESS if reading == ReasonCode.LICENSE_IN_PROGRESS else LICENSE_NOT_VALID
        )
        candidate = FieldCandidate(value=value, confidence=1.0)
    elif candidate.value is not True and license_unsure(text):
        candidate = candidate.model_copy(update={"confidence": 0.0})
    elif candidate.value is not True and license_affirmed(text, words):
        candidate = FieldCandidate(value=True, confidence=1.0)
    else:
        return extraction
    fields = {**extraction.fields, field: candidate}
    return extraction.model_copy(update={"fields": fields})


def _awaits(state: ConversationState, field: FieldId) -> bool:
    """`field`'s question is the one on screen, with no read-back or pause offer over it."""
    return (
        state.consented
        and state.stage != Stage.DONE
        and state.awaiting == field
        and state.pending_confirmation is None
        and not state.pause_offered
    )


def with_consent(state: ConversationState, extraction: TurnExtraction, text: str) -> TurnExtraction:
    """ "Sí, adelante, pero ¿cuánto se paga?" to the privacy notice is consent, and its question
    is answered after it (D1); read as a question by the extractor, it asked for consent again."""
    if state.consented or extraction.intent in (Intent.CONFIRM, Intent.OPT_OUT, Intent.ABUSE):
        return extraction
    if not opens_with_yes(text):
        return extraction
    return extraction.model_copy(update={"intent": Intent.CONFIRM})


# Words that make "¿por qué pone…?" about the location even when no place is named.
LOCATION_WORDS = frozenset(
    {"local", "sucursal", "zona", "alcaldia", "colonia", "eso", "ese", "esa"}
)


def fix_reading(state: ConversationState, text: str) -> FieldId | InterviewFix | None:
    """The field the candidate says is wrong, asks to change or asks about (`field_to_fix`),
    or the interview's time or mode. At the summary a field named alone is enough ("la zona",
    the answer to "¿Qué dato quieres cambiar?"). "¿Por qué pone Cuauhtémoc?" names no field but
    their location: it is about the city."""
    wrong = field_to_fix(text, bare=state.stage == Stage.CONFIRM)
    if wrong is None:
        wrong = _why_location(state, text)
    if wrong is None:
        return None
    if wrong in (fix.value for fix in InterviewFix):
        return InterviewFix(wrong)
    return wrong


def _why_location(state: ConversationState, text: str) -> FieldId | None:
    field = agent().one(FieldType.LOCATION)
    location_id = field.value(state.profile) if field is not None else None
    rest = asks_why(text)
    if field is None or rest is None or location_id is None:
        return None
    if not rest or set(rest.split()) & LOCATION_WORDS:
        return field.id
    named = {location.location_id for location, _ in match_city(rest)}
    return field.id if location_id in named else None


def with_named_place(
    state: ConversationState, extraction: TurnExtraction, text: str
) -> TurnExtraction:
    """ "La zona es Roma…", a correction naming the field and the place, with no city read by
    the extractor (production, 2026-10-22: it read none, and the summary was asked again):
    the place is the city answer, so it is applied, or explained when it is theirs already."""
    field = agent().one(FieldType.LOCATION)
    place = named_place(text)
    if field is None or place is None or field.id in extraction.fields:
        return extraction
    fields = {**extraction.fields, field.id: FieldCandidate(value=place, confidence=0.9)}
    return extraction.model_copy(
        update={"fields": fields, "intent": Intent.CORRECTION, "question": None}
    )


def with_place_reading(
    state: ConversationState, extraction: TurnExtraction, text: str
) -> TurnExtraction:
    """A place that names one location outright ("Polanco"), typed or said in reply to the
    city question, when the extractor read nothing in it (live run, 2026-10-22: "Polanco"
    after "quiero cambiar la zona" came back empty and the question was asked again)."""
    field = agent().one(FieldType.LOCATION)
    if field is None or state.awaiting != field.id or extraction.fields:
        return extraction
    if extraction.intent not in (Intent.ANSWER, Intent.UNCLEAR, Intent.CORRECTION):
        return extraction
    result = validate_location(text)
    if not isinstance(result, Valid) or result.needs_confirmation:
        return extraction
    fields = {field.id: FieldCandidate(value=text, confidence=0.9)}
    return extraction.model_copy(update={"fields": fields, "intent": Intent.ANSWER})


def without_echoed_credential(
    fix: "FieldId | InterviewFix | None", extraction: TurnExtraction, text: str
) -> TurnExtraction:
    """ "La licencia está mal" asks to correct a credential; it is no answer to its question,
    whatever the extractor read (live run, 2026-10-22: a no at 0.9 closed the application).
    Only words that answer it ("no tengo", "la tengo vencida", "sí, el B") count."""
    if isinstance(fix, InterviewFix) or fix is None or fix not in extraction.fields:
        return extraction
    spec = agent().field(fix)
    if spec.type != FieldType.CREDENTIAL:
        return extraction
    words = spec.words
    if license_reading(text, words) is not None or license_affirmed(text, words):
        return extraction
    if license_unsure(text) or reads_as_license_answer(text, words):
        return extraction
    fields = {k: v for k, v in extraction.fields.items() if k != fix}
    return extraction.model_copy(update={"fields": fields})


def with_refusal(state: ConversationState, extraction: TurnExtraction, text: str) -> TurnExtraction:
    """ "Prefiero no decirlo" to the question on screen is a refusal of that value, whatever the
    extractor read: the engine says why it is asked, never "I didn't understand" (D5, D7)."""
    field = state.awaiting
    if extraction.declines_field or field is None or not refuses_value(text):
        return extraction
    fields = dict(extraction.fields)
    candidate = fields.get(field)
    if candidate is not None:
        # The refusal passed through as the value ("prefiero no decirlo" as the experience).
        if not isinstance(candidate.value, str) or not refuses_value(candidate.value):
            return extraction
        del fields[field]
    return extraction.model_copy(update={"declines_field": True, "fields": fields})


def cancel_reading(
    state: ConversationState, extraction: TurnExtraction, text: str
) -> tuple[TurnExtraction, bool]:
    """Whether the message asks to cancel the booked interview ("cancela la entrevista"). Such
    a request is an opt-out when, and only when, it also gives up on the job ("ya no me
    interesa, cancela la entrevista"), whatever the extractor read: a bare one read as an
    opt-out would skip the question whether to cancel or move it, and one that gives up must
    never get that question."""
    if state.booking is None or not asks_to_cancel(text):
        return extraction, False
    if gives_up(text):
        intent = Intent.OPT_OUT
    elif extraction.intent == Intent.OPT_OUT:
        intent = Intent.CORRECTION
    else:
        intent = extraction.intent
    return extraction.model_copy(update={"intent": intent}), True


def _candidate_cancelled(action: Action) -> bool:
    """The candidate dropped their interview: they cancelled it or opted out."""
    return isinstance(action, End) and action.reason in (EndReason.CANCELLED, EndReason.OPT_OUT)


def with_years_words(extraction: TurnExtraction, text: str) -> TurnExtraction:
    """Years the extractor worked out from words that are not one number ("desde 2019",
    "entre 2 y 3", "no sé" read as 0, "dos años no, tres" added up) are replaced by the words,
    for the validator to read: it asks for one number, or takes the one not taken back."""
    field = agent().one(FieldType.YEARS)
    candidate = extraction.fields.get(field.id) if field is not None else None
    if field is None or candidate is None or isinstance(candidate.value, str):
        return extraction
    if not years_need_words(text):
        return extraction
    fields = {**extraction.fields, field.id: candidate.model_copy(update={"value": text})}
    return extraction.model_copy(update={"fields": fields})


def with_email_as_typed(extraction: TurnExtraction, text: str) -> TurnExtraction:
    """The email as the candidate typed it, when the extractor fixed a mistyped domain on its
    own ("GMAIL.CON" read as gmail.com): the engine asks about the correction (D2)."""
    field = agent().channel(ContactChannel.EMAIL)
    if field is None or not field.read_back.domain_typo:
        return extraction
    candidate = extraction.fields.get(field.id)
    if candidate is None or not isinstance(candidate.value, str):
        return extraction
    extracted = candidate.value.strip().lower()
    for typed in TYPED_EMAIL.findall(text.lower()):
        if typed != extracted and correct_email_domain(typed) == extracted:
            fields = {**extraction.fields, field.id: candidate.model_copy(update={"value": typed})}
            return extraction.model_copy(update={"fields": fields})
    return extraction


def with_date_ahead(extraction: TurnExtraction, text: str, today: date) -> TurnExtraction:
    """A day and month with no year that the extractor placed this year, already gone ("el 1
    de octubre" on 19 October), is next year's: the engine reads it back with today's date
    (D6) instead of saying it has passed. "Ayer" or a date with its year stays as read."""
    field = agent().one(FieldType.DATE)
    candidate = extraction.fields.get(field.id) if field is not None else None
    if field is None or candidate is None or not isinstance(candidate.value, str):
        return extraction
    ahead = _next_year(candidate.value, text, today)
    if ahead is None:
        return extraction
    update = {"value": ahead.isoformat()}
    fields = {**extraction.fields, field.id: candidate.model_copy(update=update)}
    return extraction.model_copy(update={"fields": fields})


def _next_year(value: str, text: str, today: date) -> date | None:
    try:
        start = date.fromisoformat(value.strip())
    except ValueError:
        return None
    if start >= today or start.year != today.year or not names_day_and_month(text):
        return None
    try:
        return start.replace(year=start.year + 1)
    except ValueError:  # 29 February
        return None


def with_slot_choice(
    state: ConversationState, extraction: TurnExtraction, text: str, today: date
) -> TurnExtraction:
    """`slot_reading` without the time request."""
    return slot_reading(state, extraction, text, today)[0]


def slot_reading(
    state: ConversationState, extraction: TurnExtraction, text: str, today: date
) -> tuple[TurnExtraction, TimeRequest | None]:
    """Settle which offered slot the candidate meant: our own reading of their words first,
    the extractor's hint only when ours finds nothing in them. Words that fit several offered
    slots, name a time none has, or ask about other times never book the extractor's pick:
    the engine gets that request instead. Anything that is not an offered slot is dropped."""
    offered = {slot.id for slot in state.offered_slots}
    booked = state.booking is not None
    reading = read_choice(text, state.offered_slots, booked=booked, today=today)
    ours, request = reading.choice, reading.request
    hint = extraction.slot
    unsure = request is not None and (request.matches or request.unmatched_time)
    if ours in offered:
        choice = ours
    elif hint in offered and request is None:
        choice = hint
    else:
        signals = (CHANGE,) if booked else (NONE_WORK,)
        choice = ours if ours in signals else hint if hint in signals and not unsure else None
        if booked and choice is None and hint == NONE_WORK and not unsure:
            choice = CHANGE
    return extraction.model_copy(update={"slot": choice}), request


def widget_extraction(
    state: ConversationState, answer: WidgetAnswer
) -> tuple[TurnExtraction, str] | None:
    """Map a widget tap straight to an extraction, or None when it fits no pending widget."""
    value = answer.value
    if isinstance(value, str) and state.cancel_offered and value in (CANCEL, RESCHEDULE):
        # "¿La cancelo o prefieres otra hora?": Cancelar is a yes, Otra hora a request to move it.
        label = next(o.label for o in CANCEL_OPTIONS[state.language] if o.value == value)
        if value == CANCEL:
            return _extraction(state, Intent.CONFIRM, {}), label
        extraction = _extraction(state, Intent.ANSWER, {}).model_copy(update={"slot": CHANGE})
        return extraction, label
    if isinstance(value, str):
        slot = next((slot for slot in state.offered_slots if slot.id == value), None)
        if slot is not None:
            extraction = _extraction(state, Intent.ANSWER, {}).model_copy(update={"slot": slot.id})
            return extraction, slot.label
        mode = next((mode for mode in state.available_modes if mode.value == value), None)
        if mode is not None and _choosing_mode(state):
            extraction = _extraction(state, Intent.ANSWER, {}).model_copy(update={"mode": mode})
            return extraction, mode_label(mode, state.language)
    if isinstance(value, PhoneInput):
        dial = next(c.dial for c in DIAL_COUNTRIES if c.code == value.country)
        phone = agent().channel(ContactChannel.PHONE)
        if phone is None:
            return None
        return _extraction(state, Intent.ANSWER, {phone.id: value}), f"{dial} {value.number}"
    values = [value] if isinstance(value, str) else list(value)
    yes_no = CONFIRM_ANSWERS[state.language] if answer.kind == "confirm" else YES_NO[state.language]
    if values in (["yes"], ["no"]):
        yes = values == ["yes"]
        label = yes_no[0].label if yes else yes_no[1].label
        intent = _yes_no_intent(state, yes)
        if intent is None:
            return _extraction(state, Intent.ANSWER, {state.awaiting: yes}), label
        return _extraction(state, intent, {}), label
    field = state.awaiting
    if field is None or not values:
        return None
    labels = ", ".join(option_label(field, v, state.language) for v in values)
    multi = agent().field(field).type == FieldType.MULTI_ENUM
    raw: str | list[str] = values if multi else values[0]
    return _extraction(state, Intent.ANSWER, {field: raw}), labels


def _yes_no_intent(state: ConversationState, yes: bool) -> Intent | None:
    """None means the yes or no answers the question on screen: a credential or a yes/no."""
    if not state.consented:
        return Intent.CONFIRM if yes else Intent.OPT_OUT
    if state.stage == Stage.DONE or state.pause_offered:
        return Intent.CONFIRM if yes else Intent.SMALL_TALK
    if state.pending_identity is not None:  # "¿Quieres continuar como Lucía?" (D10)
        return Intent.CONFIRM if yes else Intent.CORRECTION
    if state.pending_confirmation is not None or state.stage == Stage.CONFIRM:
        return Intent.CONFIRM if yes else Intent.CORRECTION
    if state.awaiting is not None and agent().field(state.awaiting).type in YES_NO_TYPES:
        return None
    return Intent.CONFIRM if yes else Intent.SMALL_TALK


def _extraction(state: ConversationState, intent: Intent, fields: dict[Any, Any]) -> TurnExtraction:
    language = state.language
    return TurnExtraction(
        intent=intent,
        fields={str(name): FieldCandidate(value=v, confidence=1.0) for name, v in fields.items()},
        language=language,
        matrix_language=MatrixLanguage(language.value),
        english_ratio=1.0 if language == Language.EN else 0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
    )


def _widget_text(answer: WidgetAnswer) -> str:
    value = answer.value
    if isinstance(value, PhoneInput):
        return value.number
    return value if isinstance(value, str) else ", ".join(value)


def without_recovered_insult(turns: Sequence[Turn]) -> list[Turn]:
    """The turns without an insult and its calm line when the candidate wrote again after
    them. An insult left unanswered stays; so do both when a second one closed the chat, which
    the caller rules out by not calling this."""
    dropped: set[int] = set()
    for i, turn in enumerate(turns):
        if turn.action is None or _kind(turn.action) != "de_escalate" or i == 0:
            continue
        insult = turns[i - 1]
        answered = any(later.role == TurnRole.CANDIDATE for later in turns[i + 1 :])
        if answered and (insult.extraction or {}).get("intent") == Intent.ABUSE.value:
            dropped.update((i - 1, i))
    return [turn for i, turn in enumerate(turns) if i not in dropped]


def _kind(action: dict[str, Any]) -> str | None:
    """A stored action's kind; a welcome back's is its follow-up's (new times, say)."""
    if action.get("kind") == "welcome_back":
        return (action.get("follow_up") or {}).get("kind")
    return action.get("kind")


def _current_prompt(state: ConversationState) -> Action:
    """The question the candidate is looking at, to re-send it unchanged."""
    prompt = current_prompt(state)
    if prompt is not None:
        return prompt
    if state.stage == Stage.DONE:
        return End(reason=EndReason.CLOSED, language=state.language, country=state.country)
    return re_engage(state, attempt=0)[1]


def _count_outcome(before: str | None, after: Outcome | None) -> None:
    if after is not None and before != after:
        metrics.CONVERSATION_OUTCOMES.labels(after.value).inc()


def _annotate_call(current: telemetry.Span, call: CallRecord) -> None:
    current.set_attribute("sazon.model", call.model)
    current.set_attribute("sazon.fallback_used", call.fallback_used)
    current.set_attribute("sazon.latency_ms", call.latency_ms)
    if call.cost_usd is not None:
        current.set_attribute("sazon.cost_usd", call.cost_usd)
    if call.error:
        current.set_attribute("sazon.error", call.error)


def _template(
    action: Action, state: ConversationState, now: datetime, channel: Channel = Channel.WEB
) -> str:
    ctx = ReplyContext.for_action(action, today=local_today(state, now), channel=channel)
    return fallback_reply(action, ctx)


def _known_location(location_id: object) -> str | None:
    """A posting's location id, when it names a location we have (the page sends a free hint)."""
    if isinstance(location_id, str) and get_location(location_id) is not None:
        return location_id
    return None


def _dump(state: ConversationState) -> dict[str, Any]:
    return state.model_dump(mode="json")


def _faq_parts(found: Sequence[Part]) -> tuple[FaqPart, ...]:
    """What the responder reads of a question that asks several things; nothing for one."""
    if len(found) < 2:
        return ()
    return tuple(
        FaqPart(question=part.question, snippets=tuple(s.text for s in part.snippets))
        for part in found
    )


def _pii(state: ConversationState) -> tuple[str, ...]:
    profile = state.profile
    values = [profile.phone, profile.email, profile.full_name]
    if profile.full_name:
        values.extend(part for part in profile.full_name.split() if len(part) >= MIN_NAME_PART)
    return tuple(value for value in values if value)


def _extracted_pii(extraction: TurnExtraction) -> tuple[str, ...]:
    """Name, phone and email as the extractor read them, valid or not."""
    values: list[str] = []
    for field in agent().fields:
        if field.type not in CONTACT_TYPES:
            continue
        candidate = extraction.fields.get(field.id)
        if candidate is None or not isinstance(candidate.value, str):
            continue
        values.append(candidate.value)
        if field.type == FieldType.PERSON_NAME:
            values.extend(part for part in candidate.value.split() if len(part) >= MIN_NAME_PART)
    return tuple(values)


def _log_failure(exc: Exception, conversation_id: str | None, known: Iterable[str]) -> None:
    detail = "".join(traceback.format_exception(exc))
    logger.error(
        "Screening turn failed (conversation %s): %s", conversation_id, redact(detail, known)
    )


# --- export ---


def profile_export(state: ConversationState) -> dict[str, Any]:
    """The candidate profile with the matched location spelled out."""
    profile = state.profile
    data = profile.model_dump(mode="json")
    location = get_location(profile.location_id) if profile.location_id else None
    data["city"] = location.city if location else None
    data["location_name"] = location.name if location else None
    return data


def export_conversation(repo: Repo, conversation_id: str) -> dict[str, Any]:
    conversation = repo.get_conversation(conversation_id)
    if conversation is None:
        raise LookupError(f"No conversation {conversation_id}")
    state = state_of(conversation)
    turns = repo.turns(conversation_id)
    summary = repo.summary(conversation_id)
    costs = [turn.cost_usd for turn in turns if turn.cost_usd is not None]
    if summary is not None and summary.cost_usd is not None:
        costs.append(summary.cost_usd)
    return {
        "conversation": {
            "id": conversation.id,
            "candidate_id": conversation.candidate_id,
            "channel": conversation.channel,
            "outcome": conversation.outcome,
            "disqualify_reason": state.disqualify_reason,
            "quality_flags": conversation.quality_flags,
            "drop_off_stage": conversation.drop_off_stage,
            "context": conversation.context,
            "started_at": conversation.started_at.isoformat(),
            "ended_at": conversation.ended_at.isoformat() if conversation.ended_at else None,
            "cost_usd": round(sum(costs), 6),
        },
        "profile": profile_export(state),
        "summary": summary.body if summary else None,
        "faq_misses": [miss.question for miss in repo.faq_misses(conversation_id)],
        "transcript": [turn.model_dump(mode="json", exclude={"conversation_id"}) for turn in turns],
    }
