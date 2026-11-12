"""The engine's public calls as JSON cases: what `record` writes and `test_replay` plays back.

A case is one call at the engine boundary, `{"case", "fn", "args", "out"}`: the journey or
persona it came from, the `sazon.engine` function, every argument the caller passed (by name,
the clock's `today` included) and what it returned, all as `model_dump(mode="json")` would
write them. Arguments are read back through the function's own type hints, so a case replays
against the engine exactly as it was called.
"""

import gzip
import inspect
import json
from collections.abc import Callable, Iterable, Iterator, Sequence
from functools import cache
from pathlib import Path
from typing import Any, TypedDict, get_type_hints

from pydantic import TypeAdapter
from pydantic_core import to_jsonable_python

from sazon import engine
from sazon.engine import (
    BookSlot,
    ConversationState,
    DuplicateChecked,
    FindModes,
    FindSlots,
    ModesFound,
    SlotBooked,
    SlotsFound,
)
from sazon.engine.models import InterviewMode
from sazon.engine.slots import Booked, Miss, Slot
from sazon.engine.state import DuplicateMatch
from sazon.engine.turn import Turn

CORPUS_PATH = Path(__file__).parent / "corpus.jsonl.gz"

API = (
    "start",
    "step",
    "on_lookup",
    "reoffer",
    "re_engage",
    "abandon",
    "reopen",
    "restart",
    "resume",
    "current_prompt",
    "awaits_reoffer",
    "awaits_talent_pool_answer",
)
"""Every engine function the service calls; internal calls between them are not cases."""


class Case(TypedDict):
    case: str
    fn: str
    args: dict[str, Any]
    out: Any


def _apply_duplicate_result(
    state: ConversationState, match: DuplicateMatch | None, *, policy_closes: int = 0
) -> Turn:
    return engine.on_lookup(state, DuplicateChecked(match=match, policy_closes=policy_closes))


def _apply_modes(
    state: ConversationState, request: FindModes, modes: Sequence[InterviewMode]
) -> Turn:
    return engine.on_lookup(state, ModesFound(request=request, modes=modes))


def _apply_slots(
    state: ConversationState, request: FindSlots, slots: Sequence[Slot], *, miss: Miss | None = None
) -> Turn:
    return engine.on_lookup(state, SlotsFound(request=request, slots=slots, miss=miss))


def _apply_booking(state: ConversationState, request: BookSlot, booked: Booked | None) -> Turn:
    return engine.on_lookup(state, SlotBooked(request=request, booked=booked))


LEGACY: dict[str, Callable[..., Any]] = {
    "apply_duplicate_result": _apply_duplicate_result,
    "apply_modes": _apply_modes,
    "apply_slots": _apply_slots,
    "apply_booking": _apply_booking,
}
"""The four calls `on_lookup` replaced (SAZ-90), as cases recorded before it name them: played
back through it, with their arguments read as they were written."""


def engine_function(name: str) -> Callable[..., Any]:
    return LEGACY.get(name) or getattr(engine, name)


@cache
def _adapters(name: str) -> dict[str, TypeAdapter[Any]]:
    hints = get_type_hints(engine_function(name))
    return {param: TypeAdapter(hint) for param, hint in hints.items() if param != "return"}


def encode(value: Any) -> Any:
    return to_jsonable_python(value)


def encode_args(name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """The arguments as passed, by parameter name; defaults the caller left out stay out."""
    bound = inspect.signature(engine_function(name)).bind(*args, **kwargs)
    return {param: encode(value) for param, value in bound.arguments.items()}


def decode_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    adapters = _adapters(name)
    return {param: adapters[param].validate_python(value) for param, value in args.items()}


def call(case: Case) -> Any:
    """Play one case against the current engine; the result as JSON."""
    return encode(engine_function(case["fn"])(**decode_args(case["fn"], case["args"])))


def expected(case: Case) -> Any:
    """What the case recorded, its states read as the engine stores them now: a state recorded
    before it carried its phase goes through the version 1 reader (`engine/upgrade.py`)."""
    out = case["out"]
    if isinstance(out, list):
        return [_current(value) for value in out]
    return _current(out)


def is_state(value: Any) -> bool:
    """A `ConversationState` as JSON, in either stored shape."""
    return isinstance(value, dict) and "country_source" in value


def _current(value: Any) -> Any:
    return encode(ConversationState.model_validate(value)) if is_state(value) else value


def case_key(case: Case) -> str:
    return json.dumps([case["fn"], case["args"]], sort_keys=True, ensure_ascii=False)


def dedupe(cases: Iterable[Case]) -> list[Case]:
    """The first case of every distinct call: the engine is a pure function of its inputs."""
    seen: dict[str, Case] = {}
    for case in cases:
        seen.setdefault(case_key(case), case)
    return list(seen.values())


def save(cases: Iterable[Case], path: Path = CORPUS_PATH) -> None:
    # mtime=0 and no file name in the header: the same corpus is the same bytes.
    with (
        path.open("wb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as out,
    ):
        for case in cases:
            line = json.dumps(case, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            out.write(line.encode() + b"\n")


def load(path: Path = CORPUS_PATH) -> Iterator[Case]:
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            yield json.loads(line)
