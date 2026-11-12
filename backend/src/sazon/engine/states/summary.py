"""Summary: every field in, the card with them waiting for a yes. A yes qualifies, "corregir
algo" asks which detail, and a message that changes a field is read as an answer."""

from datetime import date
from typing import ClassVar

from sazon.engine.actions import ConfirmSummary
from sazon.engine.models import FieldId, Intent, TurnExtraction
from sazon.engine.screening import NextStep, _next_step, _same_value, _screening_fix
from sazon.engine.spec import agent
from sazon.engine.state import ChoosingMode, ConversationState, Node
from sazon.engine.states.asking import _data_turn
from sazon.engine.states.handler import Answered, Edge, NoLookup
from sazon.engine.states.interview import _find_modes
from sazon.engine.turn import Turn, TurnInput, _known_fields, _wrap


class Summary(NoLookup):
    edges: ClassVar[frozenset[Edge]] = frozenset(
        {
            Edge(Node.SUMMARY, "yes, nothing changed", Node.CHOOSING_MODE),
            Edge(Node.SUMMARY, "a changed value needs a read-back", Node.CONFIRMING),
            Edge(Node.SUMMARY, "gate fails", Node.DISQUALIFIED),
            # The next question, once the answers are stored (`_next_step`).
            Edge(Node.SUMMARY, "a field dropped", Node.AWAITING),
            Edge(Node.SUMMARY, "an email to check", Node.CHECKING),
        }
    )

    def on_turn(self, state: ConversationState, turn: TurnInput) -> Turn | Answered:
        extraction = turn.extraction
        # "Sí, está correcto" with a value the summary already has is a yes.
        yes = extraction.intent == Intent.CONFIRM
        if yes and not _changed_fields(state, extraction, turn.today):
            return _qualify(state)
        if _bare_correction(extraction, _screening_fix(turn.fix)):
            # "Corregir algo" (or a spoken no) at the summary: ask which detail, same card.
            state, summary = _next_step(state)
            if isinstance(summary, ConfirmSummary):
                summary = summary.model_copy(update={"correcting": True})
            return _wrap(state, extraction, summary)
        return _data_turn(state, turn, asked=None)

    def prompt(self, state: ConversationState) -> NextStep:
        return _next_step(state)[1]


HANDLER = Summary()


def _bare_correction(extraction: TurnExtraction, fix: FieldId | None) -> bool:
    """Something is wrong, with no field named and no new value."""
    return extraction.intent == Intent.CORRECTION and fix is None and not _known_fields(extraction)


def _changed_fields(
    state: ConversationState, extraction: TurnExtraction, today: date
) -> list[FieldId]:
    """The fields in the message with a value other than the one the candidate has."""
    return [
        field
        for field in _known_fields(extraction)
        if not _same_value(
            state,
            field,
            extraction.fields[field].value,
            agent().field(field).value(state.profile),
            today,
        )
    ]


def _qualify(state: ConversationState) -> Turn:
    """Qualified: find out which interview modes the location offers before any slot."""
    state = state.at_interview(ChoosingMode())
    return state, _find_modes(state)
