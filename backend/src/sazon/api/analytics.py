"""Screening analytics for the recruiter dashboard.

Every metric counts conversations started in the date range (`started_at`, UTC days, both ends
included) that match the channel, country and language filters. A conversation that handed over
to the candidate's open application (duplicate check) is not counted: the application's own
conversation already is.

Shared definitions:
- **started**: conversations opened, whether or not the candidate answered.
- **consented**: conversations where the candidate accepted the privacy notice. Screening rates
  use this as the denominator, since nothing is asked before consent.
- **completed**: conversations with a screening result: qualified, disqualified or withdrawn. A
  withdrawn candidate finished the screening and qualified, then cancelled their interview (or
  opted out with it booked): the completion rate counts them, the qualified rate does not.
- **withdrawn** (retirada): its own bucket, neither a qualification nor a disqualification. In
  the funnel it reaches the read-back (confirm), not the qualified step, so the funnel's last
  step matches the qualified total.
- **abandoned**: closed for silence, opt-out or abuse, plus conversations still in progress but
  idle for more than 72 hours, at the stage they stopped (worked out at read time, no job).
"""

import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlmodel import col, select

from sazon.api.deps import CurrentRecruiter, Now, RepoDep, require_recruiter
from sazon.engine import ConversationState, DisqualifyReason, ReasonId
from sazon.engine.models import Language, Outcome, QualityFlag, Sentiment, Stage, StepId
from sazon.engine.spec import QUALIFIED_STEP, Funnel, agent
from sazon.engine.validators import Country
from sazon.llm.fake import FAKE_MODEL
from sazon.llm.interface import FIXED_REPLY_MODEL, Channel
from sazon.llm.offline import OFFLINE_MODEL
from sazon.recruiters import in_scope, location_scope
from sazon.reengagement.idle import effective_state
from sazon.sentiment import SentimentStats
from sazon.storage.models import Candidate, Conversation, Summary, Turn, TurnRole
from sazon.storage.repo import Repo, to_utc

router = APIRouter(
    prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(require_recruiter)]
)

# The agent turn that gives the screening result. A qualified candidate is answered with
# the interview-mode question, or the slot offer when there is one mode (or the saved
# application when no slot is free; `offer_talent_pool` before SAZ-42); `qualify` is kept for
# rows written before scheduling replaced it.
COMPLETING_ACTIONS = frozenset(
    {
        "qualify",
        "find_modes",
        "ask_mode",
        "find_slots",
        "offer_slots",
        "application_saved",
        "offer_talent_pool",
        "disqualify",
    }
)
IN_PROGRESS = "in_progress"
# Outcomes with a screening result. Withdrawn finished the screening, qualified, and then
# cancelled the interview.
COMPLETED = frozenset({Outcome.QUALIFIED, Outcome.DISQUALIFIED, Outcome.WITHDRAWN})
# No real call: the local stand-ins, and the replies sent as written by design (fixed replies).
UNMEASURED_MODELS = frozenset({OFFLINE_MODEL, FAKE_MODEL, FIXED_REPLY_MODEL})


# --- response ---


class Totals(BaseModel):
    """Conversations by outcome. `in_progress` has no outcome yet, including chats still at
    the privacy notice."""

    started: int
    consented: int
    completed: int
    qualified: int
    disqualified: int
    abandoned: int
    withdrawn: int
    declined_consent: int
    in_progress: int


class Rates(BaseModel):
    """Shares from 0 to 1, or null when the denominator is zero."""

    completion_rate: float | None = Field(description="completed / consented")
    qualified_rate: float | None = Field(description="qualified / consented")
    abandonment_rate: float | None = Field(description="abandoned / consented")
    withdrawal_rate: float | None = Field(description="withdrawn / consented")
    consent_decline_rate: float | None = Field(
        description="declined consent / (consented + declined consent): of those who answered "
        "the privacy notice, the share who said no"
    )


class FunnelStep(BaseModel):
    stage: str = Field(description="A screening stage, or `qualified` for the last step")
    count: int = Field(description="Conversations that reached this step")


class StageCount(BaseModel):
    stage: StepId
    count: int


class ReasonCount(BaseModel):
    reason: ReasonId
    count: int


class Durations(BaseModel):
    """Time and candidate messages from the start of the chat to the screening result, over
    completed conversations. Null when none completed."""

    completed: int
    avg_seconds: float | None
    p50_seconds: float | None
    avg_turns: float | None
    p50_turns: float | None


class ReEngagement(BaseModel):
    """Nudges sent to candidates who stopped answering (SAZ-17).

    `recovery_rate` is null while no conversation has been nudged: before re-engagement ships
    there is nothing to measure, which is not the same as 0%.
    """

    nudged: int = Field(description="Conversations with at least one nudge")
    recovered: int = Field(description="Nudged conversations where the candidate wrote again")
    recovery_rate: float | None = Field(description="recovered / nudged")


class Breakdown(BaseModel):
    key: str
    started: int
    consented: int
    completed: int
    qualified: int
    abandoned: int
    completion_rate: float | None = Field(description="completed / consented")
    qualified_rate: float | None = Field(description="qualified / consented")


class SentimentCount(BaseModel):
    sentiment: Sentiment
    count: int


class FlagCount(BaseModel):
    flag: QualityFlag
    count: int


class Usage(BaseModel):
    """Model cost and speed, from the call records on turns and summaries. The offline and fake
    stand-ins and the fixed replies make no real call and are left out, and so are the
    conversations without a measured call (seeded or offline ones); the rates and averages are
    null when no measured call exists."""

    model_calls: int = Field(description="Measured model calls (extraction, reply, summary)")
    fallback_rate: float | None = Field(
        description="Model calls that failed (error, timeout, invalid output) or whose reply was "
        "rejected, so the template was sent instead / model calls. A reply over the budget cap "
        "or fixed by design makes no call and is not counted"
    )
    total_cost_usd: float | None
    cost_per_conversation_usd: float | None = Field(
        description="total cost / started conversations with at least one measured call"
    )
    cost_per_completed_usd: float | None = Field(
        description="total cost / completed conversations with at least one measured call: "
        "every measured conversation's spend, abandoned ones included, carried by the "
        "screenings that produced a result"
    )
    exchanges: int = Field(description="Candidate messages answered with measured model calls")
    avg_latency_ms: float | None = Field(
        description="Mean time spent in model calls per candidate message (extraction + reply)"
    )
    p50_latency_ms: float | None
    p95_latency_ms: float | None


class Quality(BaseModel):
    """Chatbot QA. Flags describe the bot's performance, never the candidate."""

    flagged: int = Field(description="Conversations with at least one quality flag")
    flagged_rate: float | None = Field(description="flagged / started")
    flags: list[FlagCount]
    usage: Usage


class AnalyticsOut(BaseModel):
    totals: Totals
    rates: Rates
    funnel: list[FunnelStep]
    drop_off: list[StageCount] = Field(description="Abandoned conversations by last stage")
    top_drop_off_stage: StepId | None = Field(
        description="The stage most abandoned conversations stopped at (earliest on a tie)"
    )
    durations: Durations
    disqualification_reasons: list[ReasonCount]
    reengagement: ReEngagement
    by_channel: list[Breakdown]
    by_country: list[Breakdown] = Field(
        description="Declined consents keep no country, so they count in the totals only"
    )
    by_language: list[Breakdown] = Field(
        description="Declined consents keep no language, so they count in the totals only"
    )
    sentiment: list[SentimentCount] = Field(
        description="Consented conversations by their peak sentiment, the most severe label "
        "read from the candidate's messages (frustrated > confused > neutral > positive); "
        "widget taps carry no label, and a conversation without a read message is left out"
    )
    quality: Quality


@dataclass(frozen=True)
class Filters:
    start: date | None = None
    end: date | None = None
    channel: Channel | None = None
    country: Country | None = None
    language: Language | None = None
    location_ids: frozenset[str] | None = None
    """Only the candidates at these locations (a recruiter's scope, `recruiters.location_scope`);
    None for all, conversations without a candidate included."""


@router.get("", response_model=AnalyticsOut)
async def get_analytics(
    repo: RepoDep,
    now: Now,
    me: CurrentRecruiter,
    start: Annotated[date | None, Query(alias="from", description="First day, UTC")] = None,
    end: Annotated[date | None, Query(alias="to", description="Last day, UTC")] = None,
    channel: Channel | None = None,
    country: Country | None = None,
    language: Language | None = None,
) -> AnalyticsOut:
    """Screening metrics for conversations started between `from` and `to`, of the candidates
    at the recruiter's locations (every conversation for an admin)."""
    filters = Filters(start, end, channel, country, language, location_scope(me))
    return compute_analytics(repo, filters, now)


# --- computing ---


@dataclass(frozen=True)
class Row:
    conversation: Conversation
    state: ConversationState
    outcome: str

    @property
    def consented(self) -> bool:
        return self.state.consented

    @property
    def completed(self) -> bool:
        return self.outcome in COMPLETED


def compute_analytics(repo: Repo, filters: Filters, now: datetime | None = None) -> AnalyticsOut:
    rows = _rows(repo, filters, to_utc(now or datetime.now(UTC)))
    known = [row for row in rows if not _bare(row.conversation)]
    ids = [row.conversation.id for row in rows]
    with repo.session() as session:
        turns = session.exec(
            select(Turn).where(col(Turn.conversation_id).in_(ids)).order_by(col(Turn.id))
        ).all()
        summaries = session.exec(select(Summary).where(col(Summary.conversation_id).in_(ids))).all()
    turns_by_conversation: dict[str, list[Turn]] = defaultdict(list)
    for turn in turns:
        turns_by_conversation[turn.conversation_id].append(turn)

    totals = _totals(rows)
    steps = agent().funnel
    return AnalyticsOut(
        totals=totals,
        rates=Rates(
            completion_rate=_ratio(totals.completed, totals.consented),
            qualified_rate=_ratio(totals.qualified, totals.consented),
            abandonment_rate=_ratio(totals.abandoned, totals.consented),
            withdrawal_rate=_ratio(totals.withdrawn, totals.consented),
            consent_decline_rate=_ratio(
                totals.declined_consent, totals.consented + totals.declined_consent
            ),
        ),
        funnel=funnel(rows, steps),
        drop_off=(drop_off := _drop_off(rows, steps)),
        top_drop_off_stage=max(drop_off, key=lambda item: item.count).stage if drop_off else None,
        durations=_durations(rows, turns_by_conversation),
        disqualification_reasons=_reasons(rows),
        reengagement=_reengagement(turns_by_conversation),
        by_channel=_breakdown(rows, lambda row: row.conversation.channel),
        by_country=_breakdown(known, lambda row: row.state.country),
        by_language=_breakdown(known, lambda row: row.state.language.value),
        sentiment=_sentiment(rows),
        quality=Quality(
            flagged=(flagged := sum(1 for row in rows if row.state.quality_flags)),
            flagged_rate=_ratio(flagged, len(rows)),
            flags=_flags(rows),
            usage=_usage(turns, summaries, rows),
        ),
    )


def _rows(repo: Repo, filters: Filters, now: datetime) -> list[Row]:
    query = select(Conversation)
    if filters.start is not None:
        query = query.where(col(Conversation.started_at) >= day_start(filters.start))
    if filters.end is not None:
        query = query.where(
            col(Conversation.started_at) < day_start(filters.end + timedelta(days=1))
        )
    if filters.channel is not None:
        query = query.where(col(Conversation.channel) == filters.channel)
    if filters.location_ids is not None:
        query = query.join(Candidate, col(Candidate.id) == col(Conversation.candidate_id)).where(
            in_scope(filters.location_ids)
        )
    with repo.session() as session:
        conversations = session.exec(query).all()
    rows = []
    for conversation in conversations:
        state = effective_state(conversation, now)
        if state.duplicate_of is not None and state.outcome is None:
            continue  # handed over to the open application, counted there
        if state.restarted:
            continue  # started again with the restart button: counted in the new chat
        if _bare(conversation) and (filters.country or filters.language):
            continue  # a declined consent keeps no country or language
        if filters.country is not None and state.country != filters.country:
            continue
        if filters.language is not None and state.language != filters.language:
            continue
        outcome = state.outcome.value if state.outcome else IN_PROGRESS
        rows.append(Row(conversation, state, outcome))
    return rows


def _bare(conversation: Conversation) -> bool:
    """A declined consent: the record keeps no country or language (`ScreeningService._decline`)."""
    return conversation.outcome == Outcome.DECLINED_CONSENT and not conversation.context


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _totals(rows: Sequence[Row]) -> Totals:
    outcomes = Counter(row.outcome for row in rows)
    return Totals(
        started=len(rows),
        consented=sum(1 for row in rows if row.consented),
        completed=sum(outcomes[outcome] for outcome in COMPLETED),
        qualified=outcomes[Outcome.QUALIFIED],
        disqualified=outcomes[Outcome.DISQUALIFIED],
        abandoned=outcomes[Outcome.ABANDONED],
        withdrawn=outcomes[Outcome.WITHDRAWN],
        declined_consent=outcomes[Outcome.DECLINED_CONSENT],
        in_progress=outcomes[IN_PROGRESS],
    )


def furthest_step(state: ConversationState, steps: Funnel | None = None) -> str:
    """The last funnel step a conversation reached. Steps run in a fixed order, so reaching a
    step means having passed every step before it.

    Qualified reached every step. Disqualified stopped at the step whose field closed it (a
    gate or a last try), abandoned at its drop-off stage, an open conversation at its current
    stage. A declined privacy notice stopped at consent. A withdrawn one passed every question
    but is not qualified: it stops at the read-back.
    """
    steps = steps or agent().funnel
    if state.outcome == Outcome.QUALIFIED:
        return QUALIFIED_STEP
    if state.outcome == Outcome.WITHDRAWN:
        return Stage.CONFIRM
    if state.outcome == Outcome.DISQUALIFIED:
        reason = state.disqualify_reason
        if reason == DisqualifyReason.POLICY_VIOLATION:
            return _policy_step(state, steps)
        return steps.closes_at.get(reason, steps.first_gate) if reason else steps.first_gate
    if state.outcome == Outcome.ABANDONED:
        return state.drop_off_stage or Stage.CONSENT
    if state.outcome is None and state.stage in steps.steps:
        return state.stage
    return Stage.CONSENT


def _policy_step(state: ConversationState, steps: Funnel) -> str:
    for field in steps.firsts.values():
        if field.value(state.profile) is None:
            return field.funnel_step
    return Stage.CONFIRM


def funnel(rows: Sequence[Row], steps: Funnel) -> list[FunnelStep]:
    """How many conversations reached each step, consent through qualified."""
    reached = Counter(steps.steps.index(furthest_step(row.state, steps)) for row in rows)
    counts = []
    remaining = len(rows)
    for index, step in enumerate(steps.steps):
        counts.append(FunnelStep(stage=step, count=remaining))
        remaining -= reached[index]
    return counts


def _drop_off(rows: Sequence[Row], steps: Funnel) -> list[StageCount]:
    """Abandoned conversations by the stage they stopped at, in funnel order, zeros left out."""
    stages = Counter(
        row.state.drop_off_stage or Stage.CONSENT
        for row in rows
        if row.outcome == Outcome.ABANDONED
    )
    order = [step for step in steps.steps if step != QUALIFIED_STEP]
    return [StageCount(stage=stage, count=stages[stage]) for stage in order if stages[stage]]


def _durations(rows: Sequence[Row], turns: dict[str, list[Turn]]) -> Durations:
    """Measured to the agent turn that gave the result (see `COMPLETING_ACTIONS`); turns are the
    candidate messages up to it."""
    seconds: list[float] = []
    messages: list[int] = []
    for row in rows:
        if not row.completed:
            continue
        conversation_turns = turns.get(row.conversation.id, [])
        result = next(
            (
                turn
                for turn in conversation_turns
                if turn.action and turn.action.get("kind") in COMPLETING_ACTIONS
            ),
            None,
        )
        if result is None:
            continue
        started = to_utc(row.conversation.started_at)
        seconds.append((to_utc(result.created_at) - started).total_seconds())
        messages.append(
            sum(
                1
                for turn in conversation_turns
                if turn.role == TurnRole.CANDIDATE and (turn.id or 0) < (result.id or 0)
            )
        )
    return Durations(
        completed=len(seconds),
        avg_seconds=statistics.fmean(seconds) if seconds else None,
        p50_seconds=statistics.median(seconds) if seconds else None,
        avg_turns=statistics.fmean(messages) if messages else None,
        p50_turns=statistics.median(messages) if messages else None,
    )


def _reasons(rows: Sequence[Row]) -> list[ReasonCount]:
    reasons = Counter(
        row.state.disqualify_reason
        for row in rows
        if row.outcome == Outcome.DISQUALIFIED and row.state.disqualify_reason
    )
    return [ReasonCount(reason=reason, count=count) for reason, count in reasons.most_common()]


def _reengagement(turns: dict[str, list[Turn]]) -> ReEngagement:
    nudged = recovered = 0
    for conversation_turns in turns.values():
        first_nudge = next((turn.id for turn in conversation_turns if turn.nudge), None)
        if first_nudge is None:
            continue
        nudged += 1
        if any(
            turn.role == TurnRole.CANDIDATE and (turn.id or 0) > first_nudge
            for turn in conversation_turns
        ):
            recovered += 1
    return ReEngagement(nudged=nudged, recovered=recovered, recovery_rate=_ratio(recovered, nudged))


def _breakdown(rows: Sequence[Row], key_of: Callable[[Row], str]) -> list[Breakdown]:
    """Totals and rates per group, the largest group first."""
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        groups[key_of(row)].append(row)
    result = []
    for key, members in groups.items():
        totals = _totals(members)
        result.append(
            Breakdown(
                key=key,
                started=totals.started,
                consented=totals.consented,
                completed=totals.completed,
                qualified=totals.qualified,
                abandoned=totals.abandoned,
                completion_rate=_ratio(totals.completed, totals.consented),
                qualified_rate=_ratio(totals.qualified, totals.consented),
            )
        )
    return sorted(result, key=lambda item: (-item.started, item.key))


def _sentiment(rows: Sequence[Row]) -> list[SentimentCount]:
    """Consented conversations by their peak label; one with no message read (taps only) is
    left out."""
    counts = Counter(
        SentimentStats.load(row.conversation.sentiment).peak for row in rows if row.consented
    )
    return [SentimentCount(sentiment=s, count=counts[s]) for s in Sentiment]


def _flags(rows: Sequence[Row]) -> list[FlagCount]:
    """Conversations carrying each flag, every flag the agent can raise listed (a gate's only
    when one of its gates flags), most frequent first."""
    counts = Counter(flag for row in rows for flag in row.state.quality_flags)
    flags = [FlagCount(flag=flag, count=counts[flag]) for flag in agent().quality_flags]
    return sorted(flags, key=lambda item: -item.count)


def _measured(model: str | None, latency_ms: int | None) -> bool:
    return model is not None and model not in UNMEASURED_MODELS and latency_ms is not None


def _usage(turns: Sequence[Turn], summaries: Sequence[Summary], rows: Sequence[Row]) -> Usage:
    calls: Iterable[Turn | Summary] = [*turns, *summaries]
    measured = [call for call in calls if _measured(call.model, call.latency_ms)]
    costs = [call.cost_usd for call in measured if call.cost_usd is not None]
    total_cost = sum(costs) if costs else None
    # Seeded and offline conversations cost nothing; dividing by them would dilute the spend.
    with_calls = {call.conversation_id for call in measured}
    measured_rows = [row for row in rows if row.conversation.id in with_calls]

    # The candidate turn and the agent reply of one exchange are stored with the same timestamp.
    exchanges: dict[tuple[str, datetime], list[Turn]] = defaultdict(list)
    for turn in turns:
        exchanges[(turn.conversation_id, turn.created_at)].append(turn)
    latencies = sorted(
        float(sum(turn.latency_ms or 0 for turn in group if _measured(turn.model, turn.latency_ms)))
        for group in exchanges.values()
        if any(turn.role == TurnRole.CANDIDATE for turn in group)
        and any(_measured(turn.model, turn.latency_ms) for turn in group)
    )
    return Usage(
        model_calls=len(measured),
        fallback_rate=_ratio(sum(1 for call in measured if call.fallback_used), len(measured)),
        total_cost_usd=total_cost,
        cost_per_conversation_usd=_divide(total_cost, len(measured_rows)),
        cost_per_completed_usd=_divide(
            total_cost, sum(1 for row in measured_rows if row.completed)
        ),
        exchanges=len(latencies),
        avg_latency_ms=statistics.fmean(latencies) if latencies else None,
        p50_latency_ms=statistics.median(latencies) if latencies else None,
        p95_latency_ms=_percentile(latencies, 0.95),
    )


def _divide(total: float | None, count: int) -> float | None:
    return total / count if total is not None and count else None


def _percentile(ordered: Sequence[float], share: float) -> float | None:
    """Nearest-rank percentile of an ascending list."""
    if not ordered:
        return None
    rank = max(1, -(-len(ordered) * share // 1))
    return ordered[int(rank) - 1]
