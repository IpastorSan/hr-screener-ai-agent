"""Tracing over OpenTelemetry, exported to self-hosted Langfuse. A no-op unless configured.

One trace per candidate message (`candidate-turn`), chat start (`candidate-start`), nudge
(`candidate-nudge`) and recruiter question (`recruiter-assistant`). Trace-level attributes
(session, hashed user id, tags, prompt version) are copied onto every span of the trace, as
Langfuse v4 reads them per observation.

Personal data never leaves the process unmasked: spans are held until their trace's local root
ends, then emails, phone numbers and the candidate's known name, phone and email are masked in
every string attribute, event and status message before export. `TRACE_CONTENT=false` keeps
prompts, model output and candidate text out of spans altogether.
"""

import base64
import hashlib
import json
import logging
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from importlib.resources import files
from typing import TYPE_CHECKING, Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from sazon.config import Settings, get_settings
from sazon.pii import redact

if TYPE_CHECKING:
    from fastapi import FastAPI

    from sazon.engine.spec import AgentSpec

logger = logging.getLogger(__name__)

TRACE_NAME = "langfuse.trace.name"
SESSION_ID = "session.id"
USER_ID = "user.id"
TAGS = "langfuse.trace.tags"
TRACE_INPUT = "langfuse.trace.input"
TRACE_OUTPUT = "langfuse.trace.output"
INPUT = "langfuse.observation.input"
OUTPUT = "langfuse.observation.output"
PROMPT_VERSION = "prompt_version"
LANGFUSE_VERSION = "langfuse.version"
ENVIRONMENT = "langfuse.environment"
# Identifiers, never personal data: masking could only damage them (a hashed id has digit runs).
UNMASKED = frozenset({
    TRACE_NAME, SESSION_ID, USER_ID, TAGS, PROMPT_VERSION, LANGFUSE_VERSION, ENVIRONMENT,
    "sazon.conversation_id", "gen_ai.request.model", "gen_ai.response.model",
    "gen_ai.response.id", "http.route",
})  # fmt: skip
# Spans held per trace before masking; past it the trace's spans go out with what is known.
MAX_BUFFERED_SPANS = 5_000
EXCLUDED_URLS = "healthz,readyz,metrics"

type Attributes = dict[str, Any]


_versions: dict[int, tuple["AgentSpec", str]] = {}


def prompt_version(spec: "AgentSpec | None" = None) -> str:
    """Short hash of what the models are given for `spec` (by default the agent in use), so
    Langfuse can compare runs by prompt version: every prompt filled with the agent's slots and
    fields, and the extractor's output schema. A change to a template, to an agent's
    `texts/prompts.yaml` or to its fields changes it. Computed once per spec."""
    from sazon.engine.spec import agent
    from sazon.llm.extraction import output_model
    from sazon.llm.prompting import prompt

    spec = spec or agent()
    if id(spec) not in _versions:
        digest = hashlib.sha256()
        templates = files("sazon.llm.prompts").iterdir()
        for name in sorted(entry.name for entry in templates if entry.name.endswith(".md")):
            digest.update(name.encode())
            digest.update(prompt(name, spec).encode())
        schema = output_model(spec).model_json_schema()
        digest.update(json.dumps(schema, sort_keys=True).encode())
        _versions[id(spec)] = (spec, digest.hexdigest()[:10])
    return _versions[id(spec)][1]


def hashed_id(value: str) -> str:
    """A stable pseudonymous id for traces: the candidate id itself never leaves."""
    return hashlib.sha256(f"sazon-candidate:{value}".encode()).hexdigest()[:16]


# --- span processors ---


_trace_attributes: ContextVar[Attributes | None] = ContextVar("sazon_trace_attrs", default=None)


class TraceAttributes(SpanProcessor):
    """Copies the current trace's attributes and the prompt version onto every new span."""

    def __init__(self, environment: str) -> None:
        self.environment = environment

    def on_start(self, span: Any, parent_context: otel_context.Context | None = None) -> None:
        span.set_attribute(PROMPT_VERSION, prompt_version())
        span.set_attribute(LANGFUSE_VERSION, prompt_version())
        span.set_attribute(ENVIRONMENT, self.environment)
        attributes = _trace_attributes.get()
        if attributes:
            span.set_attributes(attributes)


class MaskingProcessor(SpanProcessor):
    """Holds a trace's spans until its local root ends, masks them, then hands them on."""

    def __init__(self, inner: SpanProcessor, *, max_buffered: int = MAX_BUFFERED_SPANS) -> None:
        self.inner = inner
        self.max_buffered = max_buffered
        self._spans: dict[int, list[ReadableSpan]] = {}
        self._known: dict[int, set[str]] = {}
        self._lock = threading.Lock()

    def add_known(self, trace_id: int, values: Iterable[str]) -> None:
        with self._lock:
            self._known.setdefault(trace_id, set()).update(v for v in values if v)

    def on_start(self, span: Any, parent_context: otel_context.Context | None = None) -> None:
        self.inner.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        if span.context is None:
            return
        trace_id = span.context.trace_id
        local_root = span.parent is None or span.parent.is_remote
        with self._lock:
            spans = self._spans.setdefault(trace_id, [])
            spans.append(span)
            if not local_root and len(spans) < self.max_buffered:
                return
            del self._spans[trace_id]
            known = self._known.pop(trace_id, set()) if local_root else self._known.get(trace_id)
            known = set(known or ())
        for held in spans:
            self.inner.on_end(mask_span(held, known))

    def _flush_all(self) -> None:
        with self._lock:
            pending = [(spans, set(self._known.get(tid, ()))) for tid, spans in self._spans.items()]
            self._spans.clear()
            self._known.clear()
        for spans, known in pending:
            for held in spans:
                self.inner.on_end(mask_span(held, known))

    def shutdown(self) -> None:
        self._flush_all()
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self._flush_all()
        return self.inner.force_flush(timeout_millis)


def mask_value(value: Any, known: Iterable[str]) -> Any:
    if isinstance(value, str):
        return redact(value, known)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(redact(v, known) if isinstance(v, str) else v for v in value)
    return value


def mask_attributes(attributes: Mapping[str, Any] | None, known: Iterable[str]) -> Attributes:
    known = tuple(known)
    return {
        key: value if key in UNMASKED else mask_value(value, known)
        for key, value in (attributes or {}).items()
    }


def mask_span(span: ReadableSpan, known: Iterable[str]) -> ReadableSpan:
    known = tuple(known)
    status = span.status
    if status.description:
        status = Status(status.status_code, redact(status.description, known))
    events = [
        Event(event.name, mask_attributes(event.attributes, known), event.timestamp)
        for event in span.events
    ]
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=mask_attributes(span.attributes, known),
        events=events,
        links=span.links,
        kind=span.kind,
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


# --- configuration ---


@dataclass
class _State:
    provider: TracerProvider | None = None
    masker: MaskingProcessor | None = None
    content: bool = True
    instrumented_apps: list[Any] = field(default_factory=list)


_state = _State()
_NOOP = trace.NoOpTracer()


def enabled() -> bool:
    return _state.provider is not None


def trace_content() -> bool:
    return enabled() and _state.content


def configure(settings: Settings | None = None, *, exporter: SpanExporter | None = None) -> bool:
    """Start tracing when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (or an `exporter` is given, in
    tests). Returns whether tracing is on. Pydantic AI agents are instrumented from here on."""
    settings = settings or get_settings()
    if _state.provider is not None:
        return True
    endpoint = settings.otel_exporter_otlp_endpoint.strip().rstrip("/")
    if exporter is None and not endpoint:
        return False
    if exporter is None:
        exporter = _otlp_exporter(settings, endpoint)
        if exporter is None:
            return False
        inner: SpanProcessor = BatchSpanProcessor(exporter)
    else:
        inner = SimpleSpanProcessor(exporter)
    resource = Resource.create({
        "service.name": settings.otel_service_name,
        "deployment.environment": settings.env,
    })  # fmt: skip
    provider = TracerProvider(resource=resource)
    masker = MaskingProcessor(inner)
    provider.add_span_processor(TraceAttributes(settings.env))
    provider.add_span_processor(masker)
    _state.provider = provider
    _state.masker = masker
    _state.content = settings.trace_content

    from pydantic_ai import Agent, InstrumentationSettings

    Agent.instrument_all(
        InstrumentationSettings(
            tracer_provider=provider,
            include_content=settings.trace_content,
            include_binary_content=False,
        )
    )
    logger.info(
        "Tracing to %s (content %s, prompt version %s)",
        endpoint or type(exporter).__name__,
        "on" if settings.trace_content else "off",
        prompt_version(),
    )
    return True


def _otlp_exporter(settings: Settings, endpoint: str) -> SpanExporter | None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    headers: dict[str, str] = {}
    public = settings.langfuse_public_key.strip()
    secret = settings.langfuse_secret_key.get_secret_value().strip()
    if bool(public) != bool(secret):
        logger.warning("Tracing off: set both LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.")
        return None
    if public:
        token = base64.b64encode(f"{public}:{secret}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    return OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers, timeout=10)


def instrument_app(app: "FastAPI") -> None:
    """HTTP server spans for the API (health and metrics endpoints left out)."""
    if _state.provider is None:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_state.provider,
        excluded_urls=EXCLUDED_URLS,
        exclude_spans=["receive", "send"],
    )
    _state.instrumented_apps.append(app)


def shutdown() -> None:
    """Flush and stop tracing; agents go back to uninstrumented. Safe when tracing is off."""
    provider = _state.provider
    if provider is None:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from pydantic_ai import Agent

    for app in _state.instrumented_apps:
        FastAPIInstrumentor.uninstrument_app(app)
    Agent.instrument_all(False)
    provider.shutdown()
    _state.provider = None
    _state.masker = None
    _state.content = True
    _state.instrumented_apps.clear()


def tracer() -> Tracer:
    if _state.provider is None:
        return _NOOP
    return _state.provider.get_tracer("sazon")


# --- spans ---


class TraceHandle:
    """The current trace's root span and the attributes copied onto each new span."""

    def __init__(self, span: Span, attributes: Attributes) -> None:
        self.span = span
        self.attributes = attributes
        self.root_io: Attributes = {}

    def set(self, key: str, value: Any) -> None:
        """A trace-level attribute, on the root and on every span started after this."""
        if value is None:
            return
        self.attributes[key] = value
        self.span.set_attribute(key, value)

    def tag(self, *tags: str | None) -> None:
        current = list(self.attributes.get(TAGS, ()))
        current.extend(tag for tag in tags if tag and tag not in current)
        self.set(TAGS, tuple(current))

    def user(self, candidate_id: str | None) -> None:
        if candidate_id:
            self.set(USER_ID, hashed_id(candidate_id))

    def io(self, *, input: str | None = None, output: str | None = None) -> None:
        """Trace input and output (the candidate's message, Ana's reply) when content is on."""
        if not trace_content():
            return
        if input is not None:
            self.root_io.update({TRACE_INPUT: input, INPUT: input})
        if output is not None:
            self.root_io.update({TRACE_OUTPUT: output, OUTPUT: output})
        self.span.set_attributes(self.root_io)


@contextmanager
def start_trace(
    name: str,
    *,
    session_id: str | None = None,
    candidate_id: str | None = None,
    tags: Iterable[str | None] = (),
) -> Iterator[TraceHandle]:
    """A trace named `name` (Langfuse's trace name), nested under the HTTP span if any.

    The HTTP span started before the trace's attributes were known. At the end it takes the
    trace's name (Langfuse v4 names a trace after its root observation), attributes, input and
    output, so the whole trace carries its name, session, user and tags."""
    attributes: Attributes = {TRACE_NAME: name}
    if session_id:
        attributes[SESSION_ID] = session_id
        attributes["sazon.conversation_id"] = session_id
    if candidate_id:
        attributes[USER_ID] = hashed_id(candidate_id)
    clean = tuple(dict.fromkeys(tag for tag in tags if tag))
    if clean:
        attributes[TAGS] = clean
    parent = trace.get_current_span()
    token = _trace_attributes.set(attributes)
    handle: TraceHandle | None = None
    try:
        with tracer().start_as_current_span(name) as span:
            handle = TraceHandle(span, attributes)
            yield handle
    finally:
        _trace_attributes.reset(token)
        if parent.is_recording():
            parent.update_name(name)
            parent.set_attributes(attributes)
            if handle is not None:
                parent.set_attributes(handle.root_io)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """A child span. `None` values are left out."""
    with tracer().start_as_current_span(
        name, attributes={k: v for k, v in attributes.items() if v is not None}
    ) as current:
        yield current


def set_io(current: Span, *, input: str | None = None, output: str | None = None) -> None:
    """Observation input and output on `current`, only when content tracing is on."""
    if not trace_content():
        return
    if input is not None:
        current.set_attribute(INPUT, input)
    if output is not None:
        current.set_attribute(OUTPUT, output)


def add_known_pii(values: Iterable[str | None]) -> None:
    """Personal values to mask in the current trace (the candidate's name, phone, email)."""
    masker = _state.masker
    if masker is None:
        return
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        masker.add_known(context.trace_id, (v for v in values if v))


def record_error(current: Span, message: str) -> None:
    current.set_status(Status(StatusCode.ERROR, message))


def current_ids() -> tuple[str, str] | None:
    """Hex trace and span ids of the current span, for log lines."""
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x"), format(context.span_id, "016x")


def current_conversation_id() -> str | None:
    attributes = _trace_attributes.get()
    value = attributes.get(SESSION_ID) if attributes else None
    return str(value) if value else None
