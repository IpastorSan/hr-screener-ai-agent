"""The dispatch table: one handler per phase, each with its declared edges, the prompt the
state waits on, and the lookups only the state that asked for them takes."""

from datetime import date

import pytest

from sazon.agents.riders import FieldName
from sazon.engine import (
    CheckDuplicate,
    DuplicateChecked,
    FindModes,
    Greet,
    ModesFound,
    on_lookup,
    step,
)
from sazon.engine.actions import AskField, ConfirmIdentity
from sazon.engine.models import (
    CandidateProfile,
    Intent,
    Language,
    MatrixLanguage,
    Outcome,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.state import (
    Asking,
    ChoosingMode,
    Closed,
    Contact,
    ConversationState,
    DuplicateMatch,
    Interview,
    Node,
    Phase,
    PhaseKind,
    Qualified,
    Summary,
)
from sazon.engine.states import HANDLERS, current_prompt, prompt
from sazon.engine.states.handler import Answered
from sazon.engine.turn import CancelRequest, TurnInput

TODAY = date(2026, 10, 31)
MATCH = DuplicateMatch(application_id="a1", name="Lucía")
CHECKING = ConversationState(
    consented=True,
    phase=Contact(),
    profile=CandidateProfile(full_name="Lucía Pérez", phone="+34600"),
)


def at(phase: Phase, profile: CandidateProfile | None = None) -> ConversationState:
    return ConversationState(consented=True, phase=phase, profile=profile or CandidateProfile())


CLOSED = at(Closed(variant=Qualified()))


def extraction(intent: Intent) -> TurnExtraction:
    return TurnExtraction(
        intent=intent,
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
    )


def turn(intent: Intent) -> TurnInput:
    return TurnInput(extraction(intent), TODAY, False, None, None, CancelRequest())


def test_every_phase_has_a_handler() -> None:
    assert set(HANDLERS) == set(PhaseKind)


@pytest.mark.parametrize("kind", list(PhaseKind))
def test_every_handler_edge_starts_in_its_phase(kind: PhaseKind) -> None:
    """From its own phase: a turn or a lookup the handler takes starts there."""
    edges = HANDLERS[kind].edges
    assert all(edge.event and edge.source.within(Node(kind)) for edge in edges)


@pytest.mark.parametrize(
    ("state", "kind"),
    [
        (ConversationState(), Greet),
        (at(Asking(field=FieldName.FULL_NAME)), AskField),
        (at(Contact(match=MATCH)), ConfirmIdentity),
        (CHECKING, CheckDuplicate),
        (at(Interview(sub=ChoosingMode())), FindModes),
    ],
)
def test_the_prompt_comes_from_the_phase_handler(state: ConversationState, kind: type) -> None:
    assert isinstance(prompt(state), kind)


def test_nothing_is_waiting_once_closed_nor_while_a_lookup_runs() -> None:
    assert current_prompt(CLOSED) is None
    assert current_prompt(CHECKING) is None


def test_an_answer_is_stored_for_the_dispatcher_to_ask_the_next_question() -> None:
    state = at(Asking(field=FieldName.FULL_NAME))
    answered = HANDLERS[PhaseKind.ASKING].on_turn(state, turn(Intent.UNCLEAR))
    assert isinstance(answered, Answered)
    assert answered.phase is PhaseKind.ASKING
    assert answered.state.retries == {FieldName.FULL_NAME: 1}


def test_the_duplicate_check_goes_to_the_contact_handler() -> None:
    state, action = on_lookup(CHECKING, DuplicateChecked(match=MATCH))
    assert isinstance(action, ConfirmIdentity)
    assert state.pending_identity == MATCH


def test_the_modes_go_to_the_interview_handler() -> None:
    state, request = step(
        at(Summary(), CandidateProfile(location_id="l1")),
        extraction(Intent.CONFIRM),
        today=TODAY,
    )
    assert isinstance(request, FindModes)
    assert state.outcome == Outcome.QUALIFIED
    _, action = on_lookup(state, ModesFound(request=request, modes=()))
    assert action.kind == "find_slots"


@pytest.mark.parametrize(
    "state",
    [
        ConversationState(),
        at(Summary()),
        CLOSED,
    ],
)
def test_a_lookup_no_state_asked_for_is_a_bug(state: ConversationState) -> None:
    request = FindModes(location_id="l1", language=Language.ES, country="ES")
    with pytest.raises(ValueError, match="modes_found"):
        on_lookup(state, ModesFound(request=request, modes=()))
