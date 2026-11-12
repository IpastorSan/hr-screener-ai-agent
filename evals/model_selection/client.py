"""OpenRouter access for the model benchmark.

Extraction and replies run through the production ``sazon.llm.PydanticAILLM`` (same prompts,
output mode and provider preferences), so the benchmark measures what ships. The judge and the
case generator call models directly. Every request goes through one httpx2 client whose request
hook refuses any chat completion that does not carry ``provider.zdr = true`` and
``provider.data_collection = "deny"``. Cost comes from OpenRouter's usage accounting
(``usage.cost``); when it is missing the client asks ``GET /generation?id=...`` and, failing
that, prices the tokens with ``GET /models``. Every call is settled in an append-only ledger with
a cumulative ``MAX_EVAL_USD`` ceiling.

Structured calls use tool-call output: through OpenRouter, Anthropic's native JSON-schema mode
returned ``TurnExtraction.fields`` empty on every case, back when it was an open dict.
"""

import asyncio
import json
import os
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import httpx2
from openai import APIError, AsyncOpenAI
from pydantic_ai import Agent, ToolOutput, capture_run_messages
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, RetryPromptPart
from pydantic_ai.models.openrouter import (
    OpenRouterModel,
    OpenRouterModelSettings,
    OpenRouterProviderConfig,
    OpenRouterReasoning,
)
from pydantic_ai.providers.openrouter import OpenRouterProvider

from sazon.config import Settings, get_settings
from sazon.engine.actions import Action
from sazon.engine.models import TurnExtraction
from sazon.engine.state import ConversationState
from sazon.llm.fallback_templates import fallback_reply
from sazon.llm.interface import CallRecord as LLMCallRecord
from sazon.llm.interface import ChatTurn, ReplyContext
from sazon.llm.pydantic_ai_impl import PydanticAILLM, respond_prompt
from sazon.llm.reply_checks import clean_reply

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

ZDR_PROVIDER: OpenRouterProviderConfig = {"zdr": True, "data_collection": "deny"}
RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
LEDGER_PATH = RESULTS_DIR / "cost_ledger.jsonl"
DEFAULT_CALL_RESERVE_USD = 0.02
# Generous: the benchmark records slow calls instead of letting the production timeout drop them.
EVAL_TIMEOUT_S = 120.0

Effort = Literal["none", "minimal", "low", "medium", "high"]
CostSource = Literal["usage", "generation", "pricing", "none"]


class ZdrViolationError(RuntimeError):
    pass


class BudgetExceededError(RuntimeError):
    pass


def enforce_zdr(request: httpx2.Request) -> None:
    """Refuse any completion request that does not pin ZDR and data_collection=deny."""
    if request.method != "POST":
        return
    try:
        body = json.loads(request.content or b"{}")
    except ValueError as exc:
        raise ZdrViolationError("request body is not JSON") from exc
    provider = body.get("provider") if isinstance(body, dict) else None
    if (
        not isinstance(provider, dict)
        or provider.get("zdr") is not True
        or provider.get("data_collection") != "deny"
    ):
        raise ZdrViolationError(f"request to {request.url.path} lacks zdr/data_collection=deny")


def _reraise_zdr(exc: BaseException) -> None:
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ZdrViolationError):
            raise cause
        cause = cause.__cause__ or cause.__context__


@dataclass(frozen=True)
class ModelSpec:
    """A model id plus an optional reasoning effort, written ``vendor/model@effort``."""

    model_id: str
    effort: Effort | None = None

    @classmethod
    def parse(cls, text: str) -> "ModelSpec":
        model_id, _, effort = text.strip().partition("@")
        if "/" not in model_id:
            raise ValueError(f"model id must be vendor/model: {text!r}")
        if effort and effort not in ("none", "minimal", "low", "medium", "high"):
            raise ValueError(f"unknown effort {effort!r} in {text!r}")
        return cls(model_id, effort or None)  # pyright: ignore[reportArgumentType]

    @property
    def label(self) -> str:
        return f"{self.model_id}@{self.effort}" if self.effort else self.model_id

    def reasoning(self) -> OpenRouterReasoning | None:
        if self.effort is None:
            return None
        if self.effort == "none":
            return {"enabled": False}
        return {"effort": self.effort, "exclude": True}


@dataclass
class CallRecord:
    model: str
    role: str
    case_id: str
    ok: bool
    latency_s: float
    ttft_s: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_source: CostSource = "none"
    provider: str | None = None
    requests: int = 0
    output_retries: int = 0
    error: str | None = None
    generation_ids: list[str] = field(default_factory=list)


class CostLedger:
    """Append-only JSONL of every paid call, with a hard cumulative ceiling."""

    def __init__(self, path: Path, max_usd: float) -> None:
        self.path = path
        self.max_usd = max_usd
        self._reserved = 0.0
        self._spent = self._read_total()

    def _read_total(self) -> float:
        if not self.path.exists():
            return 0.0
        total = 0.0
        for line in self.path.read_text().splitlines():
            if line.strip():
                total += float(json.loads(line)["cost_usd"])
        return total

    @property
    def spent(self) -> float:
        return self._spent

    def reserve(self, amount: float) -> None:
        if self._spent + self._reserved + amount > self.max_usd:
            raise BudgetExceededError(
                f"MAX_EVAL_USD={self.max_usd:.2f} reached "
                f"(spent {self._spent:.4f}, in flight {self._reserved:.4f})"
            )
        self._reserved += amount

    def settle(self, reserved: float, record: CallRecord, run_id: str) -> None:
        self._reserved = max(0.0, self._reserved - reserved)
        self._spent += record.cost_usd
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "run_id": run_id,
            "model": record.model,
            "role": record.role,
            "case_id": record.case_id,
            "cost_usd": round(record.cost_usd, 8),
            "cost_source": record.cost_source,
        }
        with self.path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")


@dataclass
class CallResult[T]:
    output: T | None
    record: CallRecord


class EvalClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        run_id: str,
        ledger_path: Path = LEDGER_PATH,
        transport: httpx2.AsyncBaseTransport | None = None,
        max_tokens: int = 1200,
    ) -> None:
        settings = settings or get_settings()
        if settings.max_eval_usd is None:
            raise BudgetExceededError("MAX_EVAL_USD is not set; refusing to call any model")
        api_key = settings.openrouter_api_key.get_secret_value()
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        self.run_id = run_id
        self._settings = settings
        self._zdr_violations: list[str] = []
        self._llms: dict[str, PydanticAILLM] = {}
        self.base_url = settings.openrouter_base_url.rstrip("/")
        self.ledger = CostLedger(ledger_path, settings.max_eval_usd)
        self._max_tokens = max_tokens
        self._http = httpx2.AsyncClient(
            transport=transport,
            event_hooks={"request": [self._enforce_zdr_hook]},
            timeout=httpx2.Timeout(60.0, connect=10.0),
        )
        self._auth = {"Authorization": f"Bearer {api_key}"}
        openai_client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=api_key,
            http_client=self._http,  # pyright: ignore[reportArgumentType]
            max_retries=1,
        )
        self._provider = OpenRouterProvider(openai_client=openai_client)
        self._pricing: dict[str, tuple[float, float]] | None = None
        self._call_cost: dict[str, float] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    def model(self, spec: ModelSpec, *, max_tokens: int | None = None) -> OpenRouterModel:
        settings: OpenRouterModelSettings = {
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": 0.0 if spec.effort in (None, "none") else 1.0,
            "openrouter_provider": dict(ZDR_PROVIDER),  # pyright: ignore[reportAssignmentType]
            "openrouter_usage": {"include": True},
        }
        if (reasoning := spec.reasoning()) is not None:
            settings["openrouter_reasoning"] = reasoning
        return OpenRouterModel(spec.model_id, provider=self._provider, settings=settings)

    async def structured[T](
        self,
        spec: ModelSpec,
        *,
        role: str,
        case_id: str,
        instructions: str,
        prompt: str,
        output_type: type[T],
        retries: int = 1,
    ) -> CallResult[T]:
        agent = Agent(
            self.model(spec),
            output_type=ToolOutput(output_type),
            instructions=instructions,
            retries={"output": retries},
        )
        reserved = self._reserve(spec)
        started = time.perf_counter()
        output: T | None = None
        error: str | None = None
        with capture_run_messages() as messages:
            try:
                result = await agent.run(prompt)
                output = result.output
            except (AgentRunError, APIError, ValueError) as exc:
                _reraise_zdr(exc)
                error = f"{type(exc).__name__}: {exc}"[:500]
        latency = time.perf_counter() - started
        record = await self._record(
            spec, role, case_id, list(messages), latency, None, output is not None, error
        )
        self.ledger.settle(reserved, record, self.run_id)
        return CallResult(output, record)

    async def text(
        self,
        spec: ModelSpec,
        *,
        role: str,
        case_id: str,
        instructions: str,
        prompt: str,
        stream: bool = True,
        max_tokens: int | None = None,
    ) -> CallResult[str]:
        """Plain text completion; streamed so time to first token is measured."""
        agent = Agent(self.model(spec, max_tokens=max_tokens), instructions=instructions)
        reserved = self._reserve(spec)
        started = time.perf_counter()
        ttft: float | None = None
        output: str | None = None
        error: str | None = None
        with capture_run_messages() as messages:
            try:
                if stream:
                    chunks: list[str] = []
                    async with agent.run_stream(prompt) as run:
                        async for delta in run.stream_text(delta=True):
                            if delta and ttft is None:
                                ttft = time.perf_counter() - started
                            chunks.append(delta)
                    output = "".join(chunks)
                else:
                    output = (await agent.run(prompt)).output
            except (AgentRunError, APIError, ValueError) as exc:
                _reraise_zdr(exc)
                error = f"{type(exc).__name__}: {exc}"[:500]
        latency = time.perf_counter() - started
        record = await self._record(
            spec, role, case_id, list(messages), latency, ttft, output is not None, error
        )
        self.ledger.settle(reserved, record, self.run_id)
        return CallResult(output, record)

    async def _enforce_zdr_hook(self, request: httpx2.Request) -> None:
        try:
            enforce_zdr(request)
        except ZdrViolationError as exc:
            # The production layer turns exceptions into a fallback; remember it so the run stops.
            self._zdr_violations.append(str(exc))
            raise

    def _check_zdr(self) -> None:
        if self._zdr_violations:
            raise ZdrViolationError(self._zdr_violations[0])

    def production_llm(self, spec: ModelSpec) -> PydanticAILLM:
        """The shipped LLM layer with every role on `spec`, sharing the ZDR-checked client."""
        if spec.label not in self._llms:
            settings = self._settings.model_copy(
                update={
                    "llm_extractor": spec.label,
                    "llm_responder": spec.label,
                    "llm_summarizer": spec.label,
                    "llm_extract_timeout_s": EVAL_TIMEOUT_S,
                    "llm_respond_timeout_s": EVAL_TIMEOUT_S,
                    "llm_summarize_timeout_s": EVAL_TIMEOUT_S,
                }
            )
            self._llms[spec.label] = PydanticAILLM(settings, http_client=self._http)
        return self._llms[spec.label]

    async def extract(
        self,
        spec: ModelSpec,
        *,
        case_id: str,
        history: Sequence[ChatTurn],
        state: ConversationState,
        message: str,
        today: date,
    ) -> CallResult[TurnExtraction]:
        """`PydanticAILLM.extract`; a fallback extraction counts as no output."""
        llm = self.production_llm(spec)
        reserved = self._reserve(spec)
        with capture_run_messages() as messages:
            result = await llm.extract(history, state, message, today=today)
        self._check_zdr()
        record = await self._record(
            spec,
            "extractor",
            case_id,
            list(messages),
            result.call.latency_ms / 1000,
            None,
            not result.call.fallback_used,
            result.call.error,
        )
        self.ledger.settle(reserved, record, self.run_id)
        output = None if result.call.fallback_used else result.extraction
        return CallResult(output, record)

    async def respond(
        self, spec: ModelSpec, *, case_id: str, action: Action, ctx: ReplyContext
    ) -> CallResult[str]:
        """`PydanticAILLM.respond`. A reply that fell back to the template counts as none."""
        llm = self.production_llm(spec)
        reserved = self._reserve(spec)
        result = await llm.respond(action, ctx)
        self._check_zdr()
        record = await self._record_call(spec, case_id, result.call)
        self.ledger.settle(reserved, record, self.run_id)
        output = None if result.call.fallback_used else result.reply.text
        return CallResult(output, record)

    async def stream_reply(
        self, spec: ModelSpec, *, case_id: str, action: Action, ctx: ReplyContext
    ) -> CallResult[str]:
        """The production responder agent and prompt, streamed to time the first token."""
        agent = self.production_llm(spec).responders[ctx.channel]
        prompt = respond_prompt(action, ctx, fallback_reply(action, ctx))
        reserved = self._reserve(spec)
        started = time.perf_counter()
        ttft: float | None = None
        output: str | None = None
        error: str | None = None
        with capture_run_messages() as messages:
            try:
                chunks: list[str] = []
                async with agent.run_stream(prompt) as run:
                    async for delta in run.stream_text(delta=True):
                        if delta and ttft is None:
                            ttft = time.perf_counter() - started
                        chunks.append(delta)
                output = clean_reply("".join(chunks), ctx.language) or None
            except (AgentRunError, APIError, ValueError) as exc:
                _reraise_zdr(exc)
                error = f"{type(exc).__name__}: {exc}"[:500]
        latency = time.perf_counter() - started
        record = await self._record(
            spec,
            "responder_stream",
            case_id,
            list(messages),
            latency,
            ttft,
            output is not None,
            error,
        )
        self.ledger.settle(reserved, record, self.run_id)
        return CallResult(output, record)

    async def _record_call(self, spec: ModelSpec, case_id: str, call: LLMCallRecord) -> CallRecord:
        """A ledger record from the production call record (which sums retried attempts)."""
        record = CallRecord(
            model=spec.label,
            role=call.role.value,
            case_id=case_id,
            ok=not call.fallback_used,
            latency_s=round(call.latency_ms / 1000, 3),
            input_tokens=call.input_tokens or 0,
            output_tokens=call.output_tokens or 0,
            provider=call.provider,
            requests=1,
            error=call.error,
        )
        if call.cost_usd is not None:
            record.cost_usd, record.cost_source = call.cost_usd, "usage"
        elif record.input_tokens or record.output_tokens:
            prompt_price, completion_price = (await self._prices()).get(spec.model_id, (0.0, 0.0))
            record.cost_usd = (
                record.input_tokens * prompt_price + record.output_tokens * completion_price
            )
            record.cost_source = "pricing"
        if record.cost_usd:
            previous = self._call_cost.get(spec.label, 0.0)
            self._call_cost[spec.label] = max(previous, record.cost_usd * 1.5)
        return record

    def _reserve(self, spec: ModelSpec) -> float:
        amount = self._call_cost.get(spec.label, DEFAULT_CALL_RESERVE_USD)
        self.ledger.reserve(amount)
        return amount

    async def _record(
        self,
        spec: ModelSpec,
        role: str,
        case_id: str,
        messages: list[ModelMessage],
        latency: float,
        ttft: float | None,
        ok: bool,
        error: str | None,
    ) -> CallRecord:
        responses = [m for m in messages if isinstance(m, ModelResponse)]
        retries = sum(
            isinstance(part, RetryPromptPart)
            for m in messages
            if isinstance(m, ModelRequest)
            for part in m.parts
        )
        record = CallRecord(
            model=spec.label,
            role=role,
            case_id=case_id,
            ok=ok,
            latency_s=round(latency, 3),
            ttft_s=round(ttft, 3) if ttft is not None else None,
            requests=len(responses),
            output_retries=retries,
            error=error,
        )
        sources: set[CostSource] = set()
        for response in responses:
            details = response.provider_details or {}
            record.input_tokens += response.usage.input_tokens
            record.output_tokens += response.usage.output_tokens
            record.provider = details.get("downstream_provider") or record.provider
            if response.provider_response_id:
                record.generation_ids.append(response.provider_response_id)
            cost, source = await self._response_cost(spec, response)
            record.cost_usd += cost
            sources.add(source)
        for source in ("pricing", "generation", "usage"):
            if source in sources:
                record.cost_source = source  # pyright: ignore[reportAttributeAccessIssue]
                break
        if record.cost_usd:
            previous = self._call_cost.get(spec.label, 0.0)
            self._call_cost[spec.label] = max(previous, record.cost_usd * 1.5)
        return record

    async def _response_cost(
        self, spec: ModelSpec, response: ModelResponse
    ) -> tuple[float, CostSource]:
        details = response.provider_details or {}
        if isinstance(details.get("cost"), int | float):
            return float(details["cost"]), "usage"
        if response.provider_response_id:
            cost = await self._generation_cost(response.provider_response_id)
            if cost is not None:
                return cost, "generation"
        prompt_price, completion_price = (await self._prices()).get(spec.model_id, (0.0, 0.0))
        cost = (
            response.usage.input_tokens * prompt_price
            + response.usage.output_tokens * completion_price
        )
        return cost, "pricing" if cost else "none"

    async def _generation_cost(self, generation_id: str) -> float | None:
        for attempt in range(3):
            response = await self._http.get(
                f"{self.base_url}/generation", params={"id": generation_id}, headers=self._auth
            )
            if response.status_code == 200:
                data = response.json().get("data") or {}
                if isinstance(data.get("total_cost"), int | float):
                    return float(data["total_cost"])
            await asyncio.sleep(1.0 + attempt)
        return None

    async def _prices(self) -> dict[str, tuple[float, float]]:
        if self._pricing is None:
            response = await self._http.get(f"{self.base_url}/models", headers=self._auth)
            response.raise_for_status()
            self._pricing = {
                m["id"]: (
                    float(m.get("pricing", {}).get("prompt") or 0),
                    float(m.get("pricing", {}).get("completion") or 0),
                )
                for m in response.json()["data"]
            }
        return self._pricing


def record_dict(record: CallRecord) -> dict[str, Any]:
    return asdict(record)
