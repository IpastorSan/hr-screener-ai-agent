import json
from pathlib import Path

import httpx2
import pytest
from pydantic import SecretStr

from evals.model_selection.cases import (
    EXTRACTION_CASES,
    extraction_history,
    extraction_state,
    load_cases,
)
from evals.model_selection.client import (
    BudgetExceededError,
    CostLedger,
    EvalClient,
    ModelSpec,
    ZdrViolationError,
    enforce_zdr,
)
from sazon.config import Settings


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openrouter_api_key": SecretStr("test-key"),
        "openrouter_base_url": "https://openrouter.example/api/v1",
        "max_eval_usd": 1.0,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _completion(text: str) -> dict[str, object]:
    return {
        "id": "gen-123",
        "object": "chat.completion",
        "created": 1_800_000_000,
        "model": "anthropic/claude-haiku-5.5",
        "provider": "Google",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": text},
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 3,
            "total_tokens": 15,
            "cost": 0.0001,
        },
    }


async def test_every_completion_request_pins_zdr_and_deny(tmp_path: Path) -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=_completion("Hola"))

    client = EvalClient(
        _settings(),
        run_id="test",
        ledger_path=tmp_path / "ledger.jsonl",
        transport=httpx2.MockTransport(handler),
    )
    for spec in ("anthropic/claude-haiku-5.5", "z-ai/glm-5.3-flash@none"):
        result = await client.text(
            ModelSpec.parse(spec),
            role="responder",
            case_id="c1",
            instructions="Reply.",
            prompt="Hi",
            stream=False,
        )
        assert result.output == "Hola"
        assert result.record.provider == "Google"
        assert result.record.cost_source == "usage"
    await client.aclose()

    assert len(seen) == 2
    for body in seen:
        provider = body["provider"]
        assert isinstance(provider, dict)
        assert provider["zdr"] is True
        assert provider["data_collection"] == "deny"
    assert seen[1]["reasoning"] == {"enabled": False}
    assert CostLedger(tmp_path / "ledger.jsonl", 1.0).spent == pytest.approx(0.0002)


@pytest.mark.parametrize(
    "provider",
    [None, {"zdr": False, "data_collection": "deny"}, {"zdr": True}, {"zdr": "true"}],
)
def test_request_without_zdr_is_refused(provider: dict[str, object] | None) -> None:
    body: dict[str, object] = {"model": "x/y", "messages": []}
    if provider is not None:
        body["provider"] = provider
    request = httpx2.Request(
        "POST", "https://openrouter.example/api/v1/chat/completions", json=body
    )
    with pytest.raises(ZdrViolationError):
        enforce_zdr(request)


async def test_client_never_sends_a_request_without_zdr(tmp_path: Path) -> None:
    sent: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(200, json=_completion("x"))

    client = EvalClient(
        _settings(),
        run_id="test",
        ledger_path=tmp_path / "ledger.jsonl",
        transport=httpx2.MockTransport(handler),
    )
    with pytest.raises(ZdrViolationError):
        await client._http.post(  # pyright: ignore[reportPrivateUsage]
            "https://openrouter.example/api/v1/chat/completions", json={"model": "x/y"}
        )
    await client.aclose()
    assert sent == []


def test_refuses_to_run_without_a_budget(tmp_path: Path) -> None:
    with pytest.raises(BudgetExceededError):
        EvalClient(_settings(max_eval_usd=None), run_id="t", ledger_path=tmp_path / "l.jsonl")


def test_ledger_ceiling_is_cumulative(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_text(json.dumps({"cost_usd": 0.95}) + "\n")
    ledger = CostLedger(path, 1.0)
    ledger.reserve(0.04)
    with pytest.raises(BudgetExceededError):
        ledger.reserve(0.02)


def test_model_spec_parsing() -> None:
    assert ModelSpec.parse("anthropic/claude-sonnet-5.5@low").effort == "low"
    assert ModelSpec.parse("z-ai/glm-5.3").effort is None
    with pytest.raises(ValueError, match="vendor/model"):
        ModelSpec.parse("haiku")
    with pytest.raises(ValueError, match="effort"):
        ModelSpec.parse("a/b@max")


async def test_extraction_runs_through_the_production_layer(tmp_path: Path) -> None:
    seen: list[dict[str, object]] = []
    gold = {
        "intent": "answer",
        "fields": {"full_name": {"value": "Lucía Pérez", "confidence": 0.9}},
        "language": "es",
        "matrix_language": "es",
        "english_ratio": 0.0,
        "code_switched": False,
        "sentiment": "neutral",
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=_completion(json.dumps(gold)))

    client = EvalClient(
        _settings(),
        run_id="test",
        ledger_path=tmp_path / "ledger.jsonl",
        transport=httpx2.MockTransport(handler),
    )
    case = load_cases(EXTRACTION_CASES)[0]
    result = await client.extract(
        ModelSpec.parse("google/gemini-3.5-flash-lite@minimal"),
        case_id=case["id"],
        history=extraction_history(case),
        state=extraction_state(case),
        message=case["message"],
        today=case["today"],
    )
    await client.aclose()
    assert result.output is not None
    assert result.output.fields["full_name"].value == "Lucía Pérez"
    assert result.record.cost_usd == pytest.approx(0.0001)
    body = seen[0]
    assert body["provider"] == {"zdr": True, "data_collection": "deny"}
    assert body["reasoning"] == {"effort": "minimal", "exclude": True}
    assert "tools" not in body
    assert CostLedger(tmp_path / "ledger.jsonl", 1.0).spent == pytest.approx(0.0001)
