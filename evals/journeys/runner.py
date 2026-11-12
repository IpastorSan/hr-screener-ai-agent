"""Play one journey against the real `ScreeningService`, in process, and check every step.

The service runs as in the e2e evals (`evals.e2e.conversation`): its own SQLite database
(in memory, or a file the caller owns), the interviewers seeded for every location, a fake
calendar (no Google calendar, no invitations sent), and a clock fixed at `EVAL_NOW` that moves
20 seconds per candidate turn and jumps by a session's `after`. Sessions share the database,
so the same phone or email in a later session meets the duplicate check.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError
from sqlmodel import col, select

from evals.e2e.conversation import Clock, FatalRunError, MeteredLLM, outcome_label
from evals.e2e.personas import EVAL_NOW, Persona
from evals.e2e.personas import Expect as PersonaExpect
from evals.e2e.simulator import STOP_ACTIONS, Simulator, Speaker, to_message, widget_values
from evals.journeys.model import (
    BUSY_WINDOW,
    Expect,
    Journey,
    PhoneTap,
    Register,
    Scheduling,
    Session,
    Step,
)
from evals.model_selection.client import BudgetExceededError
from evals.model_selection.scoring import detect_language
from sazon import recruiters
from sazon.calendar.fake import FakeCalendar
from sazon.engine.actions import Action, AnswerQuestion
from sazon.engine.models import Language, PhoneInput
from sazon.engine.spec import agent
from sazon.engine.state import ConversationState
from sazon.llm import ReplyContext
from sazon.llm.fallback_templates import fallback_reply, is_fixed
from sazon.llm.interface import Channel
from sazon.llm.reply_checks import internal_location_name, register_slip, reply_problem
from sazon.rag.index import Retriever
from sazon.recruiters import seed_team
from sazon.scheduling import service as scheduling
from sazon.scheduling.service import SchedulingConfig
from sazon.service import Reply, ScreeningService, WidgetAnswer, local_today
from sazon.storage.models import Recruiter, Turn, TurnRole
from sazon.storage.repo import Repo, init_db, make_engine

ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)  # pyright: ignore[reportArgumentType]
PROMPT_NAMES = {
    "greet": "consent",
    "confirm_summary": "confirm",
    "ask_mode": "mode",
    "offer_slots": "slot",
    "offer_pause": "pause",
    "ask_cancel": "cancel",
    "confirm_move": "move",
}
SLOT_TAP = re.compile(r"^slot:(\d+)$")
INVARIANTS = ("reply_checks", "register", "one_question", "no_internal_location")


@dataclass
class Findings:
    """What a run of one journey found: failed checks, in the order they happened."""

    failures: list[dict[str, Any]] = field(default_factory=list)
    skipped: int = 0
    checked: int = 0


@dataclass
class _Session:
    index: int
    started_id: str
    conversation_id: str
    reply: Reply
    action: dict[str, Any] | None
    sources: list[dict[str, Any]] | None


class JourneyRun:
    def __init__(
        self,
        journey: Journey,
        llm: MeteredLLM,
        simulator: Simulator,
        faq: Retriever,
        *,
        live: bool,
        db_url: str = "sqlite://",
    ) -> None:
        self.journey = journey
        self.llm = llm
        self.simulator = simulator
        self.live = live
        self.repo = Repo(make_engine(db_url))
        init_db(self.repo.engine)
        seed_team(self.repo, EVAL_NOW, with_passwords=False)  # the agent's interviewers
        self.clock = Clock(EVAL_NOW)
        self.service = ScreeningService(
            llm,
            self.repo,
            clock=self.clock,
            faq=faq,
            scheduling_config=scheduling_world(self.repo, journey.scheduling),
        )
        self.turns: list[dict[str, Any]] = []
        self.session: Session = journey.sessions[0]
        self.findings = Findings()
        self.last_turn_id = 0
        self.error: str | None = None

    # --- driving ---

    async def run(self) -> dict[str, Any]:
        try:
            current: _Session | None = None
            for index, session in enumerate(self.journey.sessions, start=1):
                self.clock.now += session.advance
                self.session = session
                previous = current.conversation_id if current and session.restart else None
                current = await self._open(index, previous)
                for number, step in enumerate(session.steps, start=1):
                    where = {"session": index, "step": number}
                    if step.persona is not None:
                        await self._persona(current, step, where)
                    else:
                        await self._send(current, step, where)
                    if step.expect is not None:
                        self._expect(current, step.expect, where)
        except (BudgetExceededError, FatalRunError):
            raise
        except Exception as exc:  # a harness bug or a crash: reported, not raised
            self.error = f"{type(exc).__name__}: {exc}"[:500]
            self.findings.failures.append({"check": "error", "detail": self.error})
        finally:
            self.repo.engine.dispose()
        return self.result()

    async def _open(self, index: int, restart_of: str | None = None) -> _Session:
        """A new chat; `restart_of`: through that chat's restart button, as the web does it."""
        if restart_of is not None:
            await self.service.restart(restart_of)
        context = self.journey.session_context(self.session)
        reply = await self.service.start(Channel(self.journey.channel), context)
        current = _Session(index, reply.conversation_id, reply.conversation_id, reply, None, None)
        self._record_ana(current, {"session": index, "step": 0})
        return current

    async def _send(self, current: _Session, step: Step, where: dict[str, Any]) -> None:
        message = self._message(current, step, where)
        await self._turn(current, message, where, scripted=True)

    def _message(self, current: _Session, step: Step, where: dict[str, Any]) -> str | WidgetAnswer:
        if step.say is not None:
            return step.offline_say if not self.live and step.offline_say else step.say
        ui = _ui(current.reply)
        tap = step.tap
        if isinstance(tap, PhoneTap):
            return WidgetAnswer(
                value=PhoneInput(country=tap.country, number=tap.number), kind="phone"
            )
        if isinstance(tap, list):
            self._offered(current, tap, where)
            return WidgetAnswer(value=list(tap), kind="multi_select")
        assert tap is not None
        if match := SLOT_TAP.match(tap):
            slots = (ui or {}).get("slots") or []
            n = int(match.group(1))
            if ui is None or ui.get("kind") != "slots" or not 1 <= n <= len(slots):
                problem = f"{tap}: {len(slots)} slots offered"
                self._fail(where, "tap", problem, current.reply.text)
                return tap  # typed instead, as a candidate with no widget would
            return WidgetAnswer(value=slots[n - 1]["id"], kind="slots")
        self._offered(current, [tap], where)
        kind = (ui or {}).get("kind")
        return WidgetAnswer(value=tap, kind=kind if kind in ("quick_replies", "confirm") else None)

    def _offered(self, current: _Session, values: Sequence[str], where: dict[str, Any]) -> None:
        """A tap on a value the widget does not show: the web could not send it."""
        ui = _ui(current.reply)
        missing = [v for v in values if v not in widget_values(ui)]
        if missing:
            shown = (ui or {}).get("kind") or "no widget"
            problem = f"{', '.join(missing)} not offered ({shown})"
            self._fail(where, "tap", problem, current.reply.text)

    async def _persona(self, current: _Session, step: Step, where: dict[str, Any]) -> None:
        assert step.until is not None
        assert step.persona is not None
        persona = self._persona_card(step)
        for _ in range(step.max_turns):
            if not self._mismatches(current, step.until):
                return
            action = current.action or {}
            if action.get("kind") in STOP_ACTIONS or current.reply.ended:
                break
            transcript: list[tuple[Speaker, str]] = [
                (t["speaker"], t["text"] or "")
                for t in self.turns
                if t["session"] == current.index and t["speaker"] in ("ana", "candidate")
            ]
            move = await self.simulator.next_turn(persona, transcript, _ui(current.reply), action)
            if move.kind == "stop":
                break
            message = to_message(move, _ui(current.reply))
            await self._turn(current, message, where, scripted=False)
        problems = self._mismatches(current, step.until)
        if problems:
            self._fail(where, "until", "; ".join(problems), current.reply.text)

    def _persona_card(self, step: Step) -> Persona:
        candidate = self.journey.candidate
        truth = {**(candidate.get("truth") or {}), **(step.facts or {})}
        return Persona(
            id=self.journey.id,
            title=self.journey.description,
            country=self.journey.country,
            formality=self.journey.expected_register,
            context=self.journey.chat_context,
            truth=truth,
            behaviour=str(step.persona),
            language=str(candidate.get("language", "Spanish")),
            phone_via=candidate.get("phone_via", "text"),
            expect=PersonaExpect(outcome="in_progress"),
        )

    async def _turn(
        self,
        current: _Session,
        message: str | WidgetAnswer,
        where: dict[str, Any],
        *,
        scripted: bool,
    ) -> None:
        self.clock.tick()
        reply = await self.service.handle_turn(current.conversation_id, message)
        if self.llm.fatal is not None:
            raise FatalRunError(str(self.llm.fatal)) from self.llm.fatal
        new = self._new_turns()
        stored = next((t for t in new if t.role == TurnRole.CANDIDATE), None)
        self.turns.append(
            {
                **where,
                "speaker": "candidate",
                "text": reply.candidate_text or _shown(message),
                "sent": message if isinstance(message, str) else message.model_dump(mode="json"),
                "scripted": scripted,
                "extraction": stored.extraction if stored else None,
            }
        )
        current.reply = reply
        current.conversation_id = reply.conversation_id or current.conversation_id
        self._record_ana(current, where, new)

    def _new_turns(self) -> list[Turn]:
        with self.repo.session() as session:
            query = select(Turn).where(col(Turn.id) > self.last_turn_id).order_by(col(Turn.id))
            turns = list(session.exec(query).all())
        self.last_turn_id = max((t.id or 0 for t in turns), default=self.last_turn_id)
        return turns

    def _record_ana(
        self, current: _Session, where: dict[str, Any], new: Sequence[Turn] | None = None
    ) -> None:
        new = self._new_turns() if new is None else new
        reply = current.reply
        agent = [t for t in new if t.role == TurnRole.AGENT and t.text is not None]
        match = next((t for t in reversed(agent) if t.text == reply.text), None)
        match = match or (agent[-1] if agent else None)
        current.action = match.action if match else None
        current.sources = match.sources if match else None
        state = self._state(current)
        entry = {
            **where,
            "speaker": "ana",
            "text": reply.text,
            "ui": _ui(reply),
            "action": current.action,
            "sources": current.sources,
            "fallback_used": bool(match and match.fallback_used),
            "ended": reply.ended,
            "conversation_id": current.conversation_id,
            "resumed": current.conversation_id != current.started_id,
            "state": snapshot(state),
        }
        entry["invariants"] = self._invariants(entry, state, where)
        self.turns.append(entry)

    def _state(self, current: _Session) -> ConversationState:
        conversation = self.repo.get_conversation(current.conversation_id)
        return ConversationState.model_validate(conversation.state if conversation else {})

    def _interviewer(self, current: _Session) -> str | None:
        """Who runs the booked interview: the recruiter by video, the manager in person."""
        conversation = self.repo.get_conversation(current.conversation_id)
        candidate = conversation.candidate_id if conversation else None
        booking = scheduling.active_booking(self.repo, candidate) if candidate else None
        slot = scheduling.get_slot(self.repo, booking.slot_id) if booking else None
        if booking is None or slot is None:
            return None
        recruiter_id, manager = scheduling.who_runs(booking, slot)
        found = recruiters.get(self.repo, recruiter_id) if recruiter_id else None
        return found.name if found else manager.name if manager else None

    # --- checks ---

    def _fail(self, where: Mapping[str, Any], check: str, problem: str, reply: str) -> None:
        self.findings.failures.append({**where, "check": check, "problem": problem, "reply": reply})

    def _invariants(
        self, entry: Mapping[str, Any], state: ConversationState, where: Mapping[str, Any]
    ) -> dict[str, str | None]:
        """Every Ana reply: the product's reply checks, the journey's register, one question,
        no internal location name. Values are None (passed) or the problem."""
        text = entry["text"] or ""
        stored = entry["action"]
        problems: dict[str, str | None] = dict.fromkeys(INVARIANTS)
        action = _parse_action(stored)
        if action is not None and not is_fixed(action):  # fixed replies are templates
            today = local_today(state, self.clock.now)
            channel = Channel(self.journey.channel)
            reference = _reference(action, today, channel)
            problems["reply_checks"] = reply_problem(
                _without_overview(text),
                action.language,
                action=action,
                reference=reference,
                voice=channel == Channel.VOICE,
            )
        register = self.journey.session_register(self.session)
        if register != "mixed":
            problems["register"] = register_problem(text, register, _language(entry))
        if text.count("?") > 1:
            problems["one_question"] = f"{text.count('?')} questions"
        if not isinstance(action, AnswerQuestion) and (name := internal_location_name(text)):
            problems["no_internal_location"] = f"names '{name}'"
        for check, problem in problems.items():
            if problem is not None:
                self._fail(where, check, problem, text)
        return problems

    def _expect(self, current: _Session, expect: Expect, where: dict[str, Any]) -> None:
        for problem in self._mismatches(current, expect, record=True):
            check, _, detail = problem.partition(": ")
            self._fail(where, check, detail, current.reply.text)

    def _mismatches(self, current: _Session, expect: Expect, *, record: bool = False) -> list[str]:
        """Each expectation that does not hold, as `check: expected X, got Y`."""
        observed = self.observe(current)
        problems: list[str] = []
        for name, want, live_only in _expectations(expect):
            if live_only and not self.live:
                self.findings.skipped += record
                continue
            self.findings.checked += record
            if problem := _compare(name, want, observed):
                problems.append(f"{name}: {problem}")
        return problems

    def observe(self, current: _Session) -> dict[str, Any]:
        """What each expectation is checked against, after the last reply."""
        action = current.action or {}
        state = self._state(current)
        prompt = action.get("follow_up") or action
        kind = prompt.get("kind")
        asks = prompt.get("field") or PROMPT_NAMES.get(str(kind))
        if kind == "disqualify":
            asks = "talent_pool" if prompt.get("talent_pool_offer") else None
        faq = [s["source"].removeprefix("faq:") for s in current.sources or () if _is_faq(s)]
        booking = state.booking
        mode = booking.mode if booking and booking.mode else state.profile.interview_mode
        ui = _ui(current.reply)
        return {
            "asks": asks or "none",
            "action": action.get("kind"),
            "outcome": outcome_label(state, action),
            "reason": state.disqualify_reason or action.get("reason"),
            "text": current.reply.text,
            "language": _language({"text": current.reply.text, "action": action}),
            "action_language": action.get("language"),
            "booked": booking is not None,
            "mode": mode.value if mode else None,
            "interviewer": self._interviewer(current) if booking else None,
            "resumed": current.conversation_id != current.started_id,
            "faq": faq,
            "closed": current.reply.ended,
            "ui": (ui or {}).get("kind") or "none",
            "card": "; ".join(
                f"{row['label']}: {row['value']}" for row in (ui or {}).get("fields", [])
            ),
        }

    def result(self) -> dict[str, Any]:
        failures = self.findings.failures
        passed = not failures
        status = "pass" if passed else "fail"
        if self.journey.xfail:
            status = "xpass" if passed else "xfail"
        return {
            "id": self.journey.id,
            "group": self.journey.group,
            "description": self.journey.description,
            "xfail": self.journey.xfail,
            "status": status,
            "passed": passed,
            "error": self.error,
            "first_failure": failures[0] if failures else None,
            "failures": failures,
            "expectations_checked": self.findings.checked,
            "expectations_skipped_offline": self.findings.skipped,
            "candidate_turns": sum(t["speaker"] == "candidate" for t in self.turns),
            "fallback_count": sum(bool(t.get("fallback_used")) for t in self.turns),
            "cost_usd": round(self.llm.cost_usd, 6),
            "simulator_cost_usd": round(getattr(self.simulator, "cost_usd", 0.0), 6),
            "turns": self.turns,
        }


# --- comparison helpers ---


BUSY_ALL_DAYS = 60
"""`busy: all` fills the recruiters' calendars this many days from the eval clock."""


def scheduling_world(repo: Repo, setup: Scheduling) -> SchedulingConfig:
    """The fake calendar and scheduling config for a journey's `scheduling` card: without
    video every recruiter is switched off; without in person an organizer is required and
    there is none; busy days fill every recruiter's calendar, in their own time zone."""
    calendar = FakeCalendar()
    config = SchedulingConfig(calendar=calendar)
    if "onsite" not in setup.modes:
        config = replace(config, organizer_required=True, organizer_email=None)
    with repo.session() as session:
        team = list(session.exec(select(Recruiter)).all())
        for recruiter in team:
            if "video" not in setup.modes:
                recruiter.active = False
                session.add(recruiter)
        session.commit()
        team = [(r.id, r.timezone) for r in team]
    start = EVAL_NOW.date() - timedelta(days=1)
    busy = (
        [start + timedelta(days=n) for n in range(BUSY_ALL_DAYS)]
        if setup.busy == "all"
        else list(setup.busy)
    )
    for recruiter_id, timezone in team:
        zone = ZoneInfo(timezone)
        for item in busy:
            calendar.add_busy(recruiter_id, *_busy_window(item, zone))
    return config


def _busy_window(item: date | str, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """A whole day, or a "YYYY-MM-DD HH:MM-HH:MM" window of it, in `zone`."""
    if isinstance(item, date):
        opens = datetime.combine(item, time(0), tzinfo=zone)
        return opens, opens + timedelta(days=1)
    match = BUSY_WINDOW.match(item)
    assert match is not None, item  # checked by the card
    day = date.fromisoformat(match.group(1))
    start, end = (time.fromisoformat(match.group(n)) for n in (2, 3))
    return datetime.combine(day, start, tzinfo=zone), datetime.combine(day, end, tzinfo=zone)


def _expectations(expect: Expect, live_only: bool = False) -> list[tuple[str, Any, bool]]:
    out: list[tuple[str, Any, bool]] = []
    for name, value in expect:
        name = name.rstrip("_")
        if name == "live":
            if value is not None:
                out.extend(_expectations(value, True))
            continue
        if value is not None:
            out.append((name, value, live_only))
    return out


def _compare(name: str, want: Any, seen: Mapping[str, Any]) -> str | None:
    checks: dict[str, Callable[[], str | None]] = {
        "register": lambda: register_problem(seen["text"], want, seen["language"]),
        "language": lambda: _language_problem(want, seen),
        "faq_source": lambda: _faq_problem(want, seen["faq"]),
        "reply_matches": lambda: (
            None if re.search(want, seen["text"] or "", re.IGNORECASE) else f"/{want}/ not in reply"
        ),
        "reply_not_matches": lambda: (
            f"/{want}/ in reply" if re.search(want, seen["text"] or "", re.IGNORECASE) else None
        ),
        "card_matches": lambda: (
            None if re.search(want, seen["card"], re.IGNORECASE) else f"/{want}/ not in the card"
        ),
    }
    if name in checks:
        return checks[name]()
    got = seen[name]
    want = want.value if isinstance(want, Language) else want
    return None if got == want else f"expected {want}, got {got}"


def _language_problem(want: Language, seen: Mapping[str, Any]) -> str | None:
    said, detected = seen["action_language"], detect_language(seen["text"] or "")
    if said in (None, want.value) and detected in (None, want.value):
        return None
    return f"expected {want.value}, action {said}, text {detected}"


def _faq_problem(want: str | list[str], sources: Sequence[str]) -> str | None:
    if isinstance(want, list):
        missing = [source for source in want if source not in sources]
        return f"expected {missing} too, got {sources or 'none'}" if missing else None
    if want == "none":
        return None if not sources else f"expected no FAQ, got {sources}"
    if want == "any":
        return None if sources else "expected a FAQ answer, got none"
    return None if want in sources else f"expected {want}, got {sources or 'none'}"


def register_problem(text: str, register: Register, language: str | None) -> str | None:
    """A Spanish reply in the other register: usted forms owed tú, or tú forms owed usted."""
    if language != Language.ES.value:
        return None
    if slip := register_slip(text or "", "MX" if register == "usted" else "ES"):
        return f"expected {register}, found '{slip}'"
    return None


def _language(entry: Mapping[str, Any]) -> str | None:
    action = entry.get("action") or {}
    return action.get("language") or detect_language(entry.get("text") or "")


def _is_faq(source: Mapping[str, Any]) -> bool:
    return str(source.get("source", "")).startswith("faq:")


def _parse_action(stored: Mapping[str, Any] | None) -> Action | None:
    if not stored:
        return None
    try:
        return ACTION_ADAPTER.validate_python(stored)
    except ValidationError:
        return None


def _reference(action: Action, today: date, channel: Channel = Channel.WEB) -> str:
    try:
        ctx = ReplyContext.for_action(action, today=today, channel=channel)
        return fallback_reply(action, ctx)
    except Exception:  # a template that needs what the service fills in
        return ""


def _without_overview(text: str) -> str:
    """The reply without the fixed process overview the service puts before the first question."""
    packs = agent().texts.values()
    for lead in (*(p.overview for p in packs), *(p.overview_voice for p in packs)):
        if text.startswith(lead):
            return text.removeprefix(lead).lstrip()
    return text


def _ui(reply: Reply) -> dict[str, Any] | None:
    return reply.ui.model_dump(mode="json") if reply.ui is not None else None


def _shown(message: str | WidgetAnswer) -> str:
    if isinstance(message, str):
        return message
    value = message.value
    if isinstance(value, PhoneInput):
        return value.number
    return value if isinstance(value, str) else ", ".join(value)


def snapshot(state: ConversationState) -> dict[str, Any]:
    """The state a reviewer needs per turn: where the screening is, what it holds, the close."""
    profile = state.profile.model_dump(mode="json", exclude_none=True)
    return {
        "stage": state.stage,
        "awaiting": state.awaiting,
        "consented": state.consented,
        "country": state.country,
        "language": state.language.value,
        "profile": profile,
        "outcome": state.outcome.value if state.outcome else None,
        "disqualify_reason": state.disqualify_reason,
        "booking": state.booking.model_dump(mode="json") if state.booking else None,
        "duplicate_of": state.duplicate_of,
        "quality_flags": [flag.value for flag in state.quality_flags],
    }
