"""The generated statechart (`docs/engine/states.mmd`): up to date with the declared edges,
and edges Mermaid can draw. Whether the corpus takes them is
`evals/replay/tests/test_transitions.py`."""

from pathlib import Path

import pytest

from sazon.engine.diagram import declared, render
from sazon.engine.interrupts import INTERRUPTS
from sazon.engine.machine import EDGES
from sazon.engine.state import Node
from sazon.engine.states import HANDLERS

DIAGRAM = Path(__file__).parents[3] / "docs" / "engine" / "states.mmd"


def test_the_diagram_matches_the_code() -> None:
    assert DIAGRAM.read_text(encoding="utf-8") == render(), (
        "docs/engine/states.mmd is out of date: run `make diagram`"
    )


def test_no_two_takers_share_a_name() -> None:
    """A handler's phase, an interrupt and an entry point never overwrite each other's edges."""
    names = [str(kind) for kind in HANDLERS] + [each.name for each in INTERRUPTS] + [*EDGES]
    assert len(set(names)) == len(names)


@pytest.mark.parametrize("by", sorted(declared()))
def test_edges_join_two_states(by: str) -> None:
    """Never a state and one of its own sub-states, nor twice the same ends for one taker, and
    no colon in the event: Mermaid reads the label after the first one."""
    edges = declared()[by]
    ends = [(edge.source, edge.target) for edge in edges]
    assert len(set(ends)) == len(ends)
    for edge in edges:
        assert not edge.source.within(edge.target)
        assert not edge.target.within(edge.source)
        assert edge.event
        assert ":" not in edge.event
        assert edge.target is not Node.START


def test_every_state_has_an_edge_in() -> None:
    targets = {edge.target for edges in declared().values() for edge in edges}
    leaves = [node for node in Node if not any(n.startswith(f"{node}.") for n in Node)]
    unreached = [n for n in leaves if n is not Node.START and not any(n.within(t) for t in targets)]
    assert not unreached
