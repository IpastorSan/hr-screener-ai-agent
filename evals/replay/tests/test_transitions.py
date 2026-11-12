"""Transition coverage: the replay corpus takes only the edges the engine declares, and takes
every one of them but those in `UNREACHED`.

The legs come from playing the corpus with a recorder on the dispatch table
(`evals/replay/transitions.py`); the edges are those of the diagram (`sazon.engine.diagram`).
An undeclared leg is a path the diagram does not show: declare it when it is intended, fix it
when it is not. An edge no case takes needs a case (`evals/replay/synth.py`) or a reason here.
"""

from collections import Counter
from functools import cache

from evals.replay.corpus import load
from evals.replay.transitions import Leg, observed
from sazon.engine.diagram import declared
from sazon.engine.state import Node
from sazon.engine.states.handler import Edge

UNREACHED: dict[tuple[str, Node, Node], str] = {}
"""Declared edges no recorded case takes, by who takes them and their ends, with why."""


@cache
def legs() -> Counter[Leg]:
    return observed(load())


def _taken(by: str, edge: Edge) -> bool:
    return any(leg.by == by and edge.covers(leg.source, leg.target) for leg in legs())


def test_every_transition_taken_is_declared() -> None:
    edges = declared()
    undeclared = [
        f"{leg.by}: {leg.source} -> {leg.target} ({count} times)"
        for leg, count in legs().items()
        if not any(edge.covers(leg.source, leg.target) for edge in edges.get(leg.by, ()))
    ]
    assert not undeclared, "transitions taken but not declared:\n" + "\n".join(undeclared)


def test_every_declared_edge_is_taken_or_unreached() -> None:
    missing = [
        f"{by}: {edge.source} -> {edge.target} ({edge.event})"
        for by, edges in declared().items()
        for edge in edges
        if (by, edge.source, edge.target) not in UNREACHED and not _taken(by, edge)
    ]
    assert not missing, "declared edges no case takes:\n" + "\n".join(missing)


def test_unreached_edges_are_declared_and_not_taken() -> None:
    edges = declared()
    for by, source, target in UNREACHED:
        edge = next(e for e in edges.get(by, ()) if (e.source, e.target) == (source, target))
        assert not _taken(by, edge), f"{by}: {source} -> {target} is taken now"
