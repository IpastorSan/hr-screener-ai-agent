"""Record the replay corpus: every engine call made while the journeys and the e2e personas run
offline, the calls no journey makes on a sample of their states (`derived`), the scripted
paths the offline extractor cannot reach (`synth`), then the state census.

    uv run --project backend python -m evals.replay.record      (or `make replay`)

Writes `evals/replay/corpus.jsonl.gz` and `docs/engine/state-census.md`. Regenerate only when
the engine's behaviour changes on purpose: the replay test exists to catch the changes that
were not.

Each journey and persona runs alone, in order, on the offline LLM, with the eval clock and
database ids counted from zero, so the same code records the same bytes.
"""

import asyncio
import contextvars
import functools
import os
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from evals.e2e.conversation import MeteredLLM, run_conversation
from evals.e2e.personas import load_personas
from evals.e2e.simulator import ScriptedSimulator
from evals.journeys.model import DEFAULT_AGENT, load_journeys
from evals.journeys.runner import JourneyRun
from evals.replay import census
from evals.replay.corpus import API, CORPUS_PATH, Case, call, dedupe, encode, encode_args, save
from evals.replay.synth import synthesize
from sazon import engine
from sazon.config import Settings, get_settings
from sazon.engine.state import ConversationState
from sazon.llm.offline import OfflineLLM
from sazon.rag.index import NoRetriever
from sazon.storage import models as storage_models

_case: contextvars.ContextVar[str] = contextvars.ContextVar("replay_case")


def _recording(name: str, fn: Callable[..., Any], cases: list[Case]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        encoded = encode_args(name, args, kwargs)
        result = fn(*args, **kwargs)
        cases.append(Case(case=_case.get(), fn=name, args=encoded, out=encode(result)))
        return result

    return wrapper


@contextmanager
def recording(cases: list[Case]) -> Iterator[None]:
    """Wrap the engine's public functions wherever a module outside the engine imported them;
    calls inside the engine are left alone."""
    originals = {id(getattr(engine, name)): name for name in API}
    patched: list[tuple[object, str, object]] = []
    for module_name, module in list(sys.modules.items()):
        inside = module_name == "sazon.engine" or module_name.startswith(
            ("sazon.engine.", "evals.replay")
        )
        if inside or not module_name.startswith(("sazon", "evals")):
            continue
        for attr, value in list(vars(module).items()):
            name = originals.get(id(value))
            if name is not None:
                patched.append((module, attr, value))
                setattr(module, attr, _recording(name, value, cases))
    try:
        yield
    finally:
        for module, attr, value in patched:
            setattr(module, attr, value)


@contextmanager
def counted_ids() -> Iterator[Callable[[], None]]:
    """Database ids counted from zero instead of random, reset per run."""
    counter = 0

    def next_id() -> uuid.UUID:
        nonlocal counter
        counter += 1
        return uuid.UUID(int=counter)

    def reset() -> None:
        nonlocal counter
        counter = 0

    original = storage_models.uuid4
    storage_models.uuid4 = next_id  # pyright: ignore[reportAttributeAccessIssue]
    try:
        yield reset
    finally:
        storage_models.uuid4 = original


def isolate_settings() -> None:
    """As the eval tests do (`evals/conftest.py`): no `.env` and no settings from the shell."""
    Settings.model_config["env_file"] = None
    fields = set(Settings.model_fields)
    for name in list(os.environ):
        if name.lower() in fields:
            del os.environ[name]
    os.environ["OPENROUTER_API_KEY"] = "test-key-not-valid"
    get_settings.cache_clear()


async def _play(reset_ids: Callable[[], None]) -> list[str]:
    """Every rider journey, then every persona; the names of those that did not pass. The
    corpus is the riders': another agent's calls replay only against its own spec."""
    problems: list[str] = []
    for journey in load_journeys(agent=DEFAULT_AGENT):
        name = f"{journey.group}.{journey.id}"
        reset_ids()
        _case.set(name)
        run = JourneyRun(
            journey, MeteredLLM(OfflineLLM()), ScriptedSimulator(), NoRetriever(), live=False
        )
        result = await run.run()
        if result["status"] == "fail":
            problems.append(name)
    for persona in load_personas():
        name = f"e2e.{persona.id}"
        reset_ids()
        _case.set(name)
        llm = MeteredLLM(OfflineLLM())
        await run_conversation(persona, llm, ScriptedSimulator(), NoRetriever())
    return problems


DERIVED_PER_KIND = 2
"""States per census combination (and pending field and channel) that get the derived calls."""


def _derived_call(case: str, name: str, *args: Any) -> Case:
    result = getattr(engine, name)(*args)
    return Case(case=case, fn=name, args=encode_args(name, args, {}), out=encode(result))


def derived(cases: list[Case]) -> list[Case]:
    """The calls no journey makes, because they need a nudge, an idle timeout or a calendar
    change (`current_prompt`, `re_engage`, `abandon`, `reopen`, `restart`, `reoffer`,
    `awaits_reoffer`), made on a sample of the recorded states: a few of every kind."""
    taken: Counter[tuple[Any, ...]] = Counter()
    out: list[Case] = []
    for journey, data in census.states(cases):
        kind = (*census.key(data), data["awaiting"], data["voice"])
        if taken[kind] >= DERIVED_PER_KIND:
            continue
        taken[kind] += 1
        state = ConversationState.model_validate(data)
        name = f"{journey}+derived"
        out += [
            _derived_call(name, "current_prompt", state),
            _derived_call(name, "re_engage", state, 1),
            _derived_call(name, "restart", state),
            _derived_call(name, "awaits_reoffer", state),
        ]
        if state.outcome is None and state.duplicate_of is None:
            # Only what the idle sweep abandons (`reengagement.idle.is_stale`).
            abandoned = engine.abandon(state)
            out.append(_derived_call(name, "abandon", state))
            out.append(_derived_call(name, "reopen", abandoned))
        if state.booking is not None:
            # A recruiter cancelled the booked interview (`scheduling.push`).
            out.append(_derived_call(name, "reoffer", state))
    return out


def main() -> int:
    isolate_settings()
    cases: list[Case] = []
    with counted_ids() as reset_ids, recording(cases):
        failed = asyncio.run(_play(reset_ids))
    if failed:
        print(f"journeys failing offline: {', '.join(failed)}", file=sys.stderr)
    unique = dedupe([*cases, *derived(cases), *synthesize(cases)])
    # Each case must replay from its JSON alone: the engine is pure and the encoding lossless.
    broken = [case for case in unique if call(case) != case["out"]]
    if broken:
        first = broken[0]
        print(f"{len(broken)} cases do not replay; first: {first['case']} {first['fn']}")
        return 1
    save(unique)
    census.write(unique)
    size = CORPUS_PATH.stat().st_size
    print(f"recorded {len(cases)} calls; {len(unique)} distinct cases, {size / 1024:.0f} KiB")
    print(f"wrote {CORPUS_PATH} and {census.CENSUS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
