"""End-to-end persona evals: simulated candidates against the real screening service.

    uv run --project backend python -m evals.e2e --combo A|B|A,B --personas all --repeat 1
        [--concurrency 4] [--max-usd 5] [--min-key-usd 5] [--no-judge] [--judge-combos A]
        [--dry-run] [--offline] [--tag T]
    uv run --project backend python -m evals.e2e --rescore evals/results/<e2e json>

`--dry-run` prints the projected cost and exits. `--offline` runs the whole pipeline with no
model at all (keyword extractor, template replies, scripted candidate, no judge): it proves the
harness and costs nothing. A live run needs `MAX_EVAL_USD`: the eval ledger's cumulative
ceiling applies, plus `--max-usd` for this run alone, and the run refuses to start when the
estimate exceeds what the OpenRouter key has left.

A plain asyncio runner, like the model benchmark: per-call telemetry and the spend ceiling live
in the eval client, and the checks are plain functions over the persona cards.
"""

import argparse
import asyncio
import json
import statistics
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2

from evals.e2e.checks import CHECKS, check_conversation
from evals.e2e.conversation import FatalRunError, MeteredLLM, run_conversation
from evals.e2e.judge import DEFAULT_JUDGE, DIMENSIONS, judge_conversation
from evals.e2e.personas import Persona, select_personas
from evals.e2e.simulator import (
    DEFAULT_SIMULATOR,
    LLMSimulator,
    ScriptedSimulator,
    Simulator,
)
from evals.model_selection.client import (
    RESULTS_DIR,
    BudgetExceededError,
    EvalClient,
    ModelSpec,
)
from evals.model_selection.scoring import percentile
from sazon.config import get_settings
from sazon.llm import LLM
from sazon.llm.interface import Role
from sazon.llm.offline import OfflineLLM
from sazon.llm.pydantic_ai_impl import OPENROUTER_PREFIX, PydanticAILLM
from sazon.rag.index import NoRetriever, RagIndex, Retriever, default_index

COMBOS: dict[str, dict[Role, str]] = {
    # SAZ-24 finalists (evals/results/model_selection.md).
    "A": {
        Role.EXTRACTOR: "google/gemini-3.5-flash-lite@minimal",
        Role.RESPONDER: "anthropic/claude-haiku-5.5",
        Role.SUMMARIZER: "anthropic/claude-haiku-5.5",
    },
    "B": {
        Role.EXTRACTOR: "anthropic/claude-sonnet-5.5@low",
        Role.RESPONDER: "anthropic/claude-sonnet-5.5@low",
        Role.SUMMARIZER: "anthropic/claude-sonnet-5.5@low",
    },
}
OFFLINE = "offline"
MATRIX_PATH = RESULTS_DIR / "e2e.md"

# Cost model per conversation, from prompt sizes (chars / 3.2) and the benchmark's outputs.
EST_CANDIDATE_TURNS = 14
EST_TOKENS = {  # role: (input, output) per call
    "extractor": (3600, 250),
    "responder": (2400, 80),
    "summarizer": (1700, 200),
    "simulator": (1800, 120),
    "judge": (2600, 300),
}
EST_REASONING = {"minimal": 150, "low": 600, "medium": 1500, "high": 3000}


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.e2e", description=__doc__)
    parser.add_argument("--combo", default="A", help="A, B or A,B (SAZ-24 finalists)")
    parser.add_argument("--personas", default="all", help="all, or comma-separated ids")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-usd", type=float, default=5.0, help="spend ceiling for this run")
    parser.add_argument(
        "--min-key-usd",
        type=float,
        default=5.0,
        help="stop cleanly when the OpenRouter key has less than this left",
    )
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument(
        "--judge-combos", default=None, help="judge only these combos (default: every combo)"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the cost estimate only")
    parser.add_argument("--offline", action="store_true", help="no models: proves the pipeline")
    parser.add_argument("--tag", default=None)
    parser.add_argument(
        "--rescore",
        type=Path,
        default=None,
        help="re-run the checks on a results file (no model calls) and rewrite it",
    )
    return parser.parse_args(argv)


def _spec(text: str) -> ModelSpec:
    return ModelSpec.parse(text.removeprefix(OPENROUTER_PREFIX))


def simulator_spec() -> ModelSpec:
    return _spec(get_settings().llm_simulator or DEFAULT_SIMULATOR)


def judge_spec() -> ModelSpec:
    return _spec(get_settings().llm_judge or DEFAULT_JUDGE)


# --- cost estimate ---


def _prices() -> dict[str, tuple[float, float]]:
    response = httpx2.get(f"{get_settings().openrouter_base_url.rstrip('/')}/models", timeout=30)
    response.raise_for_status()
    return {
        m["id"]: (float(m["pricing"]["prompt"] or 0), float(m["pricing"]["completion"] or 0))
        for m in response.json()["data"]
    }


def call_cost(
    prices: dict[str, tuple[float, float]], spec: ModelSpec, role: str, calls: float
) -> float:
    prompt_price, completion_price = prices.get(spec.model_id, (0.0, 0.0))
    tokens_in, tokens_out = EST_TOKENS[role]
    tokens_out += EST_REASONING.get(spec.effort or "", 0)
    return calls * (tokens_in * prompt_price + tokens_out * completion_price)


def estimate(
    prices: dict[str, tuple[float, float]], combos: Sequence[str], conversations: int, judge: bool
) -> dict[str, float]:
    """Projected USD per combo for `conversations` conversations each, simulator and judge in."""
    turns = EST_CANDIDATE_TURNS
    out: dict[str, float] = {}
    for combo in combos:
        roles = COMBOS[combo]
        per_conv = (
            call_cost(prices, _spec(roles[Role.EXTRACTOR]), "extractor", turns)
            + call_cost(prices, _spec(roles[Role.RESPONDER]), "responder", turns + 1)
            + call_cost(prices, _spec(roles[Role.SUMMARIZER]), "summarizer", 1)
            + call_cost(prices, simulator_spec(), "simulator", turns)
            + (call_cost(prices, judge_spec(), "judge", 1) if judge else 0.0)
        )
        out[combo] = per_conv * conversations
    return out


FAQ_UNAVAILABLE = (
    "FAQ retrieval is unavailable: no embedding model or cached vectors in {cache}, so every "
    "question would get the unknown answer. Build the cache once with "
    "`cd backend && uv run sazon rag-index`, or copy backend/.cache from the main checkout."
)


def load_faq(*, offline: bool) -> RagIndex | None:
    """The FAQ index, loaded now rather than on the first question. Without it every question
    takes the unknown path, which a run with real models would report as the models' fault: that
    run stops here (None). An offline run goes on after a loud warning."""
    index = default_index()
    if index.warm():
        return index
    message = FAQ_UNAVAILABLE.format(cache=get_settings().sazon_cache_dir)
    print(f"{'WARNING' if offline else 'refusing'}: {message}", file=sys.stderr)
    return index if offline else None


async def key_remaining(client: EvalClient) -> float | None:
    """What the OpenRouter key may still spend, or None when it has no limit."""
    response = await client._http.get(  # pyright: ignore[reportPrivateUsage]
        f"{client.base_url}/key",
        headers=client._auth,  # pyright: ignore[reportPrivateUsage]
    )
    response.raise_for_status()
    remaining = (response.json().get("data") or {}).get("limit_remaining")
    return float(remaining) if remaining is not None else None


# --- running ---


def product_llm(combo: str, client: EvalClient) -> PydanticAILLM:
    """The shipped LLM layer with the combo's models and production timeouts."""
    roles = COMBOS[combo]
    settings = get_settings().model_copy(
        update={
            "llm_extractor": OPENROUTER_PREFIX + roles[Role.EXTRACTOR],
            "llm_responder": OPENROUTER_PREFIX + roles[Role.RESPONDER],
            "llm_summarizer": OPENROUTER_PREFIX + roles[Role.SUMMARIZER],
        }
    )
    return PydanticAILLM(settings, http_client=client._http)  # pyright: ignore[reportPrivateUsage]


class Runner:
    def __init__(self, args: argparse.Namespace, client: EvalClient | None, faq: Retriever):
        self.args = args
        self.client = client
        self.faq = faq
        self.start_spent = client.ledger.spent if client else 0.0
        self.aborted: str | None = None

    def judges(self, combo: str) -> bool:
        if self.args.no_judge:
            return False
        if not self.args.judge_combos:
            return True
        return combo in [c.strip().upper() for c in self.args.judge_combos.split(",")]

    def guard(self) -> None:
        if self.client is None:
            return
        spent = self.client.ledger.spent - self.start_spent
        if spent > self.args.max_usd:
            raise BudgetExceededError(f"--max-usd {self.args.max_usd:.2f} reached ({spent:.4f})")

    async def one(
        self, combo: str, persona: Persona, repeat: int, inner: LLM
    ) -> dict[str, Any] | None:
        if self.aborted:
            return None
        case_id = f"e2e:{combo}:{persona.id}:{repeat}"
        simulator: Simulator
        if self.client is None:
            llm = MeteredLLM(inner)
            simulator = ScriptedSimulator()
        else:
            roles = COMBOS[combo]
            specs = {role: _spec(model) for role, model in roles.items()}
            llm = MeteredLLM(inner, self.client, specs, case_id, self.guard)
            simulator = LLMSimulator(self.client, simulator_spec(), case_prefix=f"{combo}:")
        try:
            self.guard()
            if self.client is not None:
                remaining = await key_remaining(self.client)
                if remaining is not None and remaining < self.args.min_key_usd:
                    raise BudgetExceededError(f"the OpenRouter key has ${remaining:.2f} left")
            run = await run_conversation(persona, llm, simulator, self.faq)
            run["simulator_cost_usd"] = round(simulator.cost_usd, 6)
            run["judge"] = None
            if self.client is not None and self.judges(combo):
                self.guard()
                run["judge"] = await judge_conversation(
                    self.client, judge_spec(), persona, run, case_id
                )
        except (BudgetExceededError, FatalRunError) as exc:
            self.aborted = str(exc)
            print(f"stopped: {exc}", file=sys.stderr)
            return None
        run.update({"combo": combo, "repeat": repeat, "title": persona.title})
        run["result"] = check_conversation(persona, run)
        mark = "PASS" if run["result"]["passed"] else "fail"
        failed = [k for k, v in run["result"]["checks"].items() if v is False]
        print(
            f"  {combo} {persona.id:<24} {mark} {run['outcome']:<12} "
            f"turns={run['candidate_turns']:<2} ${run['cost_usd']:.4f} {','.join(failed)}",
            flush=True,
        )
        return run

    async def combo(self, combo: str, personas: Sequence[Persona]) -> list[dict[str, Any]]:
        inner: LLM = OfflineLLM() if self.client is None else product_llm(combo, self.client)
        semaphore = asyncio.Semaphore(self.args.concurrency)

        async def job(persona: Persona, repeat: int) -> dict[str, Any] | None:
            async with semaphore:
                return await self.one(combo, persona, repeat, inner)

        jobs = [job(p, r) for r in range(1, self.args.repeat + 1) for p in personas]
        return [run for run in await asyncio.gather(*jobs) if run is not None]


# --- aggregation and reports ---


def _ratio(values: Sequence[bool]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def aggregate(runs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    results = [run["result"] for run in runs]
    checks = {
        name: _ratio([r["checks"][name] for r in results if r["checks"].get(name) is not None])
        for name in CHECKS
    }
    metrics = [r["metrics"] for r in results]
    scored = sum(m["fields_scored"] for m in metrics)
    lang_total = sum(m["language_total"] for m in metrics)
    latencies = [
        t["latency_s"] for run in runs for t in run["turns"] if t["speaker"] == "candidate"
    ]
    judged = [run["judge"] for run in runs if run.get("judge") and "mean" in run["judge"]]
    judge = (
        {dim: round(statistics.mean(j[dim] for j in judged), 2) for dim in (*DIMENSIONS, "mean")}
        if judged
        else None
    )
    n = len(runs)
    return {
        "conversations": n,
        "all_checks": _ratio([r["passed"] for r in results]),
        "checks": checks,
        "outcome_accuracy": checks["outcome"],
        "field_accuracy": round(sum(m["fields_matched"] for m in metrics) / scored, 4)
        if scored
        else None,
        "language_accuracy": round(sum(m["language_ok"] for m in metrics) / lang_total, 4)
        if lang_total
        else None,
        "fallbacks": sum(m["fallback_count"] for m in metrics),
        "judge": judge,
        "avg_turns": round(sum(run["candidate_turns"] for run in runs) / n, 2) if n else None,
        "avg_cost_usd": round(sum(run["cost_usd"] for run in runs) / n, 5) if n else None,
        "simulator_cost_usd": round(sum(run.get("simulator_cost_usd", 0) for run in runs), 5),
        "judge_cost_usd": round(
            sum((run.get("judge") or {}).get("cost_usd", 0) for run in runs), 5
        ),
        "latency_p50_s": percentile(latencies, 50),
        "latency_p95_s": percentile(latencies, 95),
    }


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


MATRIX_HEADER = (
    "| Date | Combo | Models (extractor / responder) | Convs | All checks | Outcome | Fields "
    "| Language | ≤300 chars | 1 question | Register | No emoji | No promises | AI disclosure "
    "| Real slots | No fallback | Flags | Judge tone/nat/emp/role/brev (mean) | Avg turns "
    "| $/conv | Turn p50/p95 s |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
)


def matrix_row(date: str, combo: str, agg: dict[str, Any]) -> str:
    roles = COMBOS.get(combo)
    models = f"{roles[Role.EXTRACTOR]} / {roles[Role.RESPONDER]}" if roles else combo
    c = agg["checks"]
    judge = agg["judge"]
    judge_text = (
        "/".join(f"{judge[d]:.1f}" for d in DIMENSIONS) + f" ({judge['mean']:.2f})"
        if judge
        else "-"
    )
    cells = [
        date,
        combo,
        models,
        str(agg["conversations"]),
        _pct(agg["all_checks"]),
        _pct(agg["outcome_accuracy"]),
        _pct(agg["field_accuracy"]),
        _pct(agg["language_accuracy"]),
        _pct(c["message_length"]),
        _pct(c["one_question"]),
        _pct(c["register"]),
        _pct(c["no_emoji"]),
        _pct(c["no_promises"]),
        _pct(c["ai_disclosure"]),
        _pct(c["real_slots"]),
        _pct(c["no_fallback"]),
        _pct(c["flags"]),
        judge_text,
        f"{agg['avg_turns']}",
        f"${agg['avg_cost_usd']:.4f}" if agg["avg_cost_usd"] is not None else "-",
        f"{agg['latency_p50_s']}/{agg['latency_p95_s']}",
    ]
    return "| " + " | ".join(cells) + " |\n"


def append_matrix(rows: Sequence[str], path: Path = MATRIX_PATH) -> None:
    if not path.exists():
        path.write_text(
            "# End-to-end persona evals\n\n"
            "Simulated candidates (`evals/e2e/personas.yaml`) against the real screening "
            "service. One row per combo and run; percentages are the share of conversations "
            "passing each check, except Fields (fields matched) and Language (Ana messages "
            "in the expected language). Cost is the product's model spend per conversation; "
            "the simulator and judge are extra. Latency is per candidate turn, without the "
            "closing summary.\n\n" + MATRIX_HEADER
        )
    with path.open("a") as fh:
        fh.writelines(rows)


def rescore(path: Path) -> list[str]:
    """Check stored conversations again against the current persona cards and checks."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    personas = {p.id: p for p in select_personas("all")}
    for run in payload["conversations"]:
        run["result"] = check_conversation(personas[run["persona"]], run)
    combos = list(dict.fromkeys(run["combo"] for run in payload["conversations"]))
    payload["aggregates"] = {
        combo: aggregate([r for r in payload["conversations"] if r["combo"] == combo])
        for combo in combos
    }
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False, default=str) + "\n")
    date = payload["meta"]["date"]
    return [matrix_row(date, combo, agg) for combo, agg in payload["aggregates"].items()]


async def _main(args: argparse.Namespace) -> int:
    if args.rescore is not None:
        print(MATRIX_HEADER + "".join(rescore(args.rescore)), end="")
        return 0
    combos = [OFFLINE] if args.offline else [c.strip().upper() for c in args.combo.split(",")]
    unknown = [c for c in combos if c != OFFLINE and c not in COMBOS]
    if unknown:
        print(f"unknown combo {unknown}; use {', '.join(COMBOS)}", file=sys.stderr)
        return 2
    personas = select_personas(args.personas)
    conversations = len(personas) * args.repeat
    print(f"e2e: {len(personas)} personas x {args.repeat} x {', '.join(combos)}")

    projected = 0.0
    if not args.offline:
        prices = _prices()
        judged = Runner(args, None, NoRetriever()).judges
        costs = {
            combo: estimate(prices, [combo], conversations, judged(combo))[combo]
            for combo in combos
        }
        for combo, cost in costs.items():
            print(f"  combo {combo}: {conversations} conversations ~${cost:.2f}")
        projected = sum(costs.values())
        print(f"  projected total ~${projected:.2f} (run ceiling --max-usd {args.max_usd:.2f})")
    if args.dry_run:
        return 0

    faq = load_faq(offline=args.offline)
    if faq is None:
        return 2
    now = datetime.now(UTC)
    client: EvalClient | None = None
    if not args.offline:
        client = EvalClient(run_id=f"e2e-{now:%Y%m%dT%H%M%S}")
        remaining = await key_remaining(client)
        if remaining is not None and projected > remaining:
            await client.aclose()
            print(f"refusing: the OpenRouter key has ${remaining:.2f} left", file=sys.stderr)
            return 1
    runner = Runner(args, client, faq)
    runs: list[dict[str, Any]] = []
    try:
        for combo in combos:
            if runner.aborted:
                break
            print(f"→ combo {combo}", flush=True)
            runs.extend(await runner.combo(combo, personas))
    finally:
        if client is not None:
            await client.aclose()

    by_combo = {combo: [r for r in runs if r["combo"] == combo] for combo in combos}
    aggregates = {combo: aggregate(rs) for combo, rs in by_combo.items() if rs}
    meta: dict[str, Any] = {
        "date": f"{now:%Y-%m-%d}",
        "combos": {c: {r.value: m for r, m in COMBOS[c].items()} for c in combos if c in COMBOS},
        "simulator": None if args.offline else simulator_spec().label,
        "judge": None if args.offline or args.no_judge else judge_spec().label,
        "judge_combos": args.judge_combos,
        "repeat": args.repeat,
        "personas": [p.id for p in personas],
        "aborted": runner.aborted,
    }
    if client is not None:
        meta["run_cost_usd"] = round(client.ledger.spent - runner.start_spent, 6)
        meta["ledger_total_usd"] = round(client.ledger.spent, 6)
    tag = args.tag or (OFFLINE if args.offline else None)
    stem = f"e2e_{now:%Y-%m-%d}" + (f"_{tag}" if tag else "")
    path = RESULTS_DIR / f"{stem}.json"
    payload = {"meta": meta, "aggregates": aggregates, "conversations": runs}
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False, default=str) + "\n")
    print(f"wrote {path}")

    rows = [matrix_row(meta["date"], combo, agg) for combo, agg in aggregates.items()]
    print(MATRIX_HEADER + "".join(rows), end="")
    if not args.offline and rows:
        append_matrix(rows)
        print(f"appended {len(rows)} row(s) to {MATRIX_PATH}")
    if client is not None:
        print(f"spent ${meta['run_cost_usd']:.4f}; ledger ${meta['ledger_total_usd']:.4f}")
    return 1 if runner.aborted else 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_main(_parse_args(argv)))
