"""Tracing (span tree, PII masking, no-op when unconfigured, prompt version), metrics and JSON
logs."""

import json
import logging
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NoOpTracer
from prometheus_client import REGISTRY
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from riders import SPEC
from sazon import telemetry
from sazon.agents import agent_dir
from sazon.agents.loader import load_agent
from sazon.api.app import create_app
from sazon.config import Settings
from sazon.engine import ConversationContext
from sazon.engine.models import (
    FieldCandidate,
    Intent,
    Language,
    MatrixLanguage,
    Sentiment,
    TurnExtraction,
)
from sazon.engine.spec import AgentSpec, agent
from sazon.llm import FakeLLM
from sazon.logs import JsonFormatter
from sazon.observability import record
from sazon.pii import redact
from sazon.service import ScreeningService
from sazon.storage.repo import Repo, init_db, make_engine

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
CONTEXT = ConversationContext(country="ES", timezone="Europe/Madrid", locale="es-ES")
NAME = "Lucía García"
PHONE = "612345678"
EMAIL = "lucia@example.com"
CONTACT_MESSAGE = f"Soy {NAME}, mi móvil es {PHONE} y mi correo {EMAIL}"
TURN_SPANS = {"sanitize", "extract", "engine.step", "fill_action", "respond", "persist"}


def ex(intent: Intent = Intent.ANSWER, **fields: Any) -> TurnExtraction:
    return TurnExtraction(
        intent=intent,
        fields={
            name: FieldCandidate(value=value, confidence=0.95) for name, value in fields.items()
        },
        language=Language.ES,
        matrix_language=MatrixLanguage.ES,
        english_ratio=0.0,
        code_switched=False,
        sentiment=Sentiment.NEUTRAL,
    )


def settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


@pytest.fixture
def exporter() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    assert telemetry.configure(settings(), exporter=exporter)
    yield exporter
    telemetry.shutdown()


def service(llm: FakeLLM) -> ScreeningService:
    repo = Repo(make_engine("sqlite://"))
    init_db(repo.engine)
    return ScreeningService(llm, repo, clock=lambda: NOW)


def all_text(span: ReadableSpan) -> str:
    return json.dumps(dict(span.attributes or {}), ensure_ascii=False, default=str)


def sample(name: str, labels: dict[str, str] | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


# --- masking ---


def test_redact_masks_contact_details_but_not_dates() -> None:
    text = redact(f"{NAME} +34 612 345 678 {EMAIL} on 2026-10-19", [NAME])
    assert text == "[redacted] [phone] [email] on 2026-10-19"


def test_redact_masks_json_escaped_names() -> None:
    assert redact(json.dumps({"name": NAME}), [NAME]) == '{"name": "[redacted]"}'


def test_masking_processor_waits_for_the_root_to_learn_the_name(
    exporter: InMemorySpanExporter,
) -> None:
    with telemetry.start_trace("candidate-turn", session_id="conv-123456789") as trace:
        with telemetry.span("extract") as child:
            child.set_attribute("langfuse.observation.output", json.dumps({"full_name": NAME}))
            child.add_event("note", {"text": f"call {PHONE}"})
        assert exporter.get_finished_spans() == ()  # held until the root ends
        telemetry.add_known_pii([NAME])
        trace.io(input=CONTACT_MESSAGE)
    spans = {span.name: span for span in exporter.get_finished_spans()}
    extract = all_text(spans["extract"])
    assert NAME not in extract
    assert "[redacted]" in extract
    assert spans["extract"].events[0].attributes == {"text": "call [phone]"}
    root = spans["candidate-turn"].attributes or {}
    assert root["langfuse.trace.input"] == "Soy [redacted], mi móvil es [phone] y mi correo [email]"
    assert root["session.id"] == "conv-123456789"  # ids are never masked


# --- tracing ---


def test_tracing_is_a_no_op_without_an_endpoint() -> None:
    assert not telemetry.configure(settings())
    assert not telemetry.enabled()
    assert isinstance(telemetry.tracer(), NoOpTracer)
    with telemetry.start_trace("candidate-turn", session_id="c1") as trace:
        trace.tag("country:ES")
        with telemetry.span("extract") as child:
            telemetry.set_io(child, input="text")
    telemetry.add_known_pii([NAME])  # nothing to register with
    assert Agent._instrument_default is False


def test_tracing_needs_both_langfuse_keys() -> None:
    configured = settings(
        otel_exporter_otlp_endpoint="http://langfuse-web:3000/api/public/otel",
        langfuse_public_key="pk-lf-x",
    )
    assert not telemetry.configure(configured)


async def test_one_turn_exports_the_span_tree_with_session_and_masked_phone(
    exporter: InMemorySpanExporter,
) -> None:
    screening = service(FakeLLM([ex(Intent.CONFIRM), ex(full_name=NAME, phone=PHONE, email=EMAIL)]))
    started = await screening.start(context=CONTEXT)
    conversation_id = started.conversation_id
    await screening.handle_turn(conversation_id, "sí")
    exporter.clear()

    await screening.handle_turn(conversation_id, CONTACT_MESSAGE)

    spans = exporter.get_finished_spans()
    names = {span.name for span in spans}
    assert names >= TURN_SPANS | {"candidate-turn"}
    root = next(span for span in spans if span.name == "candidate-turn")
    assert root.context is not None
    assert all(span.context and span.context.trace_id == root.context.trace_id for span in spans)
    for span in spans:
        attributes = span.attributes or {}
        assert attributes["session.id"] == conversation_id
        assert attributes["prompt_version"] == telemetry.prompt_version()
        assert attributes["langfuse.trace.name"] == "candidate-turn"
        text = all_text(span)
        assert PHONE not in text
        assert NAME not in text
        assert EMAIL not in text
    root_attributes = root.attributes or {}
    # Known values (from the extraction) are masked by value, the rest by pattern.
    assert root_attributes["langfuse.trace.input"] == (
        "Soy [redacted], mi móvil es [redacted] y mi correo [redacted]"
    )
    assert len(str(root_attributes["user.id"])) == 16
    tags = set(root_attributes["langfuse.trace.tags"])  # pyright: ignore[reportArgumentType]
    assert {"country:ES", "language:es", "channel:web", "outcome:open"} <= tags
    by_id = {span.context.span_id: span for span in spans if span.context}
    for span in spans:
        if span.name in TURN_SPANS:
            assert span.parent is not None
            assert by_id[span.parent.span_id].name == "candidate-turn"


async def test_pydantic_ai_spans_are_masked(exporter: InMemorySpanExporter) -> None:
    agent = Agent(TestModel(custom_output_text=f"Llama al {PHONE}, {NAME}"))
    with telemetry.start_trace("recruiter-assistant"):
        telemetry.add_known_pii([NAME])
        await agent.run(f"Escribe a {EMAIL}")
    spans = exporter.get_finished_spans()
    chat = [span for span in spans if span.name.startswith("chat")]
    assert chat, [span.name for span in spans]
    text = " ".join(all_text(span) for span in spans)
    for mask in ("[phone]", "[email]", "[redacted]"):
        assert mask in text
    for value in (PHONE, EMAIL, NAME):
        assert value not in text


async def test_trace_content_off_keeps_messages_out() -> None:
    exporter = InMemorySpanExporter()
    telemetry.configure(settings(trace_content=False), exporter=exporter)
    try:
        agent = Agent(TestModel(custom_output_text="hola"))
        with telemetry.start_trace("candidate-turn") as trace:
            trace.io(input="mensaje del candidato")
            await agent.run("mensaje del candidato")
    finally:
        telemetry.shutdown()
    text = " ".join(all_text(span) for span in exporter.get_finished_spans())
    assert "mensaje del candidato" not in text
    assert "hola" not in text


# --- metrics ---


async def test_a_turn_increments_the_metrics() -> None:
    before = {
        "started": sample("sazon_conversations_started_total", {"country": "ES", "channel": "web"}),
        "candidate": sample("sazon_turns_total", {"role": "candidate"}),
        "calls": sample("sazon_llm_calls_total", {"role": "extractor", "model": "fake"}),
        "fallback": sample("sazon_llm_fallback_total", {"role": "extractor"}),
        "latency": sample(
            "sazon_llm_latency_seconds_count", {"role": "responder", "model": "fake"}
        ),
    }
    screening = service(FakeLLM([ex(Intent.CONFIRM)]))
    started = await screening.start(context=CONTEXT)
    await screening.handle_turn(started.conversation_id, "sí")
    await screening.handle_turn(started.conversation_id, "?")  # queue empty: a fallback

    assert (
        sample("sazon_conversations_started_total", {"country": "ES", "channel": "web"})
        == before["started"] + 1
    )
    assert sample("sazon_turns_total", {"role": "candidate"}) == before["candidate"] + 2
    calls = sample("sazon_llm_calls_total", {"role": "extractor", "model": "fake"})
    assert calls == before["calls"] + 2
    assert sample("sazon_llm_fallback_total", {"role": "extractor"}) == before["fallback"] + 1
    latency = sample("sazon_llm_latency_seconds_count", {"role": "responder", "model": "fake"})
    assert latency == before["latency"] + 3


def test_a_fixed_reply_is_not_counted_as_a_model_call() -> None:
    """QA-B6: a reply sent as written by design is no model call: counted, it would dilute the
    fallback rate and pull the latency p95 the alerts read towards 0."""
    from sazon.llm.interface import FIXED_REPLY_MODEL, CallRecord, Role
    from sazon.metrics import observe_call

    model = {"role": "responder", "model": FIXED_REPLY_MODEL}
    before = {
        "calls": sample("sazon_llm_calls_total", model),
        "latency": sample("sazon_llm_latency_seconds_count", model),
        "fixed": sample("sazon_fixed_replies_total", {"role": "responder"}),
    }
    observe_call(CallRecord(role=Role.RESPONDER, model=FIXED_REPLY_MODEL))

    assert sample("sazon_llm_calls_total", model) == before["calls"]
    assert sample("sazon_llm_latency_seconds_count", model) == before["latency"]
    assert sample("sazon_fixed_replies_total", {"role": "responder"}) == before["fixed"] + 1


def test_metrics_endpoint_serves_every_metric() -> None:
    llm = FakeLLM([ex(Intent.CONFIRM)])
    with TestClient(create_app(service(llm))) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        record("rate_limited", bucket="message_limit")
        body = client.get("/metrics").text
    for name in (
        "sazon_turns_total",
        "sazon_llm_latency_seconds_bucket",
        "sazon_llm_fallback_total",
        "sazon_llm_cost_usd_total",
        "sazon_fixed_replies_total",
        "sazon_conversations_started_total",
        "sazon_conversation_outcomes_total",
        "sazon_quality_flags_total",
        "sazon_faq_miss_total",
        "sazon_nudges_served_total",
        "sazon_nudge_recoveries_total",
        "sazon_openrouter_errors_total",
        "sazon_spend_cap_blocks_total",
        "sazon_rate_limited_total",
        "sazon_spend_today_usd",
    ):
        assert f"# TYPE {name.removesuffix('_total').removesuffix('_bucket')}" in body, name
    assert 'sazon_rate_limited_total{bucket="message_limit"}' in body


def test_openrouter_errors_are_counted_by_status() -> None:
    from pydantic_ai.exceptions import ModelHTTPError

    from sazon.metrics import count_openrouter_error

    before = sample("sazon_openrouter_errors_total", {"status": "402"})
    count_openrouter_error(ModelHTTPError(402, "anthropic/claude-haiku-5.5"))
    count_openrouter_error(ValueError("bad output"))  # not OpenRouter's
    assert sample("sazon_openrouter_errors_total", {"status": "402"}) == before + 1


def test_an_httpx_timeout_is_counted_as_a_timeout() -> None:
    """httpx's timeouts are not TimeoutError subclasses."""
    import httpx

    from sazon.metrics import count_openrouter_error

    before = sample("sazon_openrouter_errors_total", {"status": "timeout"})
    count_openrouter_error(httpx.ReadTimeout("read"))
    assert sample("sazon_openrouter_errors_total", {"status": "timeout"}) == before + 1


# --- logs ---


def test_json_logs_are_masked_and_carry_the_conversation() -> None:
    formatter = JsonFormatter()
    record_ = logging.LogRecord("sazon", logging.INFO, __file__, 1, "from %s", (EMAIL,), None)
    with telemetry.start_trace("candidate-turn", session_id="conv-1"):
        line = json.loads(formatter.format(record_))
    assert line["msg"] == "from [email]"
    assert line["conversation_id"] == "conv-1"
    assert line["level"] == "info"


def test_probe_access_lines_are_dropped() -> None:
    from sazon.logs import QuietProbes

    def access(path: str, status: int) -> logging.LogRecord:
        args = ("10.0.0.1:1", "GET", path, "1.1", status)
        return logging.LogRecord(
            "uvicorn.access", logging.INFO, "", 1, '%s - "%s %s HTTP/%s" %d', args, None
        )

    quiet = QuietProbes()
    assert not quiet.filter(access("/metrics", 200))
    assert not quiet.filter(access("/readyz", 200))
    assert quiet.filter(access("/readyz", 503))
    assert quiet.filter(access("/api/conversations", 200))


# --- prompt version ---


def _teachers(tmp_path: Path, tone: str | None = None) -> AgentSpec:
    """A copy of the teacher agent, its `tone` prompt slot reworded when given."""
    directory = tmp_path / "teachers"
    shutil.copytree(Path(str(agent_dir("teachers"))), directory)
    if tone is not None:
        path = directory / "texts" / "prompts.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["slots"]["tone"] = tone
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return load_agent(directory)


def test_the_prompt_version_follows_the_wording_the_model_gets(tmp_path: Path) -> None:
    """The version hashes the prompts filled for the agent, so a change to an agent's
    `texts/prompts.yaml` changes it though no template does."""
    teachers = load_agent(agent_dir("teachers"))
    copy = _teachers(tmp_path / "copy")
    reworded = _teachers(tmp_path / "reworded", tone="like a strict head of studies")
    assert telemetry.prompt_version(copy) == telemetry.prompt_version(teachers)
    assert telemetry.prompt_version(reworded) != telemetry.prompt_version(teachers)
    assert telemetry.prompt_version(teachers) != telemetry.prompt_version(SPEC)
    assert telemetry.prompt_version() == telemetry.prompt_version(agent())
