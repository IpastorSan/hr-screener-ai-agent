"""Differential replay: every recorded engine call gives the same (state, action) today.

The corpus (`evals/replay/corpus.jsonl.gz`) is what the engine did when it was recorded. A
change that fails here changed behaviour: fix it, or, when the change is intended, regenerate
the corpus with `make replay` and review the census diff. States recorded in the version 1
shape are compared as the version 1 reader reads them, given and returned alike.
"""

from typing import Any

from evals.replay import census
from evals.replay.corpus import Case, call, expected, load

MAX_REPORTED = 5
MAX_PATHS = 12
MAX_VALUE = 120


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= MAX_VALUE else text[:MAX_VALUE] + "..."


def differences(want: Any, got: Any, path: str = "") -> list[str]:
    """The JSON paths where two values differ, with both values."""
    if isinstance(want, dict) and isinstance(got, dict):
        out: list[str] = []
        for key in sorted({*want, *got}):
            child = f"{path}.{key}" if path else key
            if key not in got:
                out.append(f"{child}: missing (recorded {_short(want[key])})")
            elif key not in want:
                out.append(f"{child}: unexpected {_short(got[key])}")
            else:
                out += differences(want[key], got[key], child)
        return out
    if isinstance(want, list) and isinstance(got, list) and len(want) == len(got):
        pairs = enumerate(zip(want, got, strict=True))
        return [d for i, (w, g) in pairs for d in differences(w, g, f"{path}[{i}]")]
    if want != got:
        return [f"{path or '<result>'}: recorded {_short(want)}, now {_short(got)}"]
    return []


def describe(case: Case, got: Any) -> str:
    want = expected(case)
    if isinstance(want, list) and isinstance(got, list) and len(want) == len(got) == 2:
        # A turn: name its halves.
        paths = differences(want[0], got[0], "state") + differences(want[1], got[1], "action")
    else:
        paths = differences(want, got)
    shown = "\n    ".join(paths[:MAX_PATHS])
    more = f"\n    ... {len(paths) - MAX_PATHS} more" if len(paths) > MAX_PATHS else ""
    return f"{case['case']}: {case['fn']}\n    {shown}{more}"


def test_every_recorded_call_replays_the_same() -> None:
    cases = list(load())
    assert cases, "empty corpus: run `make replay`"
    failures: list[str] = []
    count = 0
    for case in cases:
        got = call(case)
        if got != expected(case):
            count += 1
            if len(failures) < MAX_REPORTED:
                failures.append(describe(case, got))
    assert not count, (
        f"{count} of {len(cases)} recorded engine calls changed (`make replay` regenerates the"
        " corpus when the change is intended):\n" + "\n".join(failures)
    )


def test_the_census_matches_the_corpus() -> None:
    assert census.CENSUS_PATH.read_text(encoding="utf-8") == census.render(load()), (
        "docs/engine/state-census.md is out of date: run `make replay`"
    )
