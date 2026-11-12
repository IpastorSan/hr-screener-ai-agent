"""Scripted candidate journeys against the real screening service.

    uv run --project backend python -m evals.journeys [--agent a] [--group g] [--id j1,j2]
        [--offline] [--repeat N] [--combo A] [--max-usd 2] [--concurrency 4] [--keep-db]
        [--dry-run]

Writes `evals/results/journeys/<timestamp>/`: `summary.md` (pass or fail per journey, the
first failed expectation, cost) and one JSON per journey run with the full transcript. Exits 1
when a journey fails (an `xfail` journey's failure does not count) or the run was stopped.

`--offline` needs no model: keyword extractor, template replies, scripted candidate for
`persona` steps, no retrieval; `live:` expectations are skipped. A live run needs
`MAX_EVAL_USD` (the cumulative ceiling over the eval ledger) and stops at `--max-usd` for this
run. Every journey runs on its own in-memory database with a fake calendar, so nothing is left
behind but the report; `--keep-db` writes each journey's SQLite file into the run folder.

Each agent's journeys run screening for it (`cases/` the riders', `cases/<agent>/` another's),
one agent after the other, with its own places and team; `--agent` runs only one.
"""

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.e2e.conversation import FatalRunError, MeteredLLM
from evals.e2e.run import (
    COMBOS,
    estimate,
    key_remaining,
    load_faq,
    product_llm,
    simulator_spec,
)
from evals.e2e.simulator import LLMSimulator, ScriptedSimulator, Simulator
from evals.journeys.model import DEFAULT_AGENT, Journey, load_journeys
from evals.journeys.runner import JourneyRun
from evals.model_selection.client import RESULTS_DIR, BudgetExceededError, EvalClient, ModelSpec
from sazon.agents import agent_dir
from sazon.agents.loader import load_agent
from sazon.engine.spec import use
from sazon.llm import LLM
from sazon.llm.offline import OfflineLLM
from sazon.rag.index import NoRetriever, Retriever

JOURNEYS_DIR = RESULTS_DIR / "journeys"
STATUS_MARK = {"pass": "PASS", "fail": "FAIL", "xfail": "xfail", "xpass": "XPASS"}


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m evals.journeys", description=__doc__)
    parser.add_argument("--agent", default=None, help="one agent's journeys (cases/<agent>/)")
    parser.add_argument("--group", default=None, help="one cases/<group>.yaml")
    parser.add_argument("--id", default=None, help="comma-separated journey ids")
    parser.add_argument("--offline", action="store_true", help="no models, no cost")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--combo", default="A", help=f"product models: {', '.join(COMBOS)}")
    parser.add_argument("--max-usd", type=float, default=2.0, help="spend ceiling for this run")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--keep-db", action="store_true", help="keep each journey's SQLite file")
    parser.add_argument("--dry-run", action="store_true", help="list the journeys and the cost")
    parser.add_argument("--out", type=Path, default=JOURNEYS_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--no-faq-index", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


class Runner:
    def __init__(
        self, args: argparse.Namespace, client: EvalClient | None, faq: Retriever, run_dir: Path
    ):
        self.args = args
        self.client = client
        self.faq = faq
        self.run_dir = run_dir
        self.start_spent = client.ledger.spent if client else 0.0
        self.aborted: str | None = None
        self.inner: LLM = OfflineLLM()

    def guard(self) -> None:
        if self.client is None:
            return
        spent = self.client.ledger.spent - self.start_spent
        if spent > self.args.max_usd:
            raise BudgetExceededError(f"--max-usd {self.args.max_usd:.2f} reached ({spent:.4f})")

    async def one(self, journey: Journey, repeat: int) -> dict[str, Any] | None:
        if self.aborted:
            return None
        name = run_name(journey, repeat, self.args.repeat)
        simulator: Simulator
        if self.client is None:
            llm = MeteredLLM(self.inner)
            simulator = ScriptedSimulator()
        else:
            roles = COMBOS[self.args.combo]
            specs = {role: ModelSpec.parse(model) for role, model in roles.items()}
            llm = MeteredLLM(self.inner, self.client, specs, f"journey:{name}", self.guard)
            simulator = LLMSimulator(self.client, simulator_spec(), case_prefix="journey:")
        db_url = f"sqlite:///{self.run_dir / f'{name}.db'}" if self.args.keep_db else "sqlite://"
        try:
            self.guard()
            run = JourneyRun(
                journey, llm, simulator, self.faq, live=self.client is not None, db_url=db_url
            )
            result = await run.run()
        except (BudgetExceededError, FatalRunError) as exc:
            self.aborted = str(exc)
            print(f"stopped: {exc}", file=sys.stderr)
            return None
        result["repeat"] = repeat
        result["name"] = name
        path = self.run_dir / f"{name}.json"
        path.write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str) + "\n")
        first = result["first_failure"]
        print(
            f"  {STATUS_MARK[result['status']]:<5} {name:<32} turns={result['candidate_turns']:<3}"
            f" ${result['cost_usd'] + result['simulator_cost_usd']:.4f}"
            + (f"  {describe_failure(first)}" if first else ""),
            flush=True,
        )
        return result

    async def all(self, journeys: Sequence[Journey]) -> list[dict[str, Any]]:
        """Each agent's journeys in turn, screening for it."""
        by_agent: dict[str, list[Journey]] = {}
        for journey in journeys:
            by_agent.setdefault(journey.agent, []).append(journey)
        results: list[dict[str, Any]] = []
        for name, group in by_agent.items():
            with use(load_agent(agent_dir(name))):
                if self.client is not None:  # its prompts are the agent's
                    self.inner = product_llm(self.args.combo, self.client)
                results += await self._run_group(group)
        return results

    async def _run_group(self, journeys: Sequence[Journey]) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(self.args.concurrency)

        async def job(journey: Journey, repeat: int) -> dict[str, Any] | None:
            async with semaphore:
                return await self.one(journey, repeat)

        jobs = [job(j, r) for r in range(1, self.args.repeat + 1) for j in journeys]
        return [result for result in await asyncio.gather(*jobs) if result is not None]


def run_name(journey: Journey, repeat: int, repeats: int) -> str:
    name = f"{journey.group}.{journey.id}"
    if journey.agent != DEFAULT_AGENT:
        name = f"{journey.agent}.{name}"
    return f"{name}-r{repeat}" if repeats > 1 else name


def describe_failure(failure: dict[str, Any] | None) -> str:
    if not failure:
        return ""
    where = f"s{failure.get('session', '?')}/step {failure.get('step', '?')}"
    if failure.get("check") == "error":
        return f"{where}: error: {failure.get('detail')}"
    reply = re.sub(r"\s+", " ", str(failure.get("reply")))[:160]
    return f"{where}: {failure['check']}: {failure.get('problem')}. Reply: {reply}"


def summary(results: Sequence[dict[str, Any]], meta: dict[str, Any]) -> str:
    counts = {status: sum(r["status"] == status for r in results) for status in STATUS_MARK}
    cost = sum(r["cost_usd"] + r["simulator_cost_usd"] for r in results)
    lines = [
        f"# Journeys {meta['started']}",
        "",
        f"Mode: {meta['mode']}. Journeys: {len(results)}. "
        + ", ".join(f"{STATUS_MARK[s]} {n}" for s, n in counts.items() if n)
        + f". Cost: ${cost:.4f}."
        + (f" Stopped: {meta['aborted']}." if meta.get("aborted") else ""),
        "",
        "| Journey | Status | Turns | Cost | First failed expectation |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        failure = describe_failure(r["first_failure"]).replace("|", "\\|")
        xfail = f" ({r['xfail']})" if r.get("xfail") else ""
        lines.append(
            f"| [{r['name']}]({r['name']}.json) | {STATUS_MARK[r['status']]}{xfail} "
            f"| {r['candidate_turns']} | ${r['cost_usd'] + r['simulator_cost_usd']:.4f} "
            f"| {failure or '-'} |"
        )
    skipped = sum(r["expectations_skipped_offline"] for r in results)
    if skipped:
        lines += ["", f"{skipped} `live:` expectations skipped offline."]
    if skipped and counts["xpass"]:
        lines += [
            "",
            "An offline XPASS may only have skipped what fails: run the journey with real models "
            "before removing its `xfail`.",
        ]
    return "\n".join(lines) + "\n"


async def _main(args: argparse.Namespace) -> int:
    if not args.offline and args.combo not in COMBOS:
        print(f"unknown combo {args.combo}; use {', '.join(COMBOS)}", file=sys.stderr)
        return 2
    journeys = load_journeys(args.group, args.id, agent=args.agent)
    sessions = sum(len(j.sessions) for j in journeys) * args.repeat
    mode = "offline" if args.offline else f"combo {args.combo}"
    print(f"journeys: {len(journeys)} x {args.repeat} ({sessions} sessions), {mode}")
    if args.dry_run:
        for journey in journeys:
            steps = sum(len(s.steps) for s in journey.sessions)
            print(
                f"  {journey.group}.{journey.id}: {len(journey.sessions)} session(s), {steps} steps"
            )
        if not args.offline:
            from evals.e2e.run import _prices  # pyright: ignore[reportPrivateUsage]

            projected = estimate(_prices(), [args.combo], sessions, judge=False)[args.combo]
            print(f"  projected ~${projected:.2f} (run ceiling --max-usd {args.max_usd:.2f})")
        return 0

    faq: Retriever = NoRetriever()
    if not (args.offline or args.no_faq_index):
        index = load_faq(offline=False)
        if index is None:
            return 2
        faq = index
    now = datetime.now(UTC)
    run_dir = args.out / f"{now:%Y-%m-%dT%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    client: EvalClient | None = None
    if not args.offline:
        client = EvalClient(run_id=f"journeys-{now:%Y%m%dT%H%M%S}")
        remaining = await key_remaining(client)
        if remaining is not None and remaining < args.max_usd:
            await client.aclose()
            print(f"refusing: the OpenRouter key has ${remaining:.2f} left", file=sys.stderr)
            return 1
    runner = Runner(args, client, faq, run_dir)
    try:
        results = await runner.all(journeys)
    finally:
        if client is not None:
            await client.aclose()
    meta: dict[str, Any] = {
        "started": f"{now:%Y-%m-%d %H:%M:%S} UTC",
        "mode": mode,
        "models": None if args.offline else {r.value: m for r, m in COMBOS[args.combo].items()},
        "simulator": None if args.offline else simulator_spec().label,
        "aborted": runner.aborted,
    }
    if client is not None:
        meta["run_cost_usd"] = round(client.ledger.spent - runner.start_spent, 6)
        meta["ledger_total_usd"] = round(client.ledger.spent, 6)
    (run_dir / "summary.md").write_text(summary(results, meta), encoding="utf-8")
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"wrote {run_dir}/summary.md")
    if client is not None:
        print(f"spent ${meta['run_cost_usd']:.4f}; ledger ${meta['ledger_total_usd']:.4f}")
    failed = [r for r in results if r["status"] == "fail"]
    return 1 if failed or runner.aborted else 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_main(_parse_args(argv)))
