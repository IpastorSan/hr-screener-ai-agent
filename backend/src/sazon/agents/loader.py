"""An agent's spec, read from its directory: `agent.yaml` (the fields and their rules, the
interview, the dashboard), the text packs and labels in the directory it names under `texts`,
and the Python hooks its fields name, found by name in the module it names under `hooks`.

Every mistake is an `AgentError` that says which file and where in it: the key path the
engine's own checks give (`engine.spec`, `engine.texts`), or the loader's for what only it can
see (a top-level key, a label or a prompt line for no field, a skeleton line the engine has
not, a hook missing from the module).
"""

import importlib
from collections.abc import Sequence
from importlib.resources.abc import Traversable
from types import ModuleType
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from sazon.engine.spec import AgentSpec, Hooks
from sazon.engine.texts import SPANISH, Labels, Prompts, SpecError, TextPack, registers

AGENT_FILE = "agent.yaml"
KEYS = frozenset(
    {
        "agent",
        "hooks",
        "texts",
        "countries",
        "locations",
        "recruiters",
        "fields",
        "apply_order",
        "summary",
        "interview",
        "dashboard",
    }
)
"""What `agent.yaml` may say. The rest of the spec comes from the text files."""
DATA = ("locations", "recruiters")
"""The keys that name a data file in the agent's directory."""

type Path = Sequence[str | int]


class AgentError(ValueError):
    """A mistake in an agent's files: the file, the path of the bad key in it, what is wrong."""

    def __init__(self, file: str, path: Path, message: str) -> None:
        self.file = file
        self.path = tuple(path)
        where = f"{file}: {dotted(path)}" if path else file
        super().__init__(f"{where}: {message}")


def dotted(path: Path) -> str:
    """`fields[3].gate.readings`."""
    out = ""
    for key in path:
        out += f"[{key}]" if isinstance(key, int) else f".{key}" if out else str(key)
    return out


class _Files:
    """An agent's directory, its files named as errors name them (`riders/texts/en.yaml`)."""

    def __init__(self, directory: Traversable) -> None:
        self.directory = directory

    def name(self, file: str) -> str:
        return f"{self.directory.name}/{file}"

    def path(self, file: str, key: str | None = None) -> Traversable:
        """`file` in the directory; a missing one is named in `agent.yaml` at `key`, or as
        itself."""
        path = self.directory.joinpath(*file.split("/"))
        if path.is_file():
            return path
        if key is None:
            raise AgentError(self.name(file), (), "no such file")
        raise AgentError(self.name(AGENT_FILE), (key,), f"no such file {file}")

    def read(self, file: str) -> Any:
        path = self.path(file)
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            raise AgentError(self.name(file), (), f"not YAML: {error}") from None

    def validate[M: BaseModel](self, model: type[M], data: object, file: str) -> M:
        try:
            return model.model_validate(data)
        except ValidationError as error:
            raise self.located(error, {None: file}) from None

    def located(self, error: ValidationError, files: dict[str | None, str]) -> AgentError:
        """The first of `error`'s mistakes, in the file it is in: `files` by the `source` of a
        `SpecError` (None for the file validated)."""
        first = error.errors()[0]
        cause = first.get("ctx", {}).get("error")
        if isinstance(cause, SpecError):
            loc = () if cause.source else first["loc"]
            return AgentError(self.name(files[cause.source]), (*loc, *cause.path), str(cause))
        return AgentError(self.name(files[None]), first["loc"], first["msg"])


def load_agent(directory: Traversable) -> AgentSpec:
    """The agent in `directory`."""
    files = _Files(directory)
    data = files.read(AGENT_FILE)
    if not isinstance(data, dict):
        raise AgentError(files.name(AGENT_FILE), (), "an agent is a mapping of keys")
    for key in data:
        if key not in KEYS:
            raise AgentError(files.name(AGENT_FILE), (key,), f"no such key; one of {sorted(KEYS)}")
    data = dict(data)
    texts = str(data.pop("texts", "texts"))
    hooks = _module(files, data.pop("hooks", None))
    fields = [field for field in data.get("fields") or () if isinstance(field, dict)]
    ids = {str(field.get("id")) for field in fields}

    for key in DATA:
        if data.get(key) is not None:
            data[key] = str(files.path(str(data[key]), key))
    countries = data.get("countries") or AgentSpec.model_fields["countries"].default
    spoken = registers(c for c in countries if c in SPANISH)
    sources: dict[str | None, str] = {
        "labels": f"{texts}/labels.yaml",
        "prompts": f"{texts}/prompts.yaml",
        **{register: f"{texts}/{register}.yaml" for register in spoken},
    }
    labels = files.validate(Labels, files.read(sources["labels"]), sources["labels"])
    prompts = files.validate(Prompts, files.read(sources["prompts"]), sources["prompts"])
    packs = {
        register: files.validate(TextPack, files.read(sources[register]), sources[register])
        for register in spoken
    }
    dashboard = dict(data.get("dashboard") or {})
    columns = [column for column in dashboard.get("columns") or () if isinstance(column, dict)]
    _check_names(
        files, sources, labels, prompts, packs, ids, {str(c.get("field")) for c in columns}
    )
    for column in columns:
        label = labels.dashboard.columns.get(str(column.get("field")))
        if label is not None:
            column.setdefault("label", label)
    spec = {
        **data,
        "fields": [_texts(field, labels, prompts) for field in data.get("fields") or ()],
        "fix_words": labels.fix_patterns(),
        "interviewers": labels.interviewers,
        "place_label": labels.place_label,
        "dashboard": {
            **dashboard,
            "columns": columns,
            "labels": labels.dashboard.fields,
            "country_labels": labels.dashboard.by_country,
            "step_labels": labels.dashboard.steps,
        },
        "hooks": _hooks(hooks, fields),
        "texts": packs,
        "persona": prompts.persona,
        "prompt_slots": prompts.slots,
    }
    try:
        return AgentSpec.model_validate(spec)
    except ValidationError as error:
        raise files.located(error, {**sources, None: AGENT_FILE}) from None


def _texts(field: Any, labels: Labels, prompts: Prompts) -> Any:
    """A field with its names from the labels and its extraction line from the prompts, unless
    it says them itself."""
    if not isinstance(field, dict):
        return field
    id = str(field.get("id"))
    named = labels.fields.get(id)
    said = named.model_dump(exclude_unset=True) if named is not None else {}
    return {**said, "extract": prompts.fields.get(id, ""), **field}


def _module(files: _Files, name: object) -> ModuleType | None:
    if name is None:
        return None
    try:
        return importlib.import_module(str(name))
    except ImportError as error:
        raise AgentError(files.name(AGENT_FILE), ("hooks",), f"no module {name}: {error}") from None


def _hooks(module: ModuleType | None, fields: list[dict[str, Any]]) -> Hooks:
    """The hooks the fields name that `module` has; the spec rejects a field that names one it
    has not, with where."""

    def found(names: set[str]) -> dict[str, Any]:
        return {name: getattr(module, name) for name in names if hasattr(module, name)}

    checks = {name for field in fields for name in field.get("checks") or ()}
    readers = {field["reader"] for field in fields if field.get("reader")}
    return Hooks(checks=found(checks), readers=found(readers))


def _check_names(
    files: _Files,
    sources: dict[str | None, str],
    labels: Labels,
    prompts: Prompts,
    packs: dict[str, TextPack],
    ids: set[str],
    columns: set[str],
) -> None:
    """Labels and prompt lines name fields the agent has, and a pack's skeleton lines ones the
    engine has."""
    from sazon.llm.fallback_templates import SKELETON

    named = {
        ("labels", ("fields",)): (labels.fields, ids, "field"),
        ("labels", ("fix_words",)): (labels.fix_words, ids, "field"),
        ("labels", ("dashboard", "fields")): (labels.dashboard.fields, ids, "field"),
        ("labels", ("dashboard", "by_country")): (labels.dashboard.by_country, ids, "field"),
        ("labels", ("dashboard", "columns")): (labels.dashboard.columns, columns, "column"),
        ("prompts", ("fields",)): (prompts.fields, ids, "field"),
        **{
            (register, ("skeleton",)): (pack.skeleton, SKELETON.keys(), "skeleton line")
            for register, pack in packs.items()
        },
    }
    for (source, path), (keyed, known, what) in named.items():
        for key in keyed:
            if key not in known:
                raise AgentError(files.name(sources[source]), (*path, key), f"no {what} {key}")
