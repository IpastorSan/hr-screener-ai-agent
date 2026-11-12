"""Stage 1 operations: health, the daily budget, rate limits, production guards, retention."""

import logging
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlmodel import select

from sazon.api import health
from sazon.api.app import UnsafeProductionConfig, create_app
from sazon.cli import main
from sazon.config import Settings, get_settings
from sazon.engine.actions import Greet
from sazon.engine.models import CandidateProfile, Language
from sazon.engine.state import ConversationState
from sazon.guards import SpendGuard, TokenBucket, client_ip
from sazon.llm import FakeLLM, ReplyContext
from sazon.llm.budget import BUDGET_ERROR, BudgetedLLM
from sazon.llm.fallback_templates import pack
from sazon.service import ScreeningService
from sazon.storage.migrate import head_revision
from sazon.storage.models import (
    Booking,
    Candidate,
    CandidateReview,
    Conversation,
    InterviewSlot,
    Summary,
    Turn,
)
from sazon.storage.repo import Repo, init_db, make_engine
from sazon.storage.retention import purge

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)  # the clock in tests/api/conftest.py
CONTEXT = {"country": "ES", "timezone": "Europe/Madrid", "locale": "es-ES"}


class Ticker:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def spend(repo: Repo, usd: float, at: datetime = NOW) -> None:
    init_db(repo.engine)
    conversation = repo.save(
        Conversation(channel="web", state={}, started_at=at, last_activity_at=at)
    )
    repo.save(Turn(conversation_id=conversation.id, role="agent", cost_usd=usd, created_at=at))


# --- health ---


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_shows_the_status_alone_and_logs_the_failing_check(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="sazon.api.health"):
        response = client.get("/readyz")
    # The tests run without a retrieval index, so readiness fails on it alone.
    assert response.status_code == 503
    assert response.json() == {"status": "fail"}
    (record,) = [r for r in caplog.records if r.name == "sazon.api.health"]
    assert "'rag'" in record.getMessage()
    assert "database" not in record.getMessage()


class ReadyIndex:
    ready = True

    def retrieve(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def describe(self) -> str:
        return "2 Q&A entries"


def test_readyz_ok_with_a_loaded_index_and_a_migrated_file_database(tmp_path) -> None:
    repo = Repo(make_engine(f"sqlite:///{tmp_path / 'ready.db'}"))
    service = ScreeningService(FakeLLM(), repo, clock=lambda: NOW, faq=ReadyIndex())
    spend(repo, 0.4)
    app = create_app(service)
    with TestClient(app) as client:
        app.state.spend_guard = SpendGuard(repo, 2.0, clock=lambda: NOW)
        response = client.get("/readyz")
    assert response.status_code == 200
    # The day's spend and cap are Prometheus gauges, never in the public body.
    assert response.json() == {"status": "ok"}
    assert health.database_check(service) == health.Check(
        ok=True, detail=f"schema {head_revision()}"
    )


class DownProbe:
    calls = 0

    async def __call__(self) -> health.Check:
        self.calls += 1
        return health.Check(ok=False, detail="ConnectError")


def test_an_openrouter_outage_does_not_fail_readiness(tmp_path) -> None:
    repo = Repo(make_engine(f"sqlite:///{tmp_path / 'ready.db'}"))
    service = ScreeningService(FakeLLM(), repo, clock=lambda: NOW, faq=ReadyIndex())
    probe = DownProbe()
    app = create_app(service)
    with TestClient(app) as client:
        app.state.openrouter_probe = probe
        plain = client.get("/readyz")
        deep = client.get("/readyz?deep=1")
    assert plain.status_code == 200
    assert deep.status_code == 503
    assert deep.json() == {"status": "fail"}
    assert probe.calls == 1


async def test_openrouter_probe_is_cached_for_a_minute() -> None:
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(200 if len(calls) == 1 else 500, json={"data": []})

    ticker = Ticker()
    probe = health.OpenRouterProbe(
        "https://openrouter.test/api/v1/",
        "sk-test",
        monotonic=ticker,
        transport=httpx2.MockTransport(handler),
    )
    assert (await probe()).ok
    ticker.t = 59
    assert (await probe()).ok
    assert len(calls) == 1
    assert str(calls[0].url) == "https://openrouter.test/api/v1/models"
    assert calls[0].headers["authorization"] == "Bearer sk-test"
    ticker.t = 61
    check = await probe()
    assert not check.ok
    assert check.detail == "HTTP 500"
    assert "sk-test" not in check.detail


# --- budget ---


def test_spend_guard_sums_today_and_caches(service: ScreeningService) -> None:
    ticker = Ticker()
    guard = SpendGuard(service.repo, 1.0, clock=lambda: NOW, monotonic=ticker)
    spend(service.repo, 0.4)
    spend(service.repo, 5.0, at=NOW - timedelta(days=1))
    assert guard.spent_today() == pytest.approx(0.4)
    assert not guard.over_budget()
    spend(service.repo, 0.7)
    assert not guard.over_budget()  # cached
    ticker.t = 31
    assert guard.over_budget()
    guard.add(0.1)
    assert guard.spent_today() == pytest.approx(1.2)


def test_no_cap_never_blocks(service: ScreeningService) -> None:
    spend(service.repo, 1000)
    assert not SpendGuard(service.repo, None, clock=lambda: NOW).over_budget()


def test_new_chats_come_back_later_once_the_budget_is_spent(service: ScreeningService) -> None:
    guard = SpendGuard(service.repo, 0.5, clock=lambda: NOW)
    spend(service.repo, 0.6)
    with TestClient(create_app(service, spend_guard=guard)) as client:
        response = client.post("/api/conversations", json={"context": CONTEXT})
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == ""
    assert body["reply"] == {"text": pack("es-ES").busy, "ui": None}
    assert body["state"]["ended"] is True
    with service.repo.session() as session:
        assert len(session.exec(select(Conversation)).all()) == 1  # only the one spending


async def test_budgeted_llm_falls_back_to_templates_past_the_cap() -> None:
    over = False
    primary = FakeLLM(replies=["from the model"])
    llm = BudgetedLLM(primary, lambda: over)
    state = ConversationState(country="ES")
    action = Greet(language=Language.ES, country="ES")
    ctx = ReplyContext.for_action(action, today=NOW.date())
    first = await llm.respond(action, ctx)
    assert first.reply.text == "from the model"
    assert not first.call.fallback_used

    over = True
    second = await llm.respond(action, ctx)
    assert second.reply.text != "from the model"
    assert second.call.fallback_used
    assert second.call.error == BUDGET_ERROR
    extracted = await llm.extract([], state, "si")
    assert not extracted.call.fallback_used  # the keyword reader moves the chat on
    summary = await llm.summarize([], CandidateProfile())
    assert summary.call.fallback_used
    assert primary.messages == []


async def test_budgeted_llm_checks_the_budget_off_the_event_loop() -> None:
    loop_thread = threading.get_ident()
    checked_on: list[int] = []

    def over_budget() -> bool:
        checked_on.append(threading.get_ident())
        return False

    llm = BudgetedLLM(FakeLLM(replies=["hola"]), over_budget)
    action = Greet(language=Language.ES, country="ES")
    await llm.respond(action, ReplyContext.for_action(action, today=NOW.date()))
    assert checked_on
    assert loop_thread not in checked_on


def test_spend_guard_reads_without_holding_its_lock(service: ScreeningService) -> None:
    """A slow read must not block `add` (or another caller's cached answer)."""
    guard = SpendGuard(service.repo, 1.0, clock=lambda: NOW)
    reading, release = threading.Event(), threading.Event()

    def slow_read() -> float:
        reading.set()
        release.wait(5)
        return 0.0

    guard._read = slow_read  # type: ignore[method-assign]
    worker = threading.Thread(target=guard.spent_today)
    worker.start()
    try:
        assert reading.wait(5)
        adder = threading.Thread(target=guard.add, args=(0.1,))
        adder.start()
        adder.join(1)
        assert not adder.is_alive()
    finally:
        release.set()
        worker.join(5)


# --- rate limits ---


def test_token_bucket_refills() -> None:
    ticker = Ticker()
    bucket = TokenBucket(2, 60, monotonic=ticker)
    assert bucket.take("a") is None
    assert bucket.take("a") is None
    wait = bucket.take("a")
    assert wait == pytest.approx(30)
    assert bucket.take("b") is None
    ticker.t = 30
    assert bucket.take("a") is None
    assert TokenBucket(0, 60).take("a") is None


def test_client_ip_trusts_only_the_proxy_entry() -> None:
    assert client_ip("6.6.6.6, 203.0.113.9", "172.18.0.5") == "203.0.113.9"
    assert client_ip(None, "172.18.0.5") == "172.18.0.5"
    assert client_ip("", None) == "unknown"


def test_new_chats_are_rate_limited_per_ip(
    service: ScreeningService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_CONVERSATIONS_PER_HOUR", "2")
    _fresh_settings(monkeypatch)
    with TestClient(create_app(service)) as client:
        mine = {"x-forwarded-for": "203.0.113.9"}
        other = {"x-forwarded-for": "198.51.100.7"}
        for _ in range(2):
            assert client.post("/api/conversations", json={}, headers=mine).status_code == 200
        limited = client.post("/api/conversations", json={}, headers=mine)
        assert limited.status_code == 429
        assert limited.json()["error"] == "rate_limited"
        assert int(limited.headers["retry-after"]) >= 1
        assert client.post("/api/conversations", json={}, headers=other).status_code == 200


def test_messages_are_rate_limited_per_ip(
    service: ScreeningService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_MESSAGES_PER_MINUTE", "1")
    _fresh_settings(monkeypatch)
    with TestClient(create_app(service)) as client:
        conversation = client.post("/api/conversations", json={}).json()["conversation_id"]
        url = f"/api/conversations/{conversation}/messages"
        headers = {"accept": "application/json"}
        assert client.post(url, json={"text": "si"}, headers=headers).status_code == 200
        assert client.post(url, json={"text": "si"}, headers=headers).status_code == 429


def test_nudges_share_the_message_rate_limit(
    service: ScreeningService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATE_LIMIT_MESSAGES_PER_MINUTE", "1")
    _fresh_settings(monkeypatch)
    with TestClient(create_app(service)) as client:
        conversation = client.post("/api/conversations", json={}).json()["conversation_id"]
        url = f"/api/conversations/{conversation}/nudge"
        assert client.post(url, json={"attempt": 1}).status_code == 409  # before consent
        limited = client.post(url, json={"attempt": 1})
        assert limited.status_code == 429
        assert limited.json()["error"] == "rate_limited"


# --- production guards ---


STRONG_SECRET = "s" * 48


@pytest.mark.parametrize(
    ("fake", "key", "secret", "problem"),
    [
        (True, "sk-real", STRONG_SECRET, "SAZON_FAKE_LLM"),
        (False, "", STRONG_SECRET, "OPENROUTER_API_KEY"),
        (False, "sk-real", "", "SESSION_SECRET"),
        (False, "sk-real", "too-short", "SESSION_SECRET"),
    ],
)
def test_production_refuses_demo_settings(fake: bool, key: str, secret: str, problem: str) -> None:
    settings = Settings(
        env="production",
        sazon_fake_llm=fake,
        openrouter_api_key=SecretStr(key),
        session_secret=SecretStr(secret),
    )
    problems = settings.production_problems()
    assert len(problems) == 1
    assert problem in problems[0]
    development = Settings(env="development", sazon_fake_llm=True)
    assert development.production_problems() == []


def test_the_api_does_not_boot_in_production_with_the_fake_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SAZON_FAKE_LLM", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret-value")
    _fresh_settings(monkeypatch)
    with pytest.raises(UnsafeProductionConfig) as error:
        create_app()
    assert "sk-secret-value" not in str(error.value)


def test_seed_reset_is_refused_in_production(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = f"sqlite:///{tmp_path / 'live.db'}"
    spend(Repo(make_engine(database)), 0.1)
    monkeypatch.setenv("ENV", "production")
    get_settings.cache_clear()

    assert main(["--db", database, "seed", "--reset"]) == 2

    assert "production" in capsys.readouterr().err
    with Repo(make_engine(database)).session() as session:
        assert len(session.exec(select(Turn)).all()) == 1


def test_a_rate_limit_without_a_period_is_refused() -> None:
    with pytest.raises(ValueError, match="period"):
        TokenBucket(5, 0)
    assert TokenBucket(0, 0).take("a") is None  # capacity 0: the limit is off


def _fresh_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the environment again; `_clear_settings_cache` forgets it after the test."""
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- retention ---


def test_purge_deletes_applications_idle_past_retention() -> None:
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    now = datetime(2026, 10, 19, tzinfo=UTC)
    old, recent = now - timedelta(days=400), now - timedelta(days=10)

    def application(last: datetime, *more: datetime) -> Candidate:
        candidate = repo.save(Candidate(channel="web", created_at=last, updated_at=last))
        for moment in (last, *more):
            conversation = repo.save(
                Conversation(
                    candidate_id=candidate.id,
                    channel="web",
                    state={},
                    started_at=moment,
                    last_activity_at=moment,
                )
            )
            repo.save(Turn(conversation_id=conversation.id, role="agent", created_at=moment))
            repo.save(Summary(conversation_id=conversation.id, body={}, created_at=moment))
        return candidate

    gone = application(old)
    slot = repo.save(
        InterviewSlot(location_id="mad-centro", starts_at=old, ends_at=old + timedelta(hours=1))
    )
    gone_conversation = repo.latest_conversation(gone.id)
    assert gone_conversation is not None
    repo.save(
        Booking(
            candidate_id=gone.id,
            conversation_id=gone_conversation.id,
            slot_id=slot.id,
            created_at=old,
            updated_at=old,
        )
    )
    repo.save(CandidateReview(candidate_id=gone.id, updated_at=old))
    kept = application(old, recent)  # came back recently: the whole application stays
    repo.save(Conversation(channel="web", state={}, started_at=old, last_activity_at=old))

    assert purge(repo, now, 365, dry_run=True).candidates == 1
    report = purge(repo, now, 365)
    assert (report.candidates, report.conversations) == (1, 2)
    assert repo.get_candidate(gone.id) is None
    assert repo.get_candidate(kept.id) is not None
    with repo.session() as session:
        assert len(session.exec(select(Conversation)).all()) == 2
        assert session.exec(select(Booking)).all() == []
        assert session.exec(select(CandidateReview)).all() == []
