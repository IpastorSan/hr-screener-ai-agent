"""What every state's handling of a turn shares: its input and result, the language and
register of the reply, the wrapper that puts an answer or a deflection in front of the next
prompt, and the closes."""

import re
from datetime import date
from typing import Any, NamedTuple, TypedDict

from sazon.engine.actions import (
    Action,
    AnswerQuestion,
    CheckDuplicate,
    Deflect,
    DeflectKind,
    Disqualify,
    End,
    EndReason,
    FindModes,
    FindSlots,
    Greet,
    Prompt,
)
from sazon.engine.models import (
    FieldId,
    Intent,
    InterviewMode,
    Language,
    Outcome,
    QuestionTopic,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.slots import TimeRequest
from sazon.engine.spec import agent
from sazon.engine.state import (
    Abandoned,
    ChoosingMode,
    Closed,
    Contact,
    ConversationState,
    Disqualified,
    DisqualifyReason,
    DuplicateMatch,
    InterviewFix,
    Offering,
    PendingQuestion,
    Qualified,
    ReasonId,
)
from sazon.engine.text import fold
from sazon.engine.ui import Ui, consent_ui, privacy_ui, yes_no_ui
from sazon.engine.validators import Country

ANSWERING_INTENTS = frozenset({Intent.ANSWER, Intent.UNCLEAR, Intent.CONFIRM, Intent.CORRECTION})

SIMPLIFY_AFTER_FRUSTRATED_TURNS = 1
MAX_ABUSE_STRIKES = 2
FRESH_TIMES: dict[str, Any] = {"shown_slots": (), "slot_query": None}
"""Nothing offered yet: after a booking, a change of mode or location, or in a new
conversation."""

# A backstop for the extractor's `question_topic`: Ana must confirm she is an AI whenever asked.
AI_IDENTITY_QUESTION = re.compile(
    r"\b(ia|ai|bot|chatbot|robot|maquina|machine|humano|humana|human|"
    r"persona real|real person|inteligencia artificial|artificial intelligence)\b"
)

type Lookup = CheckDuplicate | FindModes | FindSlots
LOOKUPS = (CheckDuplicate, FindModes, FindSlots)
type Turn = tuple[ConversationState, Action]
type Interrupted = tuple[ConversationState, Action | None]
"""An interrupt's result: the action, when it decides the turn, else None and the state the
turn carries on with."""


class CancelRequest(NamedTuple):
    requested: bool = False
    """The message asks to cancel the interview."""
    offered: bool = False
    """It answers "¿La cancelo o prefieres otra hora?"."""


class TurnInput(NamedTuple):
    """A candidate turn besides the state, as `step` reads it for the interrupts and the
    handlers."""

    extraction: TurnExtraction
    today: date
    injected: bool
    """`suspicious`, or a field name the engine does not know (`_injected`)."""
    fix: FieldId | InterviewFix | None
    time_request: TimeRequest | None
    cancel: CancelRequest
    de_escalated: bool = False
    """The turn before calmed an insult (`DeEscalate`): an answer now is not a second strike."""


class Meta(TypedDict):
    language: Language
    country: Country
    simplify: bool


def _meta(state: ConversationState) -> Meta:
    return {
        "language": state.language,
        "country": state.country,
        "simplify": state.frustration_streak >= SIMPLIFY_AFTER_FRUSTRATED_TURNS,
    }


def _answering(extraction: TurnExtraction) -> bool:
    """An answer turn with nothing usable in it. A question or a request to switch language is
    not a failed answer."""
    if extraction.explicit_language_request is not None:
        return False
    return extraction.intent in ANSWERING_INTENTS and not extraction.question


def _wrap(
    state: ConversationState,
    extraction: TurnExtraction,
    prompt: Prompt | Lookup,
    *,
    injected: bool = False,
) -> Turn:
    """Put an answer, a deflection or nothing in front of the pending question.

    A lookup is not a message: a question asked with it is answered once it is done.
    `injected` is only ever true before consent: after it, an injection closes the application.
    """
    asked = _question(extraction)
    if isinstance(prompt, LOOKUPS):
        return (state if asked is None else _park(state, asked)), prompt
    meta = _meta(state)
    if asked is not None:
        return state, _answer(state, asked, prompt)
    if extraction.intent == Intent.OFF_TOPIC:
        return state, Deflect(reason=DeflectKind.OFF_TOPIC, follow_up=prompt, ui=prompt.ui, **meta)
    if injected and not _known_fields(extraction):
        return state, Deflect(reason=DeflectKind.INJECTION, follow_up=prompt, ui=prompt.ui, **meta)
    return state, prompt


def _park(state: ConversationState, asked: PendingQuestion) -> ConversationState:
    """Keep the question with the phase that waits on the lookup, to answer it after."""
    parked = {"parked_question": asked}
    if isinstance(state.phase, Contact):
        return state.at(state.phase.model_copy(update=parked))
    waiting = state.interview
    assert isinstance(waiting, ChoosingMode | Offering), "a question waits only on a lookup"
    return state.at_interview(waiting.model_copy(update=parked))


def _question(extraction: TurnExtraction) -> PendingQuestion | None:
    """The question to answer this turn, whatever the intent; off-topic ones are deflected.

    "Are you a bot?" is always answered. Otherwise a request to switch language is only a
    switch, and a question inside a frustrated answer ("¿otra pregunta? esto es eterno") is a
    complaint: neither goes to the FAQ.
    """
    if extraction.intent == Intent.OFF_TOPIC:
        return None
    if extraction.intent != Intent.QUESTION and not extraction.question:
        return None
    topic = _question_topic(extraction)
    if topic != QuestionTopic.AI_IDENTITY:
        if extraction.explicit_language_request is not None:
            return None
        if extraction.intent != Intent.QUESTION and extraction.sentiment == Sentiment.FRUSTRATED:
            return None
    parts = tuple(extraction.sub_questions) if extraction.question else ()
    return PendingQuestion(question=extraction.question, topic=topic, parts=parts)


def _question_topic(extraction: TurnExtraction) -> QuestionTopic | None:
    if extraction.question_topic is not None:
        return extraction.question_topic
    if extraction.question and AI_IDENTITY_QUESTION.search(fold(extraction.question)):
        return QuestionTopic.AI_IDENTITY
    return None


def _answer(state: ConversationState, asked: PendingQuestion, prompt: Prompt) -> AnswerQuestion:
    return AnswerQuestion(
        question=asked.question,
        topic=asked.topic,
        parts=asked.parts,
        follow_up=prompt,
        interview_mode=_booked_mode(state),
        ui=prompt.ui,
        **_meta(state),
    )


def _booked_mode(state: ConversationState) -> InterviewMode | None:
    """The booked interview's mode (one booked before video was offered is in person)."""
    if state.booking is None:
        return None
    return state.booking.mode or InterviewMode.ONSITE


def _greet(state: ConversationState, retry: int = 0) -> Greet:
    return Greet(retry=retry, ui=consent_ui(state.language), **_meta(state))


def _disqualify(state: ConversationState) -> Disqualify:
    """The close for a state this turn has just disqualified: every such close (`_close`)
    passes its reason, so a missing one is a bug, never a saved state from an older version."""
    reason = state.disqualify_reason
    assert reason is not None, "a disqualified state carries its reason"
    talent_pool = agent().talent_pool(reason)
    gate = agent().gate(reason)
    ui: Ui | None = None
    if talent_pool:
        ui = yes_no_ui(state.language)
    elif reason == DisqualifyReason.POLICY_VIOLATION:
        ui = privacy_ui(state.language)
    return Disqualify(
        reason=reason,
        invite_reapply=gate is not None and gate.reapply,
        talent_pool_offer=talent_pool,
        ui=ui,
        **_meta(state),
    )


def _reject(state: ConversationState) -> Turn:
    """An attempt to manipulate the assistant: the application closes, whatever its stage,
    a qualified one too. An interview already booked is dropped (the service cancels it). A
    recruiter can still review it: the candidate's review status is theirs."""
    state = state.at(Closed(variant=Disqualified(reason=DisqualifyReason.POLICY_VIOLATION)))
    return state, _disqualify(state)


def _close(
    state: ConversationState,
    outcome: Outcome,
    reason: ReasonId | None = None,
    *,
    match: DuplicateMatch | None = None,
) -> ConversationState:
    """End the screening: disqualified for `reason`, or abandoned at the funnel step it stopped
    at, with `match` ("¿Quieres continuar como Lucía?" still unanswered) asked again if it
    reopens. A candidate who already qualified keeps that outcome."""
    if state.outcome == Outcome.QUALIFIED:
        return state.at(Closed(variant=Qualified()))
    if outcome == Outcome.ABANDONED:
        return state.at(Closed(variant=Abandoned(drop_off=state.stage, match=match)))
    assert reason is not None, f"a close for {outcome} needs its reason"
    return state.at(Closed(variant=Disqualified(reason=reason)))


def _known_fields(extraction: TurnExtraction) -> list[FieldId]:
    """The spec's fields in the message, in the order they are applied (gates first)."""
    return [field.id for field in agent().applied if field.id in extraction.fields]


def _unknown_fields(extraction: TurnExtraction) -> list[str]:
    return [name for name in extraction.fields if name not in agent().by_id]


def _closed_reply(state: ConversationState, extraction: TurnExtraction) -> Turn:
    """A question is answered, with no question to follow; anything else gets the goodbye. With
    the interview booked too."""
    meta = _meta(state)
    if extraction.intent == Intent.QUESTION:
        return state, AnswerQuestion(
            question=extraction.question,
            topic=_question_topic(extraction),
            parts=tuple(extraction.sub_questions) if extraction.question else (),
            follow_up=None,
            interview_mode=_booked_mode(state),
            **meta,
        )
    return state, End(reason=EndReason.CLOSED, **meta)
