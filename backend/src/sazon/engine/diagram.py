"""The statechart the code declares, as a Mermaid diagram (`docs/engine/states.mmd`, written by
`sazon engine-diagram`): the phases and their sub-states nested, and every edge the handlers,
the interrupts and the entry points declare. Edges with the same ends share one arrow."""

from collections.abc import Iterable
from functools import cache

from sazon.engine.interrupts import INTERRUPTS
from sazon.engine.machine import EDGES
from sazon.engine.state import Node
from sazon.engine.states import HANDLERS
from sazon.engine.states.handler import Edge

HEADER = (
    "%% Generated from the edges the engine declares (`sazon/engine/diagram.py`). Regenerate"
    " with `make diagram`;\n%% `make check` fails while it differs from the code."
)


@cache
def declared() -> dict[str, frozenset[Edge]]:
    """Every declared edge, by what takes it: a handler (its phase), an interrupt (its name)
    or an entry point (`machine.EDGES`)."""
    return {
        **{str(kind): handler.edges for kind, handler in HANDLERS.items()},
        **{each.name: each.edges for each in INTERRUPTS},
        **EDGES,
    }


def _parent(node: Node) -> Node | None:
    return Node(node.rsplit(".", 1)[0]) if "." in node else None


def _id(node: Node) -> str:
    return "[*]" if node is Node.START else node.replace(".", "_")


def _common(a: Node, b: Node) -> Node | None:
    """The innermost parent of both: where the arrow between them is written."""
    parent = _parent(a)
    while parent is not None and not b.within(parent):
        parent = _parent(parent)
    return parent


def render() -> str:
    order = {node: index for index, node in enumerate(Node)}
    labels: dict[tuple[Node, Node], list[str]] = {}
    for edges in declared().values():
        for edge in sorted(edges, key=lambda e: (order[e.source], order[e.target], e.event)):
            events = labels.setdefault((edge.source, edge.target), [])
            if edge.event not in events:
                events.append(edge.event)
    arrows: dict[Node | None, list[str]] = {}
    for source, target in sorted(labels, key=lambda ends: (order[ends[0]], order[ends[1]])):
        label = " / ".join(labels[source, target])
        arrow = f"{_id(source)} --> {_id(target)}: {label}"
        arrows.setdefault(_common(source, target), []).append(arrow)

    children: dict[Node | None, list[Node]] = {}
    for node in Node:
        if node is not Node.START:
            children.setdefault(_parent(node), []).append(node)

    def block(parent: Node | None, depth: int) -> Iterable[str]:
        pad = "    " * depth
        for node in children.get(parent, []):
            name = node.rsplit(".", 1)[-1]
            if node in children:
                yield f'{pad}state "{name}" as {_id(node)} {{'
                yield from block(node, depth + 1)
                yield f"{pad}}}"
            elif name != _id(node):
                yield f'{pad}state "{name}" as {_id(node)}'
        for arrow in arrows.get(parent, []):
            yield pad + arrow

    return "\n".join([HEADER, "stateDiagram-v2", *block(None, 1)]) + "\n"
