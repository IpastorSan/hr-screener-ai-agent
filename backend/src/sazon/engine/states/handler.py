"""What a state's handler is: how it reads a candidate turn and a lookup's result, the prompt
it is waiting on, and the transitions it declares."""

from typing import ClassVar, NamedTuple, Protocol

from sazon.engine.actions import Prompt
from sazon.engine.lookups import LookupResult
from sazon.engine.models import TurnExtraction
from sazon.engine.state import ConversationState, Node, Where, phase_kind
from sazon.engine.turn import Lookup, Turn, TurnInput


class Edge(NamedTuple):
    """A transition a handler, an interrupt or an entry point may take: from `source` on
    `event`, to `target`. A parent node stands for each of its sub-states, so one edge covers
    an interrupt that applies to all of them. Staying in the same sub-state (a retry, the next
    field, new times) is not a transition and is not declared."""

    source: Node
    event: str
    target: Node

    def covers(self, source: Node, target: Node) -> bool:
        return source.within(self.source) and target.within(self.target)


class Answered(NamedTuple):
    """A screening turn whose answers are stored and that has nothing else to say: the
    interrupts after the handler run for `phase` (the one the answers were given in), then the
    next question is asked (`step`)."""

    state: ConversationState
    extraction: TurnExtraction
    phase: Where


class Handler(Protocol):
    edges: ClassVar[frozenset[Edge]]
    """Every transition the handler may take, for the diagram and its coverage."""

    def on_turn(self, state: ConversationState, turn: TurnInput) -> Turn | Answered:
        """A candidate turn the interrupts before the handler let through."""
        ...

    def prompt(self, state: ConversationState) -> Prompt | Lookup | None:
        """What the candidate is looking at: the question to repeat, a lookup still to run,
        or None once there is nothing to answer."""
        ...

    def on_lookup(self, state: ConversationState, result: LookupResult) -> Turn:
        """What the service found for the lookup this state asked for."""
        ...


class NoLookup:
    """A handler whose state never waits on a lookup: one handed to it is a bug."""

    def on_lookup(self, state: ConversationState, result: LookupResult) -> Turn:
        raise ValueError(f"no {result.kind} is pending in {phase_kind(state)}")
