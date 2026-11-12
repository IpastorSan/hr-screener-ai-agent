"""Model selection benchmark runner.

    uv run --project backend python -m evals.model_selection --stage extraction \
        --models anthropic/claude-haiku-5.5,z-ai/glm-5.3-flash@minimal --cases 5 [--dry-run]
    ... --stage reply --models ...
    ... --stage judge --models a,b,c [--anchor a] --replies evals/results/<reply run>.json
    ... --stage report --extraction <json> --replies <json> --judge <json>
    ... --stage report --judge <json>   (the scores against the anchor, with bootstrap intervals)
    ... --stage rescore --extraction <json> [--tag T]   (stored outputs against today's labels)

Extraction and replies run through the production `sazon.llm` layer (see `client.py`). A plain
asyncio runner rather than pydantic-evals: the per-call telemetry and the spend ceiling live in
the client, and the scorers are plain functions over the YAML gold labels.
"""

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Hashable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
from pydantic_ai import Agent, NativeOutput, PromptedOutput, ToolOutput

from evals.model_selection.cases import (
    EXTRACTION_CASES,
    REPLY_CASES,
    Case,
    case_country,
    extraction_history,
    extraction_state,
    load_cases,
    reply_action,
    reply_context,
    spread,
)
from evals.model_selection.client import (
    RESULTS_DIR,
    BudgetExceededError,
    CallResult,
    EvalClient,
    ModelSpec,
    record_dict,
)
from evals.model_selection.judge import (
    JUDGE_INSTRUCTIONS,
    JudgeVerdict,
    judge_prompt,
    pairings,
    position_bias,
    vs_anchor,
    win_rates,
)
from evals.model_selection.scoring import (
    ExtractionScore,
    ReplyCheck,
    aggregate_extraction,
    aggregate_replies,
    check_reply,
    consistency,
    constant_sentiment_accuracy,
    extraction_signature,
    percentile,
    score_extraction,
    sentiment_accuracy,
)
from sazon.engine import spec as agent_spec
from sazon.llm import prompting
from sazon.llm.extraction import output_model, parse_output
from sazon.llm.fallback_templates import fallback_reply
from sazon.llm.interface import Channel
from sazon.llm.pydantic_ai_impl import extract_prompt, join_prompts, respond_prompt

CONTRACT = "engine_contract"
SPANGLISH = "spanglish"
DEFAULT_JUDGE = "anthropic/claude-opus-5.5"
# Calls per screened candidate, for the cost per 1K candidates.
EXTRACT_CALLS_PER_CANDIDATE = 12
RESPOND_CALLS_PER_CANDIDATE = 12
# Dry-run token estimates: characters per token and output tokens per call.
CHARS_PER_TOKEN = 3.2
OUTPUT_TOKENS = {"extraction": 250, "reply": 80, "judge": 70}
REASONING_TOKENS = {"minimal": 150, "low": 600, "medium": 1500, "high": 3000}
# The judge's real cost per call over the character estimate (tool schema, longer reasons):
# Opus 5.5 cost $0.0102 a call on the 2026-10-19 smoke run against $0.0038 estimated.
JUDGE_ESTIMATE_FACTOR = 2.7
CASE_CHECKS = ("intent", "matrix_language", "explicit_language_request")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.model_selection")
    parser.add_argument(
        "--stage", choices=("extraction", "reply", "judge", "report", "rescore"), required=True
    )
    parser.add_argument("--models", default="", help="comma-separated vendor/model[@effort] ids")
    parser.add_argument("--cases", type=int, default=None, help="spread N cases over the file")
    parser.add_argument("--case-ids", default=None, help="comma-separated case ids")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=8, help="parallel calls per model")
    parser.add_argument("--tag", default=None, help="suffix for the results file names")
    parser.add_argument("--dry-run", action="store_true", help="estimate the cost, call nothing")
    parser.add_argument(
        "--extract-instructions",
        type=Path,
        default=None,
        help="extractor instructions to try instead of the production extract.md",
    )
    parser.add_argument(
        "--respond-instructions",
        type=Path,
        default=None,
        help="responder instructions to try instead of the production respond.md",
    )
    parser.add_argument(
        "--output-mode", choices=("tool", "native", "prompted"), help="extractor output mode"
    )
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE)
    parser.add_argument(
        "--anchor", default=None, help="judge only the pairs that include this model"
    )
    parser.add_argument("--extraction", type=Path, help="extraction results (report stage)")
    parser.add_argument("--replies", type=Path, help="reply results (judge and report stages)")
    parser.add_argument("--judge", type=Path, help="judge results (report stage)")
    return parser.parse_args(argv)


def _specs(args: argparse.Namespace) -> list[ModelSpec]:
    return [ModelSpec.parse(m) for m in args.models.split(",") if m.strip()]


def _select(args: argparse.Namespace) -> list[Case]:
    path = EXTRACTION_CASES if args.stage == "extraction" else REPLY_CASES
    cases = load_cases(path)
    if args.case_ids:
        wanted = set(args.case_ids.split(","))
        cases = [case for case in cases if case["id"] in wanted]
    return spread(cases, args.cases)


# --- running ---


EXTRACTION = output_model(agent_spec.agent())
SCHEMA_CHARS = len(json.dumps(EXTRACTION.model_json_schema()))
OUTPUT_MODES = {
    "tool": ToolOutput(EXTRACTION),
    "native": NativeOutput(EXTRACTION),
    "prompted": PromptedOutput(EXTRACTION),
}


def _override_extractor(
    client: EvalClient, spec: ModelSpec, instructions: Path | None, output_mode: str | None
) -> None:
    """Try other instructions or another output mode on the production extractor's model and
    settings. Production uses `extract.md` and prompted JSON output."""
    llm = client.production_llm(spec)
    extractor = llm.extractor
    text = (
        instructions.read_text(encoding="utf-8")
        if instructions
        else prompting.load_prompt("extract.md")
    )
    llm.extractor = Agent(
        extractor.model,
        output_type=OUTPUT_MODES[output_mode or "prompted"],
        instructions=prompting.fill(agent_spec.agent(), text),
        model_settings=extractor.model_settings,
    )


def _override_responder(client: EvalClient, spec: ModelSpec, instructions: Path) -> None:
    """Try other responder instructions on the production responder's model and settings."""
    llm = client.production_llm(spec)
    text = prompting.fill(agent_spec.agent(), instructions.read_text(encoding="utf-8"))
    voice = join_prompts(text, prompting.prompt("respond_voice.md"))
    for channel, agent in list(llm.responders.items()):
        llm.responders[channel] = Agent(
            agent.model,
            output_type=str,
            instructions=voice if channel == Channel.VOICE else text,
            model_settings=agent.model_settings,
        )


async def _extract_case(client: EvalClient, spec: ModelSpec, case: Case) -> dict[str, Any]:
    result = await client.extract(
        spec,
        case_id=case["id"],
        history=extraction_history(case),
        state=extraction_state(case),
        message=case["message"],
        today=case["today"],
    )
    parsed = result.output
    return {
        "case_id": case["id"],
        "output": parsed.model_dump(mode="json") if parsed else None,
        "record": record_dict(result.record),
        "_parsed": parsed,
    }


async def _reply_case(client: EvalClient, spec: ModelSpec, case: Case) -> dict[str, Any]:
    action = reply_action(case)
    ctx = reply_context(case, action)
    reply = await client.respond(spec, case_id=case["id"], action=action, ctx=ctx)
    stream = await client.stream_reply(spec, case_id=case["id"], action=action, ctx=ctx)
    return {
        "case_id": case["id"],
        "output": reply.output,
        "streamed": stream.output,
        "record": record_dict(reply.record),
        "stream_record": record_dict(stream.record),
        "_parsed": reply.output,
    }


async def _gather(concurrency: int, jobs: list[Any]) -> list[Any]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(job: Any) -> Any:
        async with semaphore:
            return await job

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(one(job)) for job in jobs]
    return [task.result() for task in tasks]


def _telemetry(records: list[dict[str, Any]], stream: list[dict[str, Any]] | None = None):
    latencies = [r["latency_s"] for r in records if r["ok"]]
    ttfts = [r["ttft_s"] for r in stream or () if r["ttft_s"] is not None]
    cost = sum(r["cost_usd"] for r in records)
    return {
        "calls": len(records),
        "errors": sum(not r["ok"] for r in records),
        "latency_p50": percentile(latencies, 50),
        "latency_p95": percentile(latencies, 95),
        "ttft_p50": percentile(ttfts, 50),
        "ttft_p95": percentile(ttfts, 95),
        "cost_usd": round(cost, 6),
        "cost_per_call": round(cost / len(records), 8) if records else None,
        "measurement_cost_usd": round(sum(r["cost_usd"] for r in stream or ()), 6),
        "input_tokens": sum(r["input_tokens"] for r in records),
        "output_tokens": sum(r["output_tokens"] for r in records),
        "providers": dict(Counter(r["provider"] for r in records if r["provider"])),
        "cost_sources": dict(Counter(r["cost_source"] for r in records)),
        "error_samples": sorted({r["error"] for r in records if r["error"]})[:5],
    }


# --- scoring ---


def _case_pass(score: ExtractionScore) -> bool:
    return score.fields_exact and all(score.checks.get(name, True) for name in CASE_CHECKS)


def _by_category(scored: list[tuple[str, ExtractionScore]]) -> dict[str, Any]:
    groups: dict[str, list[ExtractionScore]] = defaultdict(list)
    for category, score in scored:
        groups[category].append(score)
    return {
        category: {
            "cases": len(items),
            "pass": round(sum(_case_pass(s) for s in items) / len(items), 4),
            "fields_exact": round(sum(s.fields_exact for s in items) / len(items), 4),
            "intent": round(sum(s.checks.get("intent", False) for s in items) / len(items), 4),
        }
        for category, items in sorted(groups.items())
    }


def _score_extraction(cases: list[Case], runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {case["id"]: case for case in cases}
    scored: list[tuple[str, ExtractionScore]] = []
    signatures: dict[str, list[Hashable]] = defaultdict(list)
    for run in runs:
        case = by_id[run["case_id"]]
        country = case_country(case)
        score = score_extraction(
            case["gold"],
            run["_parsed"],
            country=country,
            output_retries=run["record"]["output_retries"],
        )
        run["score"] = {
            "pass": _case_pass(score),
            "fields_exact": score.fields_exact,
            "fp": dict(score.fp),
            "fn": dict(score.fn),
            "checks": score.checks,
        }
        scored.append((case["category"], score))
        signatures[run["case_id"]].append(extraction_signature(run["_parsed"], country=country))
    main = [score for category, score in scored if category != CONTRACT]
    report = aggregate_extraction(main)
    report["case_pass"] = round(sum(map(_case_pass, main)) / len(main), 4) if main else None
    contract = [score for category, score in scored if category == CONTRACT]
    report[CONTRACT] = aggregate_extraction(contract) if contract else None
    report["all_cases_schema_valid"] = round(
        sum(score.schema_valid for _, score in scored) / len(scored), 4
    )
    expected = [
        run
        for run in runs
        if run["_parsed"] is not None and by_id[run["case_id"]]["gold"].get("fields")
    ]
    report["empty_fields_when_expected"] = (
        round(sum(not run["_parsed"].fields for run in expected) / len(expected), 4)
        if expected
        else None
    )
    report["consistency"] = consistency(signatures)
    report["by_category"] = _by_category(scored)
    return report


def _score_replies(cases: list[Case], runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {case["id"]: case for case in cases}
    checks: list[ReplyCheck] = []
    for run in runs:
        result = check_reply(run["_parsed"], by_id[run["case_id"]].get("expect") or {})
        run["score"] = {"passed": result.passed, "checks": result.checks}
        checks.append(result)
    return aggregate_replies(checks)


# --- dry run ---


def _prices(base_url: str) -> dict[str, tuple[float, float]]:
    response = httpx2.get(f"{base_url.rstrip('/')}/models", timeout=30)
    response.raise_for_status()
    return {
        m["id"]: (float(m["pricing"]["prompt"] or 0), float(m["pricing"]["completion"] or 0))
        for m in response.json()["data"]
    }


def _input_chars(stage: str, case: Case) -> int:
    if stage == "extraction":
        prompt = extract_prompt(
            extraction_history(case), extraction_state(case), case["message"], case["today"]
        )
        rules = prompting.prompt("extract.md")
        return len(rules) + SCHEMA_CHARS + len(prompt)
    action = reply_action(case)
    ctx = reply_context(case, action)
    prompt = respond_prompt(action, ctx, fallback_reply(action, ctx))
    return len(prompting.prompt("respond.md")) + len(prompt)


def _estimate(args: argparse.Namespace, specs: list[ModelSpec], cases: list[Case]) -> float:
    from sazon.config import get_settings

    prices = _prices(get_settings().openrouter_base_url)
    stage = args.stage
    input_tokens = sum(_input_chars(stage, case) for case in cases) / CHARS_PER_TOKEN
    calls_per_case = 2 if stage == "reply" else 1
    total = 0.0
    for spec in specs:
        prompt_price, completion_price = prices.get(spec.model_id, (0.0, 0.0))
        output = OUTPUT_TOKENS[stage] + REASONING_TOKENS.get(spec.effort or "", 0)
        calls = len(cases) * args.repeat * calls_per_case
        cost = (
            input_tokens * args.repeat * calls_per_case * prompt_price
            + calls * output * completion_price
        )
        total += cost
        missing = "" if spec.model_id in prices else "  (no price: unknown model id)"
        print(f"  {spec.label:<42} {calls:>5} calls  ~${cost:.3f}{missing}")
    print(f"  projected total ~${total:.2f}")
    return total


# --- stages ---


async def _run_stage(args: argparse.Namespace) -> int:
    specs = _specs(args)
    cases = _select(args)
    if not specs or not cases:
        print("no models or no cases selected", file=sys.stderr)
        return 2
    print(f"{args.stage}: {len(cases)} cases x {len(specs)} models x {args.repeat}")
    if args.dry_run:
        _estimate(args, specs, cases)
        return 0

    now = datetime.now(UTC)
    client = EvalClient(run_id=f"{args.stage}-{now:%Y%m%dT%H%M%S}")
    start_spent = client.ledger.spent
    models: dict[str, Any] = {}
    calls_out: list[dict[str, Any]] = []
    aborted: str | None = None
    try:
        for spec in specs:
            print(f"→ {spec.label}", flush=True)
            if args.respond_instructions:
                _override_responder(client, spec, args.respond_instructions)
            if args.extract_instructions or args.output_mode:
                _override_extractor(client, spec, args.extract_instructions, args.output_mode)
            run_case = _extract_case if args.stage == "extraction" else _reply_case
            jobs = [run_case(client, spec, case) for _ in range(args.repeat) for case in cases]
            runs: list[dict[str, Any]] = []
            try:
                runs = await _gather(args.concurrency, jobs)
            except* BudgetExceededError as group:
                aborted = str(group.exceptions[0])
            if aborted:
                print(f"stopped: {aborted}", file=sys.stderr)
                break
            if args.stage == "extraction":
                quality = _score_extraction(cases, runs)
                telemetry = _telemetry([run["record"] for run in runs])
            else:
                quality = _score_replies(cases, runs)
                telemetry = _telemetry(
                    [run["record"] for run in runs], [run["stream_record"] for run in runs]
                )
            models[spec.label] = {
                "model": spec.model_id,
                "effort": spec.effort,
                "quality": quality,
                "telemetry": telemetry,
            }
            print(f"  {_summary_line(args.stage, quality, telemetry)}", flush=True)
            for run in runs:
                run.pop("_parsed")
                calls_out.append({"model": spec.label, **run})
    finally:
        await client.aclose()

    meta = _meta(args, client, start_spent, now, aborted) | {"cases": len(cases)}
    if args.extract_instructions:
        meta["extract_instructions"] = str(args.extract_instructions)
    if args.output_mode:
        meta["output_mode"] = args.output_mode
    if args.respond_instructions:
        meta["respond_instructions"] = str(args.respond_instructions)
    _write(f"{args.stage}", now, args.tag, {"meta": meta, "models": models, "calls": calls_out})
    return 1 if aborted else 0


def _summary_line(stage: str, quality: dict[str, Any], telemetry: dict[str, Any]) -> str:
    if stage == "extraction":
        return (
            f"valid {_pct(quality['all_cases_schema_valid'])} · F1 {_pct(quality['field_f1'])} · "
            f"pass {_pct(quality['case_pass'])} · p50 {_secs(telemetry['latency_p50'])}s · "
            f"${telemetry['cost_usd']:.4f}"
        )
    return (
        f"checks {_pct(quality['all_checks_pass'])} · TTFT p50 {_secs(telemetry['ttft_p50'])}s · "
        f"${telemetry['cost_usd'] + telemetry['measurement_cost_usd']:.4f}"
    )


async def _run_judge(args: argparse.Namespace) -> int:
    if not args.replies:
        print("--replies is required", file=sys.stderr)
        return 2
    replies = json.loads(args.replies.read_text())
    labels = [spec.label for spec in _specs(args)] or list(replies["models"])
    texts: dict[tuple[str, str], str | None] = {
        (call["model"], call["case_id"]): call["output"] for call in replies["calls"]
    }
    cases = [case for case in _select(args) if all((m, case["id"]) in texts for m in labels)]
    judge = ModelSpec.parse(args.judge_model)
    pairs = pairings(labels, args.anchor)
    jobs: list[tuple[Case, str, str]] = [(case, a, b) for case in cases for a, b in pairs]
    print(f"judge: {len(cases)} cases x {len(pairs)} ordered pairs = {len(jobs)}")
    if args.dry_run:
        from sazon.config import get_settings

        prices = _prices(get_settings().openrouter_base_url)
        prompt_price, completion_price = prices.get(judge.model_id, (0.0, 0.0))
        sample = "x" * 150
        chars = sum(
            len(JUDGE_INSTRUCTIONS) + len(judge_prompt(case, sample, sample)) for case, *_ in jobs
        )
        cost = chars / CHARS_PER_TOKEN * prompt_price + len(jobs) * 70 * completion_price
        cost *= JUDGE_ESTIMATE_FACTOR
        print(f"  projected ~${cost:.2f} (identical or missing replies skip the call)")
        return 0

    now = datetime.now(UTC)
    client = EvalClient(run_id=f"judge-{now:%Y%m%dT%H%M%S}")
    start_spent = client.ledger.spent

    async def one(case: Case, a: str, b: str) -> dict[str, Any]:
        first, second = texts[(a, case["id"])], texts[(b, case["id"])]
        row: dict[str, Any] = {"case_id": case["id"], "a": a, "b": b}
        if first is None or second is None or first.strip() == second.strip():
            winner = "tie" if first == second else ("B" if first is None else "A")
            return row | {"winner": winner, "reason": "no call: missing or identical reply"}
        try:
            result: CallResult[JudgeVerdict] = await client.structured(
                judge,
                role="judge",
                case_id=case["id"],
                instructions=JUDGE_INSTRUCTIONS,
                prompt=judge_prompt(case, first, second),
                output_type=JudgeVerdict,
            )
        except BudgetExceededError as exc:
            # Keep the verdicts already paid for; the skipped pairs are dropped below.
            budget_stops.append(str(exc))
            return row | {"winner": None, "reason": "skipped: budget", "skipped": True}
        verdict = result.output
        return row | {
            "winner": verdict.winner if verdict else None,
            "reason": verdict.reason if verdict else result.record.error,
            "cost_usd": result.record.cost_usd,
            "input_tokens": result.record.input_tokens,
            "output_tokens": result.record.output_tokens,
        }

    budget_stops: list[str] = []
    try:
        rows = await _gather(args.concurrency, [one(*job) for job in jobs])
    finally:
        await client.aclose()
    comparisons = [row for row in rows if not row.get("skipped")]
    aborted = budget_stops[0] if budget_stops else None
    if aborted:
        print(f"stopped: {aborted} ({len(rows) - len(comparisons)} pairs skipped)", file=sys.stderr)
    meta = _meta(args, client, start_spent, now, aborted) | {
        "cases": len(cases),
        "judge": judge.label,
        "anchor": args.anchor,
        "comparisons": len(comparisons),
        "replies": str(args.replies),
        "position_bias_a": position_bias(comparisons),
        "failed_verdicts": sum(c["winner"] is None for c in comparisons),
    }
    rates = win_rates(comparisons)
    for model, rate in sorted(rates.items(), key=lambda item: -item[1]["win_rate"]):
        print(f"  {model:<42} {_pct(rate['win_rate'])} of {rate['comparisons']}")
    payload = {"meta": meta, "win_rates": rates, "calls": comparisons}
    if args.anchor:
        payload["vs_anchor"] = vs_anchor(comparisons, args.anchor)
    _write("judge", now, args.tag, payload)
    return 1 if aborted else 0


def _meta(
    args: argparse.Namespace,
    client: EvalClient,
    start_spent: float,
    now: datetime,
    aborted: str | None,
) -> dict[str, Any]:
    return {
        "run_id": client.run_id,
        "date": f"{now:%Y-%m-%d}",
        "stage": args.stage,
        "repeat": args.repeat,
        "base_url": client.base_url,
        "provider_preferences": {"zdr": True, "data_collection": "deny"},
        "run_cost_usd": round(client.ledger.spent - start_spent, 6),
        "ledger_total_usd": round(client.ledger.spent, 6),
        "max_eval_usd": client.ledger.max_usd,
        "aborted": aborted,
    }


def _write(stage: str, now: datetime, tag: str | None, payload: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"model_selection_{now:%Y-%m-%d}_{stage}" + (f"_{tag}" if tag else "")
    path = RESULTS_DIR / f"{stem}.json"
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False, default=str) + "\n")
    print(f"wrote {path}")
    return path


# --- report ---


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _secs(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _money(value: float | None) -> str:
    return "-" if value is None else f"${value:.2f}"


def report_rows(
    extraction: dict[str, Any], replies: dict[str, Any] | None, judge: dict[str, Any] | None
) -> list[dict[str, Any]]:
    reply_models = (replies or {}).get("models", {})
    rates = (judge or {}).get("win_rates", {})
    golds = scored_golds(extraction)
    calls = extraction.get("calls") or []
    rows = []
    for label, m in extraction["models"].items():
        q, t = m["quality"], m["telemetry"]
        own = [call for call in calls if call["model"] == label]
        sentiment = sentiment_accuracy(own, golds) if own else q["checks"].get("sentiment")
        r = reply_models.get(label)
        contract = q.get(CONTRACT) or {}
        spanglish = q["by_category"].get(SPANGLISH, {})
        extract_cost = t["cost_per_call"]
        respond_cost = r["telemetry"]["cost_per_call"] if r else None
        per_candidate = (
            EXTRACT_CALLS_PER_CANDIDATE * extract_cost + RESPOND_CALLS_PER_CANDIDATE * respond_cost
            if extract_cost is not None and respond_cost is not None
            else None
        )
        rows.append(
            {
                "model": label,
                "schema_valid": q["all_cases_schema_valid"],
                "schema_valid_first_try": q["schema_valid_first_try"],
                "field_f1": q["field_f1"],
                "fields_exact": q["fields_exact"],
                "case_pass": q["case_pass"],
                "intent": q["checks"].get("intent"),
                "sentiment": sentiment,
                "matrix_language": q["checks"].get("matrix_language"),
                "explicit_language_request": q["checks"].get("explicit_language_request"),
                "start_date": q["checks"].get("start_date"),
                "engine_contract_pass": _contract_pass(extraction, label),
                "engine_contract_f1": contract.get("field_f1"),
                "spanglish_pass": spanglish.get("pass"),
                "latency_p50": t["latency_p50"],
                "latency_p95": t["latency_p95"],
                "reply_checks": r["quality"]["all_checks_pass"] if r else None,
                "reply_latency_p50": r["telemetry"]["latency_p50"] if r else None,
                "ttft_p50": r["telemetry"]["ttft_p50"] if r else None,
                "ttft_p95": r["telemetry"]["ttft_p95"] if r else None,
                "judge_win_rate": rates.get(label, {}).get("win_rate"),
                "extract_cost_per_call": extract_cost,
                "respond_cost_per_call": respond_cost,
                "usd_per_1k_candidates": round(per_candidate * 1000, 4)
                if per_candidate is not None
                else None,
                "providers": sorted(t["providers"]),
            }
        )
    return rows


def scored_golds(extraction: dict[str, Any]) -> dict[str, Any]:
    """Gold labels of the non-contract cases the run called, by case id: the cases its field and
    check rates are over."""
    cases = {case["id"]: case for case in load_cases(EXTRACTION_CASES)}
    ran = {call["case_id"] for call in extraction.get("calls") or []}
    return {
        case_id: cases[case_id]["gold"]
        for case_id in sorted(ran)
        if case_id in cases and cases[case_id]["category"] != CONTRACT
    }


def baseline_line(extraction: dict[str, Any]) -> str:
    golds = scored_golds(extraction)
    accuracy = constant_sentiment_accuracy(golds)
    hits = round((accuracy or 0) * len(golds))
    return (
        f"Constant `neutral` sentiment baseline: {accuracy or 0:.1%} ({hits} of {len(golds)} "
        "cases accept `neutral`)."
    )


def _contract_pass(extraction: dict[str, Any], label: str) -> float | None:
    by_category = extraction["models"][label]["quality"]["by_category"]
    return by_category.get(CONTRACT, {}).get("pass")


def report_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Model | Schema valid | Field F1 | Exact match | Intent | Sentiment | Matrix lang "
        "| Explicit lang | Engine contract | Spanglish | Extract p50 / p95 s | Reply checks "
        "| TTFT p50 s | Judge win rate | $ / 1K candidates |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['model']} | {_pct(row['schema_valid'])} | {_pct(row['field_f1'])} "
            f"| {_pct(row['fields_exact'])} | {_pct(row['intent'])} | {_pct(row['sentiment'])} "
            f"| {_pct(row['matrix_language'])} | {_pct(row['explicit_language_request'])} "
            f"| {_pct(row['engine_contract_pass'])} | {_pct(row['spanglish_pass'])} "
            f"| {_secs(row['latency_p50'])} / {_secs(row['latency_p95'])} "
            f"| {_pct(row['reply_checks'])} | {_secs(row['ttft_p50'])} "
            f"| {_pct(row['judge_win_rate'])} | {_money(row['usd_per_1k_candidates'])} |"
        )
    return "\n".join(lines) + "\n"


def judge_intervals(judge: dict[str, Any]) -> dict[str, Any] | None:
    """The scores against the run's anchor and their bootstrap intervals, recomputed from the
    stored verdicts; None for a round robin."""
    anchor = judge["meta"].get("anchor")
    return vs_anchor(judge["calls"], anchor) if anchor else None


def intervals_table(intervals: dict[str, Any]) -> str:
    lines = [
        f"| Model | Score vs {intervals['anchor']} | 95% CI | Wins / ties / losses |",
        "|---|---|---|---|",
    ]
    for model, row in intervals["models"].items():
        low, high = row["ci95"]
        lines.append(
            f"| {model} | {_pct(row['score'])} | {_pct(low)}-{_pct(high)} "
            f"| {row['wins']} / {row['ties']} / {row['losses']} |"
        )
    return "\n".join(lines) + "\n"


def _run_report(args: argparse.Namespace) -> int:
    judge = json.loads(args.judge.read_text()) if args.judge else None
    intervals = judge_intervals(judge) if judge else None
    if intervals:
        print(intervals_table(intervals))
    if not args.extraction:
        if judge:
            return 0
        print("--extraction or --judge is required", file=sys.stderr)
        return 2
    extraction = json.loads(args.extraction.read_text())
    replies = json.loads(args.replies.read_text()) if args.replies else None
    rows = report_rows(extraction, replies, judge)
    now = datetime.now(UTC)
    payload = {
        "meta": {
            "date": f"{now:%Y-%m-%d}",
            "sources": {
                "extraction": str(args.extraction),
                "replies": str(args.replies) if args.replies else None,
                "judge": str(args.judge) if args.judge else None,
            },
            "calls_per_candidate": {
                "extract": EXTRACT_CALLS_PER_CANDIDATE,
                "respond": RESPOND_CALLS_PER_CANDIDATE,
            },
            "judge": (judge or {}).get("meta"),
            "judge_vs_anchor": intervals,
            "sentiment_baseline": constant_sentiment_accuracy(scored_golds(extraction)),
        },
        "rows": rows,
    }
    path = RESULTS_DIR / f"model_selection_{now:%Y-%m-%d}.json"
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    print(report_table(rows))
    print(baseline_line(extraction))
    print(f"wrote {path}")
    return 0


# --- re-score ---


def rescore_extraction(
    extraction: dict[str, Any], cases: list[Case]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Score stored extractor outputs against the current gold labels, with no model call.

    Returns the re-scored payload (telemetry kept as measured) and, per model, the change in
    the headline rates and the cases whose pass changed. Calls for cases no longer in the
    dataset are dropped.
    """
    by_id = {case["id"]: case for case in cases}
    calls = [call for call in extraction.get("calls") or [] if call["case_id"] in by_id]
    models: dict[str, Any] = {}
    deltas: dict[str, Any] = {}
    out_calls: list[dict[str, Any]] = []
    for label, stored in extraction["models"].items():
        runs = []
        for call in calls:
            if call["model"] != label:
                continue
            output = call.get("output")
            parsed = parse_output(output) if output else None
            runs.append({**call, "_parsed": parsed, "_before": (call.get("score") or {})})
        ran = [by_id[case_id] for case_id in dict.fromkeys(run["case_id"] for run in runs)]
        quality = _score_extraction(ran, runs)
        before = stored["quality"]
        changed = sorted(
            f"{run['case_id']}: {'pass' if run['score']['pass'] else 'fail'}"
            for run in runs
            if run["_before"].get("pass") is not None
            and run["_before"].get("pass") != run["score"]["pass"]
        )
        deltas[label] = {
            name: {"before": before.get(name), "after": quality.get(name)}
            for name in ("field_f1", "fields_exact", "case_pass")
        } | {"changed_cases": changed}
        models[label] = {**stored, "quality": quality}
        for run in runs:
            run.pop("_parsed")
            run.pop("_before")
            out_calls.append(run)
    meta = {**extraction.get("meta", {}), "rescored_from_stored_outputs": True}
    return {"meta": meta, "models": models, "calls": out_calls}, deltas


def _run_rescore(args: argparse.Namespace) -> int:
    if not args.extraction:
        print("--extraction is required", file=sys.stderr)
        return 2
    extraction = json.loads(args.extraction.read_text())
    payload, deltas = rescore_extraction(extraction, load_cases(EXTRACTION_CASES))
    now = datetime.now(UTC)
    payload["meta"]["rescored_on"] = f"{now:%Y-%m-%d}"
    payload["meta"]["rescored_source"] = str(args.extraction)
    payload["deltas"] = deltas
    _write("extraction", now, args.tag or "rescored", payload)
    for label, delta in deltas.items():
        rates = ", ".join(
            f"{name} {_pct(values['before'])} → {_pct(values['after'])}"
            for name, values in delta.items()
            if name != "changed_cases"
        )
        print(f"{label}: {rates}")
        for case in delta["changed_cases"]:
            print(f"    {case}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.stage == "report":
        return _run_report(args)
    if args.stage == "rescore":
        return _run_rescore(args)
    if args.stage == "judge":
        return asyncio.run(_run_judge(args))
    return asyncio.run(_run_stage(args))


if __name__ == "__main__":
    raise SystemExit(main())
