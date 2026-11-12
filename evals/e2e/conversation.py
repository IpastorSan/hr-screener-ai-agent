"""Drive one persona through the real `ScreeningService`, in process.

Each conversation gets its own in-memory SQLite database with interview slots seeded at the
persona's location, the shared RAG index, and a clock fixed at `EVAL_NOW` that moves 20 seconds
per turn. The product's LLM is wrapped by `MeteredLLM`, which settles every call in the eval
ledger and remembers per-call latency and cost.
"""

import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlmodel import col, select

from evals.e2e.personas import EVAL_NOW, OutcomeLabel, Persona, ScriptedMessage
from evals.e2e.simulator import (
    MAX_TURNS,
    STOP_ACTIONS,
    CandidateTurn,
    Simulator,
    Speaker,
    to_message,
)
from evals.model_selection.client import BudgetExceededError, EvalClient, ModelSpec
from sazon.calendar import Interval
from sazon.calendar.fake import FakeCalendar
from sazon.engine.actions import Action
from sazon.engine.models import (
    CandidateProfile,
    FieldCandidate,
    Intent,
    Language,
    MatrixLanguage,
    PhoneInput,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.service_areas import get_location
from sazon.engine.state import ConversationState, Qualified
from sazon.engine.validators import Country
from sazon.llm import LLM, CallRecord, ChatTurn, ExtractResult, ReplyContext, ReplyResult
from sazon.llm.fake import FakeLLM
from sazon.llm.interface import Channel, Role, SummaryResult
from sazon.rag.index import Retriever
from sazon.recruiters import seed_team
from sazon.scheduling.service import SchedulingConfig
from sazon.service import ScreeningService, WidgetAnswer, export_conversation
from sazon.storage.models import InterviewSlot, Turn, TurnRole
from sazon.storage.repo import Repo, init_db, make_engine

TURN_STEP = timedelta(seconds=20)
PRIOR_APPLICATION_AGE = timedelta(days=2)


class FatalRunError(RuntimeError):
    """The run must stop: spend ceiling or ZDR violation (the service swallowed it)."""


# --- the metered product LLM ---


@dataclass
class CallLog:
    role: str
    model: str
    latency_ms: int
    cost_usd: float
    fallback_used: bool
    error: str | None = None


@dataclass
class MeteredLLM:
    """The product LLM with every call settled in the eval ledger (when there is a client).

    The service turns any exception into a template reply, so a budget or ZDR stop is kept in
    `fatal` and the driver raises it after the turn.
    """

    inner: LLM
    client: EvalClient | None = None
    specs: Mapping[Role, ModelSpec] = field(default_factory=dict)
    case_id: str = ""
    guard: Callable[[], None] = lambda: None
    calls: list[CallLog] = field(default_factory=list)
    fatal: BaseException | None = None

    @property
    def cost_usd(self) -> float:
        return sum(call.cost_usd for call in self.calls)

    @property
    def summarize_ms(self) -> int:
        return sum(call.latency_ms for call in self.calls if call.role == Role.SUMMARIZER)

    async def extract(
        self,
        history: Sequence[ChatTurn],
        state: ConversationState,
        message: str,
        *,
        today: date | None = None,
    ) -> ExtractResult:
        reserved = self._reserve(Role.EXTRACTOR)
        result = await self.inner.extract(history, state, message, today=today)
        await self._settle(Role.EXTRACTOR, reserved, result.call)
        return result

    async def respond(self, action: Action, ctx: ReplyContext) -> ReplyResult:
        reserved = self._reserve(Role.RESPONDER)
        result = await self.inner.respond(action, ctx)
        await self._settle(Role.RESPONDER, reserved, result.call)
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
        reserved = self._reserve(Role.SUMMARIZER)
        result = await self.inner.summarize(
            transcript, profile, language=language, country=country, today=today
        )
        await self._settle(Role.SUMMARIZER, reserved, result.call)
        return result

    def _reserve(self, role: Role) -> float:
        if self.client is None:
            return 0.0
        try:
            self.guard()
            amount = 0.02
            self.client.ledger.reserve(amount)
            return amount
        except BudgetExceededError as exc:
            self.fatal = self.fatal or exc
            raise

    async def _settle(self, role: Role, reserved: float, call: CallRecord) -> None:
        cost = call.cost_usd or 0.0
        if self.client is not None:
            spec = self.specs.get(role) or ModelSpec(call.model)
            record = await self.client._record_call(spec, self.case_id, call)  # pyright: ignore[reportPrivateUsage]
            self.client.ledger.settle(reserved, record, self.client.run_id)
            cost = record.cost_usd
            try:
                self.client._check_zdr()  # pyright: ignore[reportPrivateUsage]
            except Exception as exc:
                self.fatal = self.fatal or exc
                raise
        self.calls.append(
            CallLog(
                role=role.value,
                model=call.model,
                latency_ms=call.latency_ms,
                cost_usd=cost,
                fallback_used=call.fallback_used,
                error=call.error,
            )
        )


# --- the driver ---


@dataclass
class BookedOutCalendar(FakeCalendar):
    """Every recruiter is busy for the whole of any window asked about. Remembers the furthest
    point searched, so a run can tell how far ahead the service looked for a free time."""

    searched_until: datetime | None = None

    def busy(
        self, recruiter_ids: Collection[str], start: datetime, end: datetime
    ) -> Mapping[str, list[Interval]]:
        self.calls.append("busy")
        if self.searched_until is None or end > self.searched_until:
            self.searched_until = end
        return {rid: [Interval(start, end)] for rid in self.connected(recruiter_ids)}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def tick(self) -> None:
        self.now += TURN_STEP


def _extraction(intent: Intent, language: Language, **fields: Any) -> TurnExtraction:
    return TurnExtraction(
        intent=intent,
        fields={k: FieldCandidate(value=v, confidence=1.0) for k, v in fields.items()},
        language=language,
        matrix_language=MatrixLanguage(language.value),
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
    )


async def seed_prior_application(repo: Repo, persona: Persona, faq: Retriever) -> str:
    """An application started two days ago and left at the availability question."""
    truth = persona.truth
    language = Language.EN if persona.formality == "en" else Language.ES
    location = get_location(str(truth["location_id"]))
    llm = FakeLLM(
        [
            _extraction(Intent.CONFIRM, language),
            _extraction(
                Intent.ANSWER,
                language,
                full_name=truth["full_name"],
                phone=truth["phone"],
                email=truth["email"],
            ),
            _extraction(Intent.ANSWER, language, has_license=True),
            _extraction(
                Intent.ANSWER,
                language,
                city=location.name.removeprefix("Sazón ") if location else "",
            ),
        ]
    )
    clock = Clock(EVAL_NOW - PRIOR_APPLICATION_AGE)
    service = ScreeningService(llm, repo, clock=clock, faq=faq)
    reply = await service.start(Channel.WEB, persona.context)
    for _ in range(4):
        clock.tick()
        reply = await service.handle_turn(reply.conversation_id, "...")
    return reply.conversation_id


def _new_turns(repo: Repo, after: int) -> list[Turn]:
    with repo.session() as session:
        query = select(Turn).where(col(Turn.id) > after).order_by(col(Turn.id))
        return list(session.exec(query).all())


def _slot_ids(repo: Repo) -> set[str]:
    with repo.session() as session:
        return set(session.exec(select(InterviewSlot.id)).all())


def _pending(action: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if action is None:
        return None
    return action.get("follow_up") or action


def _scripted(
    script: ScriptedMessage | None, action: Mapping[str, Any] | None, turn_no: int
) -> bool:
    if script is None:
        return False
    kind, _, target = script.when.partition(":")
    if kind == "turn":
        return turn_no == int(target)
    prompt = _pending(action) or {}
    return prompt.get("kind") in ("ask_field", "clarify") and prompt.get("field") == target


def _shown(message: str | WidgetAnswer, ui: Mapping[str, Any] | None) -> str:
    """The candidate's bubble for a tap, when the service stored nothing (before consent)."""
    if isinstance(message, str):
        return message
    value = message.value
    if isinstance(value, PhoneInput):
        return value.number
    labels: dict[str, str] = {}
    if ui and ui.get("kind") in ("quick_replies", "multi_select"):
        labels = {o["value"]: o["label"] for o in ui["options"]}
    values = [value] if isinstance(value, str) else list(value)
    return ", ".join(labels.get(v, v) for v in values)


def _offered_slots(turns: Sequence[Mapping[str, Any]]) -> list[str]:
    ids: list[str] = []
    for turn in turns:
        if turn["speaker"] != "ana":
            continue
        ui = turn.get("ui") or {}
        if ui.get("kind") == "slots":
            ids.extend(slot["id"] for slot in ui["slots"])
        action = turn.get("action") or {}
        for nested in (action, action.get("follow_up") or {}):
            ids.extend(slot["id"] for slot in nested.get("slots") or ())
    return list(dict.fromkeys(ids))


def outcome_label(state: ConversationState, last_action: Mapping[str, Any] | None) -> OutcomeLabel:
    if state.outcome is not None:
        return state.outcome.value  # pyright: ignore[reportReturnType]
    if last_action and last_action.get("kind") == "end" and last_action.get("reason") == "paused":
        return "paused"
    return "in_progress"


async def run_conversation(
    persona: Persona,
    llm: MeteredLLM,
    simulator: Simulator,
    faq: Retriever,
) -> dict[str, Any]:
    """One full conversation. Returns the transcript, the final profile and the telemetry."""
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    seed_team(repo, EVAL_NOW, with_passwords=False)  # interviewers for every location

    prior_id: str | None = None
    if persona.prior_application:
        prior_id = await seed_prior_application(repo, persona, faq)

    clock = Clock(EVAL_NOW)
    # A fake calendar: video interviews get a Meet link, as with Google Calendar.
    booked_out = persona.interviewers == "booked_out"
    calendar = BookedOutCalendar() if booked_out else FakeCalendar()
    # Booked out: no organizer for in-person invitations either, so nobody can interview.
    scheduling = SchedulingConfig(calendar=calendar, organizer_required=booked_out)
    service = ScreeningService(llm, repo, clock=clock, faq=faq, scheduling_config=scheduling)
    last_id = max((t.id or 0 for t in _new_turns(repo, 0)), default=0)

    started = time.perf_counter()
    reply = await service.start(Channel.WEB, persona.context)
    start_latency = time.perf_counter() - started
    turns: list[dict[str, Any]] = []
    fallbacks = 0

    def record_ana(reply_text: str, ui: Any, new: Sequence[Turn]) -> dict[str, Any]:
        agent = [t for t in new if t.role == TurnRole.AGENT and t.text is not None]
        match = next((t for t in reversed(agent) if t.text == reply_text), None)
        match = match or (agent[-1] if agent else None)
        entry = {
            "speaker": "ana",
            "text": reply_text,
            "ui": ui.model_dump(mode="json") if ui is not None else None,
            "action": match.action if match else None,
            "fallback_used": bool(match and match.fallback_used),
            "sources": match.sources if match else None,
        }
        turns.append(entry)
        return entry

    new = _new_turns(repo, last_id)
    last_id = max((t.id or 0 for t in new), default=last_id)
    fallbacks += sum(t.fallback_used for t in new)
    ana = record_ana(reply.text, reply.ui, new)
    ana["latency_s"] = round(start_latency, 3)

    conversation_id = reply.conversation_id
    scripts = list(persona.scripted)
    hit_cap = False
    error: str | None = None
    candidate_turns = 0
    while True:
        action = ana.get("action")
        if (action or {}).get("kind") in STOP_ACTIONS or reply.ended:
            break
        if candidate_turns >= MAX_TURNS:
            hit_cap = True
            break
        transcript: list[tuple[Speaker, str]] = [
            ("ana" if t["speaker"] == "ana" else "candidate", t["text"]) for t in turns
        ]
        script = scripts[0] if scripts else None
        if _scripted(script, action, candidate_turns + 1) and script is not None:
            scripts.pop(0)
            move = CandidateTurn(
                kind="text",
                text=script.message,
                matrix_language=script.matrix_language,
                explicit_language_request=script.explicit_language_request,
            )
            scripted = True
        else:
            try:
                move = await simulator.next_turn(persona, transcript, ana.get("ui"), action)
            except BudgetExceededError:
                raise
            except Exception as exc:
                error = f"simulator: {type(exc).__name__}: {exc}"[:300]
                break
            scripted = False
        if move.kind == "stop":
            break
        message = to_message(move, ana.get("ui"))
        clock.tick()
        summarize_before = llm.summarize_ms
        started = time.perf_counter()
        reply = await service.handle_turn(conversation_id, message)
        wall = time.perf_counter() - started
        if llm.fatal is not None:
            raise FatalRunError(str(llm.fatal)) from llm.fatal
        candidate_turns += 1
        new = _new_turns(repo, last_id)
        last_id = max((t.id or 0 for t in new), default=last_id)
        fallbacks += sum(t.fallback_used for t in new)
        stored = next((t for t in new if t.role == TurnRole.CANDIDATE), None)
        turns.append(
            {
                "speaker": "candidate",
                "text": reply.candidate_text or _shown(message, ana.get("ui")),
                "sent": message if isinstance(message, str) else None,
                "widget": not isinstance(message, str),
                "scripted": scripted,
                "move": move.model_dump(mode="json", exclude_none=True),
                "extraction": stored.extraction if stored else None,
                "latency_s": round(wall - (llm.summarize_ms - summarize_before) / 1000, 3),
            }
        )
        conversation_id = reply.conversation_id or conversation_id
        ana = record_ana(reply.text, reply.ui, new)

    exported = export_conversation(repo, conversation_id)
    conversation = repo.get_conversation(conversation_id)
    state = ConversationState.model_validate(conversation.state if conversation else {})
    offered = _offered_slots(turns)
    existing = _slot_ids(repo)
    summary_fallback = any(c.fallback_used for c in llm.calls if c.role == Role.SUMMARIZER)
    searched_until = calendar.searched_until if isinstance(calendar, BookedOutCalendar) else None
    return {
        "persona": persona.id,
        "conversation_id": conversation_id,
        "resumed": prior_id is not None and conversation_id == prior_id,
        "outcome": outcome_label(state, ana.get("action")),
        "disqualify_reason": state.disqualify_reason,
        "booked": state.booking is not None,
        "booking": state.booking.label if state.booking else None,
        "interview_mode": state.booking.mode.value
        if state.booking and state.booking.mode
        else None,
        "meet_url": state.booking.meet_url if state.booking else None,
        "talent_pool_opt_in": state.talent_pool_opt_in,
        "slots_declined": isinstance(state.closed, Qualified) and state.closed.slots_declined,
        "slot_search_days": (searched_until - EVAL_NOW).days if searched_until else None,
        "quality_flags": [flag.value for flag in state.quality_flags],
        "abuse_strikes": state.abuse_strikes,
        "final_language": state.language.value,
        "country": state.country,
        "profile": exported["profile"],
        "summary": exported["summary"],
        "faq_misses": exported["faq_misses"],
        "turns": turns,
        "candidate_turns": candidate_turns,
        "hit_turn_cap": hit_cap,
        "error": error,
        "offered_slots": offered,
        "invented_slots": [slot for slot in offered if slot not in existing],
        "fallback_count": fallbacks + summary_fallback,
        "cost_usd": round(llm.cost_usd, 6),
        "simulator_cost_usd": 0.0,
        "calls": [vars(call) for call in llm.calls],
    }
