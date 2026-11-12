"""Synthesized engine cases for what the offline journeys cannot script.

The keyword extractor never reads frustration, insults, a question inside an answer or a
refusal, so those paths get here instead: scripted extractions played through the engine API
from states the journeys recorded. Every call is a case named `<journey>+synth.<scenario>`.

- `interrupt.*`: an opt-out, an injection, two insults and a cancel request at every kind of
  recorded state (every stage, booked or not).
- `frustrated.*`: two frustrated turns, the pause offer, and its answers: yes (and the message
  after it), "no, sigamos", and an answer with the pending field.
- `apology.*`: an insult, then an answer (frustrated or not): the de-escalation reset.
- `question.*`: a question asked with the answer that triggers a lookup (duplicate check,
  modes): the pending question, with no match and with one.
- `refuse.*`, `invalid.*`: the field refused or failed until it is given up on (a dropped
  phone or email: the contact notice).
- `amend.*`: a booked interview's detail named as wrong, then no usable value twice.
- `edge.*`: transitions no journey takes (`tests/test_transitions.py`): an injection with the
  consent's yes or after the application was saved with no interview, a license lost at the
  summary or after booking, a new location after booking, and an email given at the summary
  that the duplicate check finds, or not.

Lookups are settled as the service would: no duplicate (or one, in `question.match`), both
interview modes, and the times and booking of a recorded journey.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from datetime import date
from typing import Any

from evals.replay import census
from evals.replay.corpus import Case, encode, encode_args
from sazon import engine
from sazon.agents.riders import FieldName
from sazon.engine.actions import Action, BookSlot, CheckDuplicate, FindModes, FindSlots
from sazon.engine.lookups import DuplicateChecked, LookupResult, ModesFound, SlotBooked, SlotsFound
from sazon.engine.models import (
    FieldCandidate,
    Intent,
    InterviewMode,
    Language,
    MatrixLanguage,
    Sentiment,
    Stage,
    TurnExtraction,
)
from sazon.engine.slots import Booked, Slot
from sazon.engine.state import (
    ConversationState,
    DuplicateMatch,
    InterviewFix,
    Qualified,
)

type Turn = tuple[ConversationState, Action]

QUESTION = "¿Cuánto se cobra por hora?"
VALID_EMAIL = "synth@example.com"
NOT_A_VALUE = "???"
MATCH = DuplicateMatch(application_id="synth-application", name="Lucía")
OTHER_PLACE = "Sants"
"""A place of another location than those the recorded bookings are at (`es-bcn-sants`)."""


def ex(
    intent: Intent,
    fields: dict[str, Any] | None = None,
    *,
    sentiment: Sentiment = Sentiment.NEUTRAL,
    question: str | None = None,
    declines_field: bool = False,
) -> TurnExtraction:
    """A message in Spanish, as the extractor would read it."""
    return TurnExtraction(
        intent=intent,
        fields={k: FieldCandidate(value=v, confidence=1.0) for k, v in (fields or {}).items()},
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=sentiment,
        question=question,
        declines_field=declines_field,
    )


FRUSTRATED = ex(Intent.UNCLEAR, sentiment=Sentiment.FRUSTRATED)
YES = ex(Intent.CONFIRM)
CARRY_ON = ex(Intent.ANSWER)  # "no, sigamos"
ABUSE = ex(Intent.ABUSE, sentiment=Sentiment.FRUSTRATED)


class Driver:
    """Plays one scenario through the engine, recording every call."""

    def __init__(
        self, name: str, today: date, slots: list[Slot], booked: Booked, out: list[Case]
    ) -> None:
        self.name, self.today, self.slots, self.booked, self.out = name, today, slots, booked, out

    def call(self, fn: str, *args: Any, **kwargs: Any) -> Any:
        result = getattr(engine, fn)(*args, **kwargs)
        self.out.append(
            Case(case=self.name, fn=fn, args=encode_args(fn, args, kwargs), out=encode(result))
        )
        return result

    def turn(
        self,
        state: ConversationState,
        extraction: TurnExtraction,
        match: DuplicateMatch | None = None,
        **kwargs: Any,
    ) -> Turn:
        state, action = self.call("step", state, extraction, today=self.today, **kwargs)
        return self.settle(state, action, match)

    def settle(
        self, state: ConversationState, action: Action, match: DuplicateMatch | None
    ) -> Turn:
        """The lookups the service makes before it replies."""
        for _ in range(5):
            if isinstance(action, CheckDuplicate):
                result: LookupResult = DuplicateChecked(match=match)
            elif isinstance(action, FindModes):
                modes = [InterviewMode.ONSITE, InterviewMode.VIDEO]
                result = ModesFound(request=action, modes=modes)
            elif isinstance(action, FindSlots):
                result = SlotsFound(request=action, slots=self.slots)
            elif isinstance(action, BookSlot):
                result = SlotBooked(request=action, booked=self.booked)
            else:
                break
            state, action = self.call("on_lookup", state, result)
        return state, action


def _interrupts(d: Driver, state: ConversationState) -> None:
    d.name += ".interrupt"
    d.turn(state, ex(Intent.OPT_OUT))
    d.turn(state, ex(Intent.ANSWER), suspicious=True)
    once, _ = d.turn(state, ABUSE)
    d.turn(once, ABUSE)
    asked, _ = d.turn(state, ex(Intent.ANSWER), cancel_request=True)
    if asked.cancel_offered:
        d.turn(asked, YES)
        d.turn(asked, ex(Intent.ANSWER), cancel_request=True)
        d.turn(asked, ex(Intent.QUESTION, question=QUESTION))


def _frustrated(d: Driver, state: ConversationState, answer: TurnExtraction | None) -> None:
    d.name += ".frustrated"
    once, _ = d.turn(state, FRUSTRATED)
    offered, _ = d.turn(once, FRUSTRATED)
    if not offered.pause_offered:
        return
    paused, _ = d.turn(offered, YES)
    d.turn(paused, answer or CARRY_ON)
    d.turn(offered, CARRY_ON)
    d.turn(offered, FRUSTRATED)
    if answer is not None:
        d.turn(offered, answer)


def _apology(d: Driver, state: ConversationState, answer: TurnExtraction) -> None:
    d.name += ".apology"
    insulted, _ = d.turn(state, ABUSE)
    d.turn(insulted, answer)
    d.turn(insulted, answer.model_copy(update={"sentiment": Sentiment.FRUSTRATED}))
    frustrated, _ = d.turn(state, FRUSTRATED)
    insulted, _ = d.turn(frustrated, ABUSE)
    d.turn(insulted, answer.model_copy(update={"sentiment": Sentiment.FRUSTRATED}))


def _question(d: Driver, state: ConversationState, answer: TurnExtraction) -> None:
    d.name += ".question"
    asking = answer.model_copy(update={"question": QUESTION})
    d.turn(state, asking)
    d.turn(state, asking, match=MATCH)


def _refuse(d: Driver, state: ConversationState, field: FieldName) -> None:
    d.name += ".refuse"
    if field == FieldName.PHONE:
        # The phone refused with the email in the same message: left out at once, and the
        # email's duplicate check runs before the line about it.
        d.turn(state, ex(Intent.ANSWER, {"email": VALID_EMAIL}, declines_field=True))
    refusing = ex(Intent.ANSWER, declines_field=True)
    for _ in range(3):
        state, _ = d.turn(state, refusing)
        if state.awaiting != field:
            break


def _invalid(d: Driver, state: ConversationState, field: FieldName) -> None:
    d.name += ".invalid"
    for _ in range(3):
        state, _ = d.turn(state, ex(Intent.ANSWER, {field.value: NOT_A_VALUE}))
        if state.awaiting != field:
            break
    d.turn(state, ex(Intent.ANSWER))


def _amend(d: Driver, state: ConversationState) -> None:
    d.name += ".amend"
    for fix in (FieldName.EMAIL, FieldName.PHONE, FieldName.FULL_NAME):
        asked, _ = d.turn(state, ex(Intent.CORRECTION), fix=fix)
        once, _ = d.turn(asked, ex(Intent.ANSWER, {fix.value: NOT_A_VALUE}))
        d.turn(once, ex(Intent.ANSWER, {fix.value: NOT_A_VALUE}))
        d.turn(once, ex(Intent.ANSWER))
        d.turn(asked, ex(Intent.QUESTION, question=QUESTION))
    for fix in InterviewFix:
        d.turn(state, ex(Intent.CORRECTION), fix=fix)


def _edges_consent(d: Driver, state: ConversationState) -> None:
    d.name += ".edge"
    d.turn(state, YES, suspicious=True)


def _edges_summary(d: Driver, state: ConversationState) -> None:
    """At the summary with no email: the license lost, and an email given."""
    d.name += ".edge"
    d.turn(state, ex(Intent.ANSWER, {"has_license": False}))
    email = ex(Intent.ANSWER, {"email": VALID_EMAIL})
    d.turn(state, email)
    identity, _ = d.turn(state, email, match=MATCH)
    d.turn(identity, ex(Intent.ANSWER))


def _edges_booked(d: Driver, state: ConversationState) -> None:
    d.name += ".edge"
    d.turn(state, ex(Intent.CORRECTION, {"has_license": False}), fix=FieldName.HAS_LICENSE)
    d.turn(state, ex(Intent.CORRECTION, {"city": OTHER_PLACE}), fix=FieldName.CITY)


def _edges_saved(d: Driver, state: ConversationState) -> None:
    d.name += ".edge"
    d.turn(state, ex(Intent.ANSWER), suspicious=True)


def _seeds(steps: Iterable[Case]) -> Iterator[tuple[str, date, ConversationState]]:
    """Every state a step was given or returned, in order, with its journey and day."""
    for case in steps:
        today = date.fromisoformat(case["args"]["today"])
        for data in (case["args"]["state"], case["out"][0]):
            yield case["case"], today, ConversationState.model_validate(data)


def _first(cases: Iterable[Case], kind: str, field: str) -> Any:
    """`field` of the first lookup result of `kind` the journeys handed back that has one."""
    results = (c["args"]["result"] for c in cases if c["fn"] == "on_lookup")
    return next(r[field] for r in results if r["kind"] == kind and r.get(field))


def synthesize(cases: list[Case]) -> list[Case]:
    slots = [Slot.model_validate(s) for s in _first(cases, "slots_found", "slots")]
    booked = Booked.model_validate(_first(cases, "slot_booked", "booked"))
    out: list[Case] = []

    def driver(source: str, today: date) -> Driver:
        return Driver(f"{source}+synth", today, slots, booked, out)

    steps = [c for c in cases if c["fn"] == "step"]
    kinds: Counter[tuple[Any, ...]] = Counter()
    answers: set[tuple[str, Any]] = set()
    plays: list[Callable[[], None]] = []
    edges: list[Callable[[], None]] = []
    for case in steps:
        data = census.flat(case["args"]["state"])
        state = ConversationState.model_validate(data)
        extraction = TurnExtraction.model_validate(case["args"]["extraction"])
        today = date.fromisoformat(case["args"]["today"])
        source = case["case"]
        kind = (*census.key(data), data["awaiting"], data["voice"])
        if not kinds[kind]:
            plays.append(lambda s=state, t=today, n=source: _interrupts(driver(n, t), s))
            plays.append(lambda s=state, t=today, n=source: _frustrated(driver(n, t), s, None))
            if state.stage == Stage.CONFIRM:
                plays.append(lambda s=state, t=today, n=source: _question(driver(n, t), s, YES))
            if state.booking is not None and state.stage == Stage.DONE:
                plays.append(lambda s=state, t=today, n=source: _amend(driver(n, t), s))
                edges.append(lambda s=state, t=today, n=source: _edges_booked(driver(n, t), s))
        kinds[kind] += 1
        field = state.awaiting
        answering = extraction.intent == Intent.ANSWER and extraction.fields
        if field is None or not answering or state.pending_confirmation is not None:
            continue
        if (field, state.voice) in answers:
            continue
        answers.add((field, state.voice))
        name = FieldName(field)
        for play in (_frustrated, _apology, _question):
            plays.append(
                lambda p=play, s=state, e=extraction, t=today, n=source: p(driver(n, t), s, e)
            )
        plays.append(lambda s=state, f=name, t=today, n=source: _refuse(driver(n, t), s, f))
        plays.append(lambda s=state, f=name, t=today, n=source: _invalid(driver(n, t), s, f))
    for play in plays:
        play()
    # Played after the others, so the cases recorded before them keep their order.
    for play in edges:
        play()
    once: list[tuple[Callable[[Driver, ConversationState], None], Callable[[Any], bool]]] = [
        (_edges_consent, lambda s: s.stage == Stage.CONSENT),
        (_edges_summary, lambda s: s.stage == Stage.CONFIRM and s.profile.email is None),
        (_edges_saved, lambda s: isinstance(s.closed, Qualified) and s.closed.slots_declined),
    ]
    for play, wanted in once:
        source, today, state = next(seed for seed in _seeds(steps) if wanted(seed[2]))
        play(driver(source, today), state)
    return out
