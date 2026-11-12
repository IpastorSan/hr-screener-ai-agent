"""One handler per top-level state (`ConversationState.phase`), behind the dispatch table
`HANDLERS`."""

from sazon.engine.actions import Prompt
from sazon.engine.state import ConversationState, PhaseKind, phase_kind
from sazon.engine.states import asking, closed, consent, contact, interview, summary
from sazon.engine.states.handler import Handler
from sazon.engine.turn import LOOKUPS, Lookup

HANDLERS: dict[PhaseKind, Handler] = {
    PhaseKind.CONSENT: consent.HANDLER,
    PhaseKind.ASKING: asking.HANDLER,
    PhaseKind.CONTACT: contact.HANDLER,
    PhaseKind.SUMMARY: summary.HANDLER,
    PhaseKind.INTERVIEW: interview.HANDLER,
    PhaseKind.CLOSED: closed.HANDLER,
}


def prompt(state: ConversationState) -> Prompt | Lookup | None:
    """What the state's handler is waiting on: a question, a lookup still to run, or None
    once the screening is over."""
    return HANDLERS[phase_kind(state)].prompt(state)


def current_prompt(state: ConversationState) -> Prompt | None:
    """The question the candidate is looking at, or None when the screening is over.

    None too while a duplicate check is pending, which is not a message.
    """
    waiting = prompt(state)
    return None if isinstance(waiting, LOOKUPS) else waiting
