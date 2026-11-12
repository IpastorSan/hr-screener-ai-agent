"""Analytics against hand-built conversations whose metrics are worked out by hand below."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from riders import SPEC
from sazon.agents.riders import Close, FieldName, Step
from sazon.api.analytics import Filters, compute_analytics, furthest_step
from sazon.engine import (
    ConversationState,
    DisqualifyReason,
)
from sazon.engine.models import CandidateProfile, Language, Outcome, QualityFlag, Sentiment, Stage
from sazon.engine.spec import AgentSpec, FieldSpec, FieldType, Gate, GateOutcome, agent, use
from sazon.engine.state import Closed, Disqualified
from sazon.sentiment import SentimentStats
from sazon.service import ScreeningService
from sazon.storage.models import Candidate, Conversation, Summary, Turn, TurnRole
from sazon.storage.repo import Repo

MODEL = "openai/gpt-5-mini"
DAY = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)  # the service's clock in conftest


def conversation(
    repo: Repo,
    started_at: datetime,
    *,
    sentiment: str | None = None,
    channel: str = "web",
    last_activity_at: datetime | None = None,
    **state: Any,
) -> Conversation:
    parsed = ConversationState.model_validate(state)
    candidate_id = None
    if parsed.consented:
        candidate = repo.save(
            Candidate(
                channel=channel,
                sentiment=sentiment,
                profile=CandidateProfile().model_dump(mode="json"),
                created_at=started_at,
                updated_at=started_at,
            )
        )
        candidate_id = candidate.id
    return repo.save(
        Conversation(
            candidate_id=candidate_id,
            channel=channel,
            state=parsed.model_dump(mode="json"),
            outcome=parsed.outcome,
            drop_off_stage=parsed.drop_off_stage,
            quality_flags=[flag.value for flag in parsed.quality_flags],
            sentiment=SentimentStats().record(Sentiment(sentiment)).dump() if sentiment else None,
            started_at=started_at,
            last_activity_at=last_activity_at or started_at,
        )
    )


def turn(
    repo: Repo,
    conversation: Conversation,
    role: TurnRole,
    seconds: int,
    *,
    kind: str = "ask_field",
    model: str | None = None,
    latency_ms: int | None = None,
    cost_usd: float | None = None,
    fallback_used: bool = False,
    nudge: bool = False,
) -> None:
    repo.save(
        Turn(
            conversation_id=conversation.id,
            role=role.value,
            action={"kind": kind} if role == TurnRole.AGENT else None,
            model=model,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            fallback_used=fallback_used,
            nudge=nudge,
            created_at=conversation.started_at + timedelta(seconds=seconds),
        )
    )


def exchange(repo: Repo, c: Conversation, seconds: int, kind: str = "ask_field", **calls: Any):
    """A candidate message and the agent reply, stored with the same timestamp."""
    candidate = calls.pop("candidate", {})
    agent = calls.pop("agent", {})
    turn(repo, c, TurnRole.CANDIDATE, seconds, **candidate)
    turn(repo, c, TurnRole.AGENT, seconds, kind=kind, **agent)


def call(latency_ms: int, cost_usd: float, **extra: Any) -> dict[str, Any]:
    return {"model": MODEL, "latency_ms": latency_ms, "cost_usd": cost_usd, **extra}


@pytest.fixture
def fixtures(service: ScreeningService) -> Repo:
    repo = service.repo

    # c1: qualified in Spain, in Spanish. Measured calls: 3 exchanges + greeting + summary.
    c1 = conversation(repo, DAY, sentiment="positive", outcome=Outcome.QUALIFIED,
                      stage=Stage.SCHEDULING, country="ES", consented=True)  # fmt: skip
    turn(repo, c1, TurnRole.AGENT, 0, kind="greet", **call(500, 0.001))
    exchange(repo, c1, 60, candidate=call(400, 0.001), agent=call(600, 0.002))
    exchange(repo, c1, 120, candidate=call(400, 0.001), agent=call(800, 0.002))
    exchange(repo, c1, 300, "qualify", candidate=call(300, 0.001),
             agent=call(700, 0.002, fallback_used=True))  # fmt: skip
    repo.save(Summary(conversation_id=c1.id, body={}, model=MODEL, latency_ms=900,
                      cost_usd=0.003, created_at=DAY))  # fmt: skip

    # c2: no license, Mexico. Offline and fake calls are not measured.
    c2 = conversation(repo, DAY + timedelta(days=1), sentiment="neutral",
                      outcome=Outcome.DISQUALIFIED, disqualify_reason=Close.NO_LICENSE,
                      stage=Stage.DONE, country="MX", consented=True)  # fmt: skip
    offline = {"model": "offline", "latency_ms": 0}
    exchange(repo, c2, 60, candidate=offline, agent={"model": "fake", "latency_ms": 0})
    exchange(repo, c2, 100, "disqualify", candidate=offline)

    # c3: abandoned at experience, in English, flagged; answered after a nudge.
    c3 = conversation(repo, DAY, sentiment="frustrated", outcome=Outcome.ABANDONED,
                      drop_off_stage=Step.EXPERIENCE, stage=Stage.DONE, language=Language.EN,
                      quality_flags=[QualityFlag.UNKNOWN_FAQ, QualityFlag.FRUSTRATION],
                      consented=True)  # fmt: skip
    exchange(repo, c3, 60)
    turn(repo, c3, TurnRole.AGENT, 7200, kind="re_engage", nudge=True)
    exchange(repo, c3, 7300)

    # c4: abandoned at experience, never answered the nudge.
    c4 = conversation(repo, DAY, sentiment="neutral", outcome=Outcome.ABANDONED,
                      drop_off_stage=Step.EXPERIENCE, stage=Stage.DONE,
                      consented=True)  # fmt: skip
    exchange(repo, c4, 60)
    turn(repo, c4, TurnRole.AGENT, 7200, kind="re_engage", nudge=True)

    # c5: declined the privacy notice.
    conversation(repo, DAY, outcome=Outcome.DECLINED_CONSENT, stage=Stage.DONE)
    # c6: still answering, at the city question, Mexico.
    conversation(repo, DAY, sentiment="confused", stage=Step.CITY, awaiting=FieldName.CITY,
                 country="MX", consented=True,
                 last_activity_at=NOW - timedelta(hours=71))  # fmt: skip
    # c7: handed over to an open application: not counted.
    conversation(repo, DAY, stage=Stage.DONE, duplicate_of="someone", consented=True)
    # c8: qualified before the date range used below.
    conversation(repo, DAY - timedelta(days=9), sentiment="positive",
                 outcome=Outcome.QUALIFIED, stage=Stage.SCHEDULING, consented=True)  # fmt: skip
    return repo


def analytics(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/analytics", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_metrics_match_hand_computed_values(client: TestClient, fixtures: Repo):
    data = analytics(client, **{"from": "2026-10-05"})

    assert data["totals"] == {
        "started": 6,
        "consented": 5,
        "completed": 2,
        "qualified": 1,
        "disqualified": 1,
        "abandoned": 2,
        "withdrawn": 0,
        "declined_consent": 1,
        "in_progress": 1,
    }
    assert data["rates"] == pytest.approx(
        {
            "completion_rate": 2 / 5,
            "qualified_rate": 1 / 5,
            "abandonment_rate": 2 / 5,
            "withdrawal_rate": 0.0,
            "consent_decline_rate": 1 / 6,
        }
    )
    # Furthest steps: qualified, license, experience x2, consent, city.
    assert [(step["stage"], step["count"]) for step in data["funnel"]] == [
        ("consent", 6),
        ("name", 5),
        ("contact", 5),
        ("license", 5),
        ("city", 4),
        ("availability", 3),
        ("schedule", 3),
        ("experience", 3),
        ("start_date", 1),
        ("confirm", 1),
        ("qualified", 1),
    ]
    assert data["drop_off"] == [{"stage": "experience", "count": 2}]
    assert data["top_drop_off_stage"] == "experience"
    # c1: 300 s and 3 messages; c2: 100 s and 2 messages.
    assert data["durations"] == {
        "completed": 2,
        "avg_seconds": 200.0,
        "p50_seconds": 200.0,
        "avg_turns": 2.5,
        "p50_turns": 2.5,
    }
    assert data["disqualification_reasons"] == [{"reason": "no_license", "count": 1}]
    assert data["reengagement"] == {"nudged": 2, "recovered": 1, "recovery_rate": 0.5}
    assert data["sentiment"] == [
        {"sentiment": "neutral", "count": 2},
        {"sentiment": "positive", "count": 1},
        {"sentiment": "confused", "count": 1},
        {"sentiment": "frustrated", "count": 1},
    ]


def test_breakdowns(client: TestClient, fixtures: Repo):
    data = analytics(client, **{"from": "2026-10-05"})

    # c5 declined consent: its bare record keeps no country or language.
    spain, mexico = data["by_country"]
    assert spain == pytest.approx(
        {"key": "ES", "started": 3, "consented": 3, "completed": 1, "qualified": 1,
         "abandoned": 2, "completion_rate": 1 / 3, "qualified_rate": 1 / 3}
    )  # fmt: skip
    assert mexico == {"key": "MX", "started": 2, "consented": 2, "completed": 1, "qualified": 0,
                      "abandoned": 0, "completion_rate": 0.5, "qualified_rate": 0.0}  # fmt: skip
    assert [(row["key"], row["started"]) for row in data["by_language"]] == [("es", 4), ("en", 1)]
    assert [(row["key"], row["started"]) for row in data["by_channel"]] == [("web", 6)]


def test_quality_and_usage(client: TestClient, fixtures: Repo):
    quality = analytics(client, **{"from": "2026-10-05"})["quality"]

    assert quality["flagged"] == 1
    assert quality["flagged_rate"] == pytest.approx(1 / 6)
    assert quality["flags"][:2] == [  # ties keep the enum order
        {"flag": "frustration", "count": 1},
        {"flag": "unknown_faq", "count": 1},
    ]
    assert {flag["flag"] for flag in quality["flags"]} == set(agent().quality_flags)
    # 7 measured turns on c1 plus its summary; exchanges 400+600, 400+800, 300+700. c1 is the
    # only conversation with measured calls, so it carries the whole spend.
    assert quality["usage"] == pytest.approx(
        {
            "model_calls": 8,
            "fallback_rate": 1 / 8,
            "total_cost_usd": 0.013,
            "cost_per_conversation_usd": 0.013,
            "cost_per_completed_usd": 0.013,
            "exchanges": 3,
            "avg_latency_ms": 3200 / 3,
            "p50_latency_ms": 1000.0,
            "p95_latency_ms": 1200.0,
        }
    )


def test_cost_ratios_leave_out_conversations_without_measured_calls(
    client: TestClient, service: ScreeningService
):
    """`client` creates the tables."""
    repo = service.repo
    seeded = {"model": "fake", "latency_ms": 0}
    fixed = {"model": "template", "latency_ms": 0}  # a reply sent as written by design

    def located(c: Conversation, location_id: str) -> None:
        candidate = repo.get_candidate(c.candidate_id or "")
        assert candidate is not None
        candidate.location_id = location_id
        repo.save(candidate)

    # Seeded: two completed and one abandoned, fake model, no cost.
    closes: list[dict[str, Any]] = [
        {"outcome": Outcome.QUALIFIED},
        {"outcome": Outcome.DISQUALIFIED, "disqualify_reason": Close.NO_LICENSE},
        {"outcome": Outcome.ABANDONED, "drop_off_stage": Step.NAME},
    ]
    for closed in closes:
        c = conversation(repo, DAY, stage=Stage.DONE, consented=True, **closed)
        located(c, "es-mad-centro")
        exchange(repo, c, 60, candidate=seeded, agent=seeded)
    # Real, completed in Madrid: a fixed reply, then a reply rejected and sent as the template.
    madrid = conversation(repo, DAY, outcome=Outcome.QUALIFIED, stage=Stage.DONE, consented=True)
    located(madrid, "es-mad-centro")
    exchange(repo, madrid, 60, candidate=call(400, 0.002), agent=fixed)
    exchange(repo, madrid, 120, candidate=call(400, 0.002),
             agent=call(600, 0.004, fallback_used=True))  # fmt: skip
    # Real, abandoned in Guadalajara.
    guadalajara = conversation(repo, DAY, outcome=Outcome.ABANDONED, stage=Stage.DONE,
                               drop_off_stage=Step.NAME, consented=True)  # fmt: skip
    located(guadalajara, "mx-gdl-centro")
    exchange(repo, guadalajara, 60, candidate=call(300, 0.001), agent=call(500, 0.003))

    usage = compute_analytics(repo, Filters(), NOW).quality.usage
    assert usage.model_calls == 5  # the fixed reply is not a model call
    assert usage.fallback_rate == pytest.approx(1 / 5)
    assert usage.total_cost_usd == pytest.approx(0.012)
    assert usage.cost_per_conversation_usd == pytest.approx(0.012 / 2)
    assert usage.cost_per_completed_usd == pytest.approx(0.012 / 1)

    madrid_only = compute_analytics(repo, Filters(location_ids=frozenset({"es-mad-centro"})), NOW)
    assert madrid_only.totals.started == 4
    assert madrid_only.quality.usage.cost_per_conversation_usd == pytest.approx(0.008)
    assert madrid_only.quality.usage.cost_per_completed_usd == pytest.approx(0.008)

    gdl_only = compute_analytics(repo, Filters(location_ids=frozenset({"mx-gdl-centro"})), NOW)
    assert gdl_only.quality.usage.cost_per_conversation_usd == pytest.approx(0.004)
    assert gdl_only.quality.usage.cost_per_completed_usd is None  # nothing measured completed

    seeded_only = compute_analytics(repo, Filters(location_ids=frozenset({"nowhere"})), NOW)
    assert seeded_only.quality.usage.cost_per_conversation_usd is None


def test_filters(client: TestClient, fixtures: Repo):
    assert analytics(client)["totals"]["started"] == 7
    early = analytics(client, to="2026-10-04")
    assert early["totals"]["started"] == 1
    assert early["totals"]["qualified"] == 1
    assert analytics(client, **{"from": "2026-10-11", "to": "2026-10-11"})["totals"]["started"] == 1

    mexico = analytics(client, country="MX")
    assert mexico["totals"]["started"] == 2
    assert mexico["durations"]["p50_seconds"] == 100.0
    english = analytics(client, language="en")
    assert english["totals"]["abandoned"] == 1
    assert english["reengagement"]["recovery_rate"] == 1.0


def test_empty_range_has_null_rates(client: TestClient, fixtures: Repo):
    data = analytics(client, channel="voice")

    assert data["totals"]["started"] == 0
    assert set(data["rates"].values()) == {None}
    assert data["top_drop_off_stage"] is None
    assert data["durations"]["avg_seconds"] is None
    assert data["reengagement"] == {"nudged": 0, "recovered": 0, "recovery_rate": None}
    assert data["quality"]["usage"]["avg_latency_ms"] is None
    assert data["quality"]["usage"]["total_cost_usd"] is None


@pytest.mark.parametrize("params", [{"from": "yesterday"}, {"country": "FR"}, {"language": "fr"}])
def test_rejects_bad_filters(client: TestClient, params: dict[str, str]):
    assert client.get("/api/analytics", params=params).status_code == 422


async def test_durations_cover_every_completed_seeded_screening():
    from sazon.api.seed import seed
    from sazon.storage.repo import init_db, make_engine

    now = datetime(2026, 10, 19, 12, tzinfo=UTC)
    engine = make_engine("sqlite://")
    init_db(engine)
    repo = Repo(engine)
    await seed(repo, 40, now=now)
    result = compute_analytics(repo, Filters(), now=now)
    assert result.totals.completed > 0
    assert result.durations.completed == result.totals.completed


@pytest.mark.parametrize(
    ("profile", "step"),
    [
        (CandidateProfile(), Step.NAME),
        (CandidateProfile(full_name="Ana", phone="+34612345678"), Step.LICENSE),
        (
            CandidateProfile(
                full_name="Ana", phone="+34612345678", has_license=True, location_id="es-mad-centro"
            ),
            Step.AVAILABILITY,
        ),
    ],
)
def test_a_policy_close_stops_the_funnel_where_it_happened(profile, step):
    closed = Closed(variant=Disqualified(reason=DisqualifyReason.POLICY_VIOLATION))
    state = ConversationState(phase=closed, profile=profile)

    assert furthest_step(state) == step


def test_the_rider_funnel_comes_from_the_field_order_steps_and_gates():
    steps = SPEC.funnel
    assert steps.steps == (
        "consent",
        "name",
        "contact",
        "license",
        "city",
        "availability",
        "schedule",
        "experience",
        "start_date",
        "confirm",
        "qualified",
    )
    assert steps.closes_at == {
        Close.NO_LICENSE: Step.LICENSE,
        Close.LICENSE_IN_PROGRESS: Step.LICENSE,
        Close.LICENSE_NOT_VALID: Step.LICENSE,
        Close.OUT_OF_AREA: Step.CITY,
        Close.UNRESOLVED_NAME: Step.NAME,
        DisqualifyReason.NO_CONTACT: Step.CONTACT,
        Close.UNRESOLVED_LICENSE: Step.LICENSE,
        Close.UNRESOLVED_CITY: Step.CITY,
    }
    assert [field.attr for field in steps.firsts.values()] == [
        "full_name",
        "phone",
        "has_license",
        "location_id",
        "availability",
        "schedule",
        "experience_years",
        "start_date",
    ]
    assert steps.first_gate == Step.LICENSE


def test_another_spec_has_its_own_funnel():
    """A gate first in the asking order, a step of two fields, and no contact field: stored in
    the riders' profile."""
    school = AgentSpec(
        agent="school",
        fields=(
            FieldSpec(
                id="certificate",
                type=FieldType.CREDENTIAL,
                funnel_step=Step.LICENSE,
                stored_as=("has_license",),
                gate=Gate(
                    readings={"no": GateOutcome.CLOSE},
                    reasons={"no": Close.NO_LICENSE},
                ),
            ),
            FieldSpec(
                id="name",
                type=FieldType.PERSON_NAME,
                funnel_step=Step.NAME,
                stored_as=("full_name",),
            ),
            FieldSpec(id="experience_years", type=FieldType.YEARS, funnel_step=Step.NAME),
        ),
    )
    steps = school.funnel
    assert steps.steps == ("consent", "license", "name", "confirm", "qualified")
    assert steps.closes_at == {Close.NO_LICENSE: Step.LICENSE}
    assert [field.id for field in steps.firsts.values()] == ["certificate", "name"]
    with use(school):
        closed = Closed(variant=Disqualified(reason=DisqualifyReason.POLICY_VIOLATION))
        state = ConversationState(phase=closed, profile=CandidateProfile(has_license=True))
        assert furthest_step(state) == Step.NAME


def test_license_not_valid_stops_at_the_license():
    closed = Closed(variant=Disqualified(reason=Close.LICENSE_NOT_VALID))
    state = ConversationState(phase=closed)

    assert furthest_step(state) == Step.LICENSE


def test_withdrawn_is_its_own_bucket_counted_as_completed_not_qualified(
    client: TestClient, service: ScreeningService
):
    """A candidate who cancelled their interview: completed, not qualified nor disqualified, and
    the funnel stops them at the read-back so its last step matches the qualified total."""
    repo = service.repo
    qualified = conversation(repo, DAY, outcome=Outcome.QUALIFIED, stage=Stage.DONE, consented=True)
    withdrawn = conversation(repo, DAY, outcome=Outcome.WITHDRAWN, stage=Stage.DONE, consented=True)
    exchange(repo, qualified, 100, "offer_slots")
    exchange(repo, withdrawn, 200, "offer_slots")

    result = compute_analytics(repo, Filters(), NOW)

    totals = result.totals
    assert (totals.completed, totals.qualified, totals.disqualified) == (2, 1, 0)
    assert (totals.withdrawn, totals.abandoned) == (1, 0)
    assert result.rates.completion_rate == 1.0
    assert result.rates.qualified_rate == 0.5
    assert result.rates.withdrawal_rate == 0.5
    steps = {step.stage: step.count for step in result.funnel}
    assert (steps["confirm"], steps["qualified"]) == (2, 1)
    assert result.durations.completed == 2
    assert result.drop_off == []
