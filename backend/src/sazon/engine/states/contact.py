"""Contact: the open application a phone or email matched, and "¿Quieres continuar como
Lucía?" waiting for its answer (D10). The check itself (`CheckDuplicate`) is a lookup the
service runs between two questions; its result comes back here."""

from typing import ClassVar

from sazon.engine.actions import CheckDuplicate, ConfirmIdentity, ResumeExisting, WarnPolicy
from sazon.engine.lookups import DuplicateChecked, LookupResult
from sazon.engine.models import Intent, Outcome, TurnExtraction
from sazon.engine.screening import NextStep, _next_step, _tell_contact_dropped
from sazon.engine.state import (
    Closed,
    ConversationState,
    DisqualifyReason,
    HandedOver,
    Node,
    PendingQuestion,
)
from sazon.engine.state import Contact as ContactPhase
from sazon.engine.states.asking import _data_turn
from sazon.engine.states.handler import Answered, Edge
from sazon.engine.turn import Turn, TurnInput, _answer, _close, _disqualify, _meta, _wrap

MAX_POLICY_CLOSES = 3
"""Manipulation closes per phone or email after which no new application starts (D11)."""
MAX_IDENTITY_RETRIES = 1
"""An unclear answer to "¿Quieres continuar como Lucía?" is asked once more, then read as a no:
a new application, which says nothing of the other one."""


class Contact:
    edges: ClassVar[frozenset[Edge]] = frozenset(
        {
            Edge(Node.IDENTITY, "yes", Node.HANDED_OVER),
            Edge(Node.IDENTITY, "no, or unclear twice", Node.AWAITING),
            Edge(Node.IDENTITY, "no, every field in", Node.SUMMARY),
            # The duplicate check's result (`on_lookup`).
            Edge(Node.CHECKING, "match", Node.IDENTITY),
            Edge(Node.CHECKING, "no match", Node.AWAITING),
            Edge(Node.CHECKING, "no match, every field in", Node.SUMMARY),
            Edge(Node.CHECKING, "three policy closes", Node.DISQUALIFIED),
        }
    )

    def on_turn(self, state: ConversationState, turn: TurnInput) -> Turn | Answered:
        if state.pending_identity is None:
            # A message before the lookup ran: its answers are read, then the lookup is asked
            # for again (`_next_step`).
            return _data_turn(state, turn, asked=None)
        return _identity_answer(state, turn.extraction)

    def prompt(self, state: ConversationState) -> NextStep:
        return _next_step(state)[1]

    def on_lookup(self, state: ConversationState, result: LookupResult) -> Turn:
        if not isinstance(result, DuplicateChecked):
            raise ValueError(f"no {result.kind} is pending at the contact")
        return _duplicate_checked(state, result)


HANDLER = Contact()


def _duplicate_checked(state: ConversationState, checked: DuplicateChecked) -> Turn:
    """Continue after `CheckDuplicate` with the matching open application, if any, and how many
    earlier applications with this phone or email were closed for a manipulation attempt.

    At `MAX_POLICY_CLOSES` no application starts: a neutral close, no screening (D11). A match
    is not resumed yet: Ana asks whether to carry on as that person (D10), and nothing else of
    it is said before the yes (`_identity_answer`). One the candidate already said is not
    theirs is ignored. A question asked in the turn that triggered the check is answered now,
    before the next step.
    """
    match, pending_question = checked.match, state.pending_question
    state = state.model_copy(
        update={
            "policy_closes": max(state.policy_closes, checked.policy_closes),
            "duplicate_checked": True,
        }
    )
    if state.policy_closes >= MAX_POLICY_CLOSES:
        state = _close(state, Outcome.DISQUALIFIED, DisqualifyReason.POLICY_VIOLATION)
        return state, _disqualify(state).model_copy(update={"blocked": True})
    if match is not None and match.application_id not in state.declined_applications:
        state = state.at(ContactPhase(match=match))
        state, confirm = _next_step(state)
        if pending_question is None or not isinstance(confirm, ConfirmIdentity):
            return state, confirm
        return state, _answer(state, pending_question, confirm)
    return _carry_on(state, pending_question)


def _carry_on(state: ConversationState, pending_question: PendingQuestion | None) -> Turn:
    """No open application to resume (or the candidate said it is not theirs): the screening
    carries on with this conversation's own data."""
    state = state.model_copy(
        update={
            "duplicate_checked": True,
            "duplicate_email_unchecked": state.profile.email is None,
            "phase": ContactPhase(),
        }
    )
    state, next_step = _next_step(state)
    if isinstance(next_step, CheckDuplicate):
        return state, next_step
    if state.policy_closes and not state.policy_warned:
        # A new application after a manipulation close starts with the warning (D11). It goes
        # first: a line about a dropped contact follows on the next turn.
        state = state.model_copy(update={"policy_warned": True})
        return state, WarnPolicy(follow_up=next_step, ui=next_step.ui, **_meta(state))
    if state.contact_notice is not None:
        return _tell_contact_dropped(state, next_step)
    if pending_question is None:
        return state, next_step
    return state, _answer(state, pending_question, next_step)


def _identity_answer(state: ConversationState, extraction: TurnExtraction) -> Turn:
    """The answer to "¿Quieres continuar como Lucía?" (D10). A yes resumes that application;
    a no carries on as a new one, which inherits nothing of it. A question is answered and the
    question asked again; anything else unclear is asked again once, then read as a no."""
    match = state.pending_identity
    assert match is not None
    intent = extraction.intent
    if intent == Intent.CONFIRM:
        state = state.at(Closed(variant=HandedOver(application_id=match.application_id)))
        return state, ResumeExisting(application_id=match.application_id, **_meta(state))
    no = intent in (Intent.ANSWER, Intent.CORRECTION)
    if no or (intent == Intent.UNCLEAR and match.retries >= MAX_IDENTITY_RETRIES):
        declined = (*state.declined_applications, match.application_id)
        state = state.model_copy(update={"declined_applications": declined})
        return _carry_on(state, None)
    if intent == Intent.UNCLEAR:
        match = match.model_copy(update={"retries": match.retries + 1})
        state = state.at(ContactPhase(match=match))
    state, confirm = _next_step(state)
    return _wrap(state, extraction, confirm)
