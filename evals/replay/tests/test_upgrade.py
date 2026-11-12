"""The version 1 reader (`engine/upgrade.py`) on every state the engine reached: each census
combination has a phase, and the phase stands for the same flags but the leftovers it drops."""

import json
from collections import defaultdict
from functools import cache

import pytest

from evals.replay import census
from evals.replay.corpus import encode, load
from sazon.engine.state import ConversationState
from sazon.engine.upgrade import FLAGS, LEFTOVERS, downgrade, upgrade

type Combination = tuple[str, tuple[str, ...]]


@cache
def combinations() -> dict[Combination, list[census.State]]:
    """Every distinct state of the corpus, by census combination."""
    seen: set[str] = set()
    out: dict[Combination, list[census.State]] = defaultdict(list)
    for _, state in census.states(load()):
        canonical = json.dumps(state, sort_keys=True)
        if canonical not in seen:
            seen.add(canonical)
            out[census.key(state)].append(state)
    return out


def _id(combination: Combination) -> str:
    stage, flags = combination
    return ",".join((stage, *flags))


@pytest.mark.parametrize("combination", sorted(combinations()), ids=_id)
def test_every_census_combination_reads_into_a_phase_that_writes_it_back(
    combination: Combination,
) -> None:
    for state in combinations()[combination]:
        upgraded = upgrade(state)
        stored = {**upgraded, "phase": upgraded["phase"].model_dump(mode="json")}
        back = downgrade(stored)
        changed = {name for name in {*state, *back} if back.get(name) != state.get(name)}
        dropped = changed - LEFTOVERS
        if state["stage"] == "done" and state["booking"] is None:
            dropped -= {"awaiting", "cancel_offered"}
        if state["booking"] is None:
            dropped -= {"cancel_offered"}
        assert not dropped, {name: (state.get(name), back.get(name)) for name in dropped}
        assert set(back) == set(state)
        assert not set(stored) & (set(FLAGS) - {"phase"})


ANALYTICS_READS = (
    "stage",
    "outcome",
    "disqualify_reason",
    "drop_off_stage",
    "consented",
    "duplicate_of",
    "restarted",
    "talent_pool_opt_in",
    "reopenable",
    "paused",
)
"""What the analytics, the API's `StateOut` and the recruiter views read off a state."""


@pytest.mark.parametrize("combination", sorted(combinations()), ids=_id)
def test_a_state_read_into_its_phase_reads_the_same_for_analytics_and_the_api(
    combination: Combination,
) -> None:
    """`stage` and the rest are derived from the phase now: on every state the engine reached
    they are what version 1 stored, so the analytics count the same."""
    for state in combinations()[combination]:
        read = ConversationState.model_validate(state)
        derived = encode({name: getattr(read, name) for name in ANALYTICS_READS})
        assert derived == {name: state[name] for name in ANALYTICS_READS}
