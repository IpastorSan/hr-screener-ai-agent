"""The transitions the replay corpus takes, for the coverage test (`tests/test_transitions.py`).

Every case is played again with a recorder on the dispatch table: the handlers (`HANDLERS`)
and the interrupts (`INTERRUPTS`). A call may take several legs, each owned by whoever moved
the state last. The call itself owns the first: what `step` does before any handler or
interrupt sees the state (a question answered, a gate close undone), and the whole move of
the other entry points (`machine.EDGES`). A handler or interrupt that changes the state owns
what follows until the next one does: the asking handler's read-back confirmed, then the next
question it asks.
"""

from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import ClassVar, NamedTuple
from unittest import mock

from evals.replay.corpus import Case, decode_args, engine_function
from sazon.engine import interrupts
from sazon.engine.actions import Prompt
from sazon.engine.interrupts import Interrupt
from sazon.engine.lookups import LookupResult
from sazon.engine.state import ConversationState, Node, node
from sazon.engine.states import HANDLERS
from sazon.engine.states.handler import Answered, Edge, Handler
from sazon.engine.turn import Interrupted, Lookup, Turn, TurnInput


class Leg(NamedTuple):
    by: str
    """A handler's phase, an interrupt's name or an entry point (`diagram.declared`)."""
    source: Node
    target: Node


class Recorder:
    """The legs of one call: `owner` moved the state to `at` last."""

    def __init__(self) -> None:
        self.start("", Node.START)

    def start(self, owner: str, at: Node) -> None:
        self.legs: list[Leg] = []
        self.owner, self.at = owner, at

    def leg(self, by: str, target: ConversationState) -> None:
        end = node(target)
        if end != self.at:
            self.legs.append(Leg(by, self.at, end))
        self.owner, self.at = by, end

    def handing(self, by: str, received: ConversationState) -> None:
        """`by` is handed the state: what it is now is the last owner's doing."""
        self.leg(self.owner, received)
        self.owner = by


class Recording:
    """A handler that records the legs the one it wraps takes."""

    edges: ClassVar[frozenset[Edge]] = frozenset()

    def __init__(self, by: str, handler: Handler, recorder: Recorder) -> None:
        self.by, self.handler, self.recorder = by, handler, recorder

    def on_turn(self, state: ConversationState, turn: TurnInput) -> Turn | Answered:
        self.recorder.handing(self.by, state)
        handled = self.handler.on_turn(state, turn)
        self.recorder.leg(self.by, handled.state if isinstance(handled, Answered) else handled[0])
        return handled

    def prompt(self, state: ConversationState) -> Prompt | Lookup | None:
        return self.handler.prompt(state)

    def on_lookup(self, state: ConversationState, result: LookupResult) -> Turn:
        self.recorder.handing(self.by, state)
        turn = self.handler.on_lookup(state, result)
        self.recorder.leg(self.by, turn[0])
        return turn


def _recording_interrupt(each: Interrupt, recorder: Recorder) -> Interrupt:
    def run(state: ConversationState, turn: TurnInput) -> Interrupted:
        owner = recorder.owner
        recorder.handing(each.name, state)
        state_after, action = each.run(state, turn)
        if action is None:
            recorder.owner = owner  # it let the turn through, as it was
        recorder.leg(recorder.owner, state_after)
        return state_after, action

    return each._replace(run=run)


@contextmanager
def recording(recorder: Recorder) -> Iterator[None]:
    handlers = {kind: Recording(str(kind), each, recorder) for kind, each in HANDLERS.items()}
    wrapped = tuple(_recording_interrupt(each, recorder) for each in interrupts.INTERRUPTS)
    with (
        mock.patch.object(interrupts, "INTERRUPTS", wrapped),
        mock.patch.dict(HANDLERS, handlers),
    ):
        yield


def observed(cases: Iterable[Case]) -> Counter[Leg]:
    """Every leg the cases take, with how many times."""
    recorder = Recorder()
    counts: Counter[Leg] = Counter()
    with recording(recorder):
        for case in cases:
            fn = case["fn"]
            args = decode_args(fn, case["args"])
            given = args.get("state")
            recorder.start(fn, node(given) if given is not None else Node.START)
            out = engine_function(fn)(**args)
            after = out[0] if isinstance(out, tuple) else out
            if isinstance(after, ConversationState):  # not a prompt or a yes/no reading
                recorder.leg(recorder.owner, after)
            counts.update(recorder.legs)
    return counts
