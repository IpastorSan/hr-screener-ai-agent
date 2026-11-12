"""Closed: what a message does once the screening has ended without an interview booked. A
gate close may be undone by the next message (`_reopens`), and an out-of-area close waits for
the answer to the talent-pool question."""

from datetime import date
from typing import ClassVar

from sazon.engine.actions import End, EndReason
from sazon.engine.models import FieldId, Intent, Outcome, QualityFlag, TurnExtraction
from sazon.engine.screening import _validate
from sazon.engine.spec import FieldType, agent
from sazon.engine.state import (
    Asking,
    ConversationState,
    Disqualified,
    InterviewFix,
    Qualified,
)
from sazon.engine.state import Closed as ClosedPhase
from sazon.engine.states.handler import Edge, NoLookup
from sazon.engine.turn import Turn, TurnInput, _closed_reply, _known_fields, _meta
from sazon.engine.validators import Valid


class Closed(NoLookup):
    edges: ClassVar[frozenset[Edge]] = frozenset()
    """None: a message after the close stays closed, the talent-pool answer included. One that
    undoes a gate close is `step`'s (`machine.EDGES`), before any handler runs."""

    def on_turn(self, state: ConversationState, turn: TurnInput) -> Turn:
        return _after_close(state, turn)

    def prompt(self, state: ConversationState) -> None:
        return None


HANDLER = Closed()


def _saved_without_interview(state: ConversationState) -> bool:
    """Qualified and closed with no interview: no time was free, or none suited. An interview
    dropped for abuse does not count."""
    qualified = isinstance(state.closed, Qualified)
    return qualified and QualityFlag.ABUSE not in state.quality_flags


def awaits_talent_pool_answer(state: ConversationState) -> bool:
    """Closed at a gate that offers the talent pool (keep the data for future vacancies), with
    its question still unanswered."""
    offered = agent().talent_pool(state.disqualify_reason)
    return offered and state.talent_pool_opt_in is None


def _reopens(
    state: ConversationState,
    extraction: TurnExtraction,
    fix: FieldId | InterviewFix | None,
    today: date,
) -> bool:
    """The message right after a gate closed the application contradicts the gate's answer
    ("sí tengo carnet", a city we serve) or corrects a field, so the close was a mishearing."""
    closing = agent().closing(state.disqualify_reason)
    if state.outcome != Outcome.DISQUALIFIED:  # `reopenable` already means a gate close
        return False
    if extraction.intent in (Intent.OPT_OUT, Intent.ABUSE):
        return False
    if fix in agent().by_id:
        return True
    if closing is None:
        return False
    gate = closing.id
    candidate = extraction.fields.get(gate)
    if candidate is not None:
        result = _validate(state, gate, candidate.value, today)
        if isinstance(result, Valid) and result.value is not False:
            return True
    others = [field for field in _known_fields(extraction) if field != gate]
    return extraction.intent == Intent.CORRECTION and bool(others)


def _undo_close(state: ConversationState) -> ConversationState:
    """Back to the gate that closed the application, its answer forgotten: a credential's no
    (a place outside the areas was never stored)."""
    closing = agent().closing(state.disqualify_reason)
    assert closing is not None, "only a gate close is undone"
    gate = closing
    kept = gate.type == FieldType.CREDENTIAL
    profile = state.profile.model_copy(update={gate.attr: None} if kept else {})
    return state.model_copy(update={"profile": profile, "phase": Asking(field=gate.id)})


def _after_close(state: ConversationState, turn: TurnInput) -> Turn:
    """A message after the screening ended, once the interrupts for a closed application let it
    through (`interrupts.py`: an injection attempt and insults): the answer to the talent-pool
    question, an answer to a question, or the goodbye."""
    extraction = turn.extraction
    closed = state.closed
    if awaits_talent_pool_answer(state) and extraction.intent != Intent.QUESTION:
        assert isinstance(closed, Disqualified)
        opt_in = extraction.intent == Intent.CONFIRM
        state = state.at(
            ClosedPhase(variant=closed.model_copy(update={"talent_pool_opt_in": opt_in}))
        )
        return state, End(reason=EndReason.CLOSED, **_meta(state))
    return _closed_reply(state, extraction)
