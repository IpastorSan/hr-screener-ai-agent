"""`sazon new-agent <name>`: a new agent's directory, ready to fill in. A spec with an example
field of a few types, one of them gated; the text packs with a stub for every line its fields
need (`engine.texts.needed_lines`), the labels and the prompts' text; and a journey per gate
and per field type, the tests a spec doesn't ship without. It loads as it is: what to change
is marked `TODO`.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from sazon.agents import AGENTS
from sazon.agents.loader import AGENT_FILE
from sazon.engine.models import Language
from sazon.engine.spec import CLOSES, AgentSpec, Channel, FieldSpec, FieldType, GateOutcome
from sazon.engine.texts import REGISTERS, Persona, TextPack, needed_lines

JOURNEYS_FILE = "journeys.yaml"
NAME = re.compile(r"[a-z][a-z0-9_]*")

EXAMPLE: dict[str, Any] = {
    "fields": [
        {"id": "full_name", "type": "person_name", "funnel_step": "name",
         "required": "unresolved_name"},
        {"id": "email", "type": "contact", "channel": "email", "funnel_step": "contact"},
        {"id": "certificate", "type": "credential", "funnel_step": "license",
         "required": "unresolved_license",
         "gate": {"readings": {"no": "close"}, "reasons": {"no": "no_license"}, "reapply": True}},
        {"id": "start_date", "type": "date", "funnel_step": "start_date", "late_after_days": 60,
         "read_back": {"after_days": 90}},
    ],
    "apply_order": ["certificate"],
}  # fmt: skip
"""The example: a name, an email, a certificate that closes on a no, a start date."""

HEADER = """\
# The {name} agent: the spec the engine reads (`engine/spec.py`; the riders' agent.yaml is a full
# example). Fields are asked in order; `funnel_step`, `required` and a gate's `reasons` are the
# agent's own names, none of the engine's (`Stage`, `DisqualifyReason`). A `checks:` or `reader:`
# names a function in the module `hooks:` names (say `hooks: sazon.agents.{name}.hooks`). What the
# agent says and calls things is in texts/; journeys.yaml has a journey per gate and field type.
"""

ANSWERS: dict[FieldType, dict[str, Any]] = {
    FieldType.PERSON_NAME: {"say": "Ana García López"},
    FieldType.CREDENTIAL: {"tap": "yes"},
    FieldType.YES_NO: {"tap": "yes"},
    FieldType.YEARS: {"say": "2"},
    FieldType.DATE: {"say": "2026-11-02"},
    FieldType.LOCATION: {"say": "TODO: a place in the service areas"},
    FieldType.FREE_TEXT: {"say": "TODO: an answer"},
}
"""A valid answer by type (enum and contact answers depend on the field)."""

READINGS: dict[str, dict[str, Any]] = {
    "no": {"tap": "no"},
    "in_progress": {"say": "TODO: still getting it"},
    "not_valid": {"say": "TODO: it has expired"},
    "out_of_area": {"say": "TODO: a place outside the service areas"},
}
"""An answer read as each reading a gate can be on."""

JOURNEYS_HEADER = """\
# The {name} agent's journeys (format: evals/journeys/README.md): one per field type and one per
# gate reading that does not pass. The answers marked TODO are the agent's to write; then move the
# file to evals/journeys/cases/{name}/, where `make journeys` plays it screening for the agent.
"""


def new_agent(name: str, directory: Path | None = None) -> Path:
    """Write the agent `name` into `directory` (by default `sazon/agents/<name>/`, where
    `SAZON_AGENT=<name>` finds it), which must not exist yet."""
    if not NAME.fullmatch(name):
        raise ValueError(f"{name!r}: an agent's name is lowercase letters, digits and _")
    directory = directory or Path(str(AGENTS)) / name
    if directory.exists():
        raise FileExistsError(f"{directory} exists")
    spec = AgentSpec.model_validate({"agent": name, **EXAMPLE})
    texts = directory / "texts"
    texts.mkdir(parents=True)
    agent = {"agent": name, "texts": texts.name, **EXAMPLE}
    (directory / AGENT_FILE).write_text(HEADER.format(name=name) + _yaml(agent), "utf-8")
    for register in REGISTERS:
        (texts / f"{register}.yaml").write_text(_yaml(_pack(spec, register)), "utf-8")
    (texts / "labels.yaml").write_text(_yaml(_labels(spec)), "utf-8")
    (texts / "prompts.yaml").write_text(_yaml(_prompts(spec)), "utf-8")
    journeys = JOURNEYS_HEADER.format(name=name) + _yaml(list(_journeys(spec)))
    (directory / JOURNEYS_FILE).write_text(journeys, "utf-8")
    return directory


def _yaml(data: Any) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)


def _pack(spec: AgentSpec, register: str) -> dict[str, Any]:
    """A stub for every line, in `register`."""
    lines = [name for name, info in TextPack.model_fields.items() if info.annotation is str]
    reasons = sorted(spec.gate_reasons)
    return {
        **{line: f"TODO ({register}): {line}" for line in lines},
        "fields": {
            field: {line: f"TODO ({register}): {field} {line}" for line in sorted(needed)}
            for field, needed in needed_lines(spec).items()
        },
        "closes": {reason: f"TODO ({register}): the close for {reason}" for reason in reasons},
    }


def _labels(spec: AgentSpec) -> dict[str, Any]:
    def named(what: str) -> dict[str, str]:
        return {language.value: f"TODO: {what}" for language in Language}

    fields: dict[str, Any] = {}
    for field in spec.fields:
        fields[field.id] = {"label": named(field.id)}
        if field.choices:
            fields[field.id]["option_labels"] = {
                language.value: {option: f"TODO: {option}" for option in field.choices}
                for language in Language
            }
    return {"fields": fields}


def _prompts(spec: AgentSpec) -> dict[str, Any]:
    """The persona, every slot of the prompts that is the domain's, an extraction line a field."""
    from sazon.llm.prompting import domain_slots

    persona = Persona(name="TODO: the agent's name", company="TODO: the company")
    slots = domain_slots(spec.model_copy(update={"persona": persona}))
    return {
        "persona": persona.model_dump(),
        "slots": {slot: f"TODO: {slot}" for slot in slots},
        "fields": {field.id: f"TODO: how the extractor reads {field.id}" for field in spec.fields},
    }


def _answer(field: FieldSpec) -> dict[str, Any]:
    if field.type == FieldType.CONTACT:
        if field.channel == Channel.EMAIL:
            return {"say": "ana.garcia@example.com"}
        return {"tap": {"country": "ES", "number": "612345678"}}
    if field.type == FieldType.ENUM:
        return {"tap": field.options[0]}
    if field.type == FieldType.MULTI_ENUM:
        return {"tap": [field.options[0]]}
    return dict(ANSWERS[field.type])


def _journeys(spec: AgentSpec) -> Iterator[dict[str, Any]]:
    """A journey per field type, answering every field up to the first of the type, and one
    per gate reading that is not a pass. Each follows the fields in order: where an `ask_if`
    leaves one out, take its step out."""

    def asks(position: int) -> str:
        return spec.fields[position].id if position < len(spec.fields) else "confirm"

    def answered(upto: int) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = [{"tap": "yes", "expect": {"asks": spec.fields[0].id}}]
        for position, field in enumerate(spec.fields[:upto]):
            steps.append({**_answer(field), "expect": {"asks": asks(position + 1)}})
        return steps

    for kind in dict.fromkeys(field.type for field in spec.fields):
        field = spec.one(kind)
        assert field is not None
        yield {
            "id": f"{spec.agent}_{kind}",
            "description": f"A {kind} answer ({field.id}) is kept and the next field asked.",
            "country": "ES",
            "steps": answered(spec.fields.index(field) + 1),
        }
    for position, field in enumerate(spec.fields):
        if field.gate is None:
            continue
        for reading, outcome in field.gate.readings.items():
            if outcome == GateOutcome.PASS:
                continue
            closes = outcome in CLOSES
            expect = (
                {"outcome": "disqualified", "reason": field.gate.reasons[reading]}
                if closes
                else {"asks": asks(position + 1)}
            )
            yield {
                "id": f"{spec.agent}_{field.id}_{reading}",
                "description": f"{field.id} read as {reading}: {outcome}.",
                "country": "ES",
                "steps": [*answered(position), {**READINGS[reading], "expect": expect}],
            }
