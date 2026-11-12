"""Idle conversations: nudge limits and abandonment worked out at read time.

The chat nudges a quiet candidate from the browser (SAZ-17); there is no background job. A
conversation still in progress whose last activity is more than `ABANDON_AFTER` ago counts as
abandoned at its current stage wherever it is read. `ScreeningService.close_if_stale` writes
that down when the conversation is next touched; `close_stale` does it for all of them
(`sazon close-stale`).
"""

from datetime import datetime, timedelta

from sazon.engine import ConversationState, abandon
from sazon.storage.models import CandidateStatus, Conversation
from sazon.storage.repo import state_of, to_utc

ABANDON_AFTER = timedelta(hours=72)
MAX_NUDGES = 2


def abandoned_since(last_activity: datetime) -> datetime:
    """When an idle conversation started counting as abandoned."""
    return to_utc(last_activity) + ABANDON_AFTER


def is_idle(last_activity: datetime, now: datetime) -> bool:
    return to_utc(now) > abandoned_since(last_activity)


def is_stale(conversation: Conversation, state: ConversationState, now: datetime) -> bool:
    """In progress, and idle for longer than `ABANDON_AFTER`.

    A conversation handed over to the candidate's open application is not: that one counts.
    """
    if state.outcome is not None or state.duplicate_of is not None:
        return False
    return is_idle(conversation.last_activity_at, now)


def effective_state(conversation: Conversation, now: datetime) -> ConversationState:
    """The conversation's state as the recruiter should see it: abandoned once stale."""
    state = state_of(conversation)
    return abandon(state) if is_stale(conversation, state, now) else state


def effective_status(status: str, last_activity: datetime, now: datetime) -> CandidateStatus:
    """A candidate still in progress but idle for too long counts as abandoned."""
    if status == CandidateStatus.IN_PROGRESS and is_idle(last_activity, now):
        return CandidateStatus.ABANDONED
    return CandidateStatus(status)
