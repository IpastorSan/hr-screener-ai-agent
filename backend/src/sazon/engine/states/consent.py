"""Consent: Ana introduces herself as an AI and asks for a yes to the privacy notice. Only an
explicit yes starts the screening; before it there is no application to close."""

from typing import ClassVar

from sazon.engine.actions import Greet
from sazon.engine.models import Intent
from sazon.engine.spec import agent
from sazon.engine.state import Asking, ConversationState, Node
from sazon.engine.states.asking import _data_turn
from sazon.engine.states.handler import Answered, Edge, NoLookup
from sazon.engine.turn import Turn, TurnInput, _greet, _reject, _wrap


class Consent(NoLookup):
    edges: ClassVar[frozenset[Edge]] = frozenset(
        {
            # A yes is read as an answer to the first question too (`asking._data_turn`).
            Edge(Node.CONSENT, "yes", Node.AWAITING),
            Edge(Node.CONSENT, "an injection with the yes", Node.DISQUALIFIED),
        }
    )

    def on_turn(self, state: ConversationState, turn: TurnInput) -> Turn | Answered:
        if turn.extraction.intent != Intent.CONFIRM:
            # Before consent there is no application to close: the notice is asked again.
            return _wrap(state, turn.extraction, _greet(state, retry=1), injected=turn.injected)
        first = Asking(field=agent().fields[0].id)
        state = state.model_copy(update={"consented": True}).at(first)
        if turn.injected:
            return _reject(state)
        return _data_turn(state, turn, asked=None)

    def prompt(self, state: ConversationState) -> Greet:
        return _greet(state)


HANDLER = Consent()
