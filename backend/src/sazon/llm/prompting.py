"""The model prompts (`prompts/*.md`) are templates: each `{slot}` is the agent's (its persona,
its domain's examples and wording, a line per field to extract), so the prompts name no
domain. Filled once per spec, when the model clients are built."""

import re
from importlib.resources import files

from sazon.engine.spec import AgentSpec, agent

SLOT = re.compile(r"\{(\w+)\}")


def load_prompt(name: str) -> str:
    """A prompt as written, its slots unfilled."""
    return files("sazon.llm.prompts").joinpath(name).read_text(encoding="utf-8")


def domain_slots(spec: AgentSpec) -> list[str]:
    """The slots of every prompt the spec's `prompt_slots` must fill: those `slots` does not
    fill from the rest of the spec."""
    prompts = files("sazon.llm.prompts").iterdir()
    found = {
        s for p in prompts if p.name.endswith(".md") for s in SLOT.findall(load_prompt(p.name))
    }
    return sorted(found - (slots(spec).keys() - spec.prompt_slots.keys()))


def slots(spec: AgentSpec) -> dict[str, str]:
    """What fills each slot: the spec's `prompt_slots`, the persona's name and company, and the
    fields' extraction lines in the order they are asked."""
    persona = (
        {"persona": spec.persona.name, "company": spec.persona.company} if spec.persona else {}
    )
    lines = "\n".join(f"- `{field.id}`: {field.extract}" for field in spec.fields)
    return {**spec.prompt_slots, **persona, "fields": lines}


def fill(spec: AgentSpec, text: str) -> str:
    """`text` with every slot filled from `spec`; a slot the spec has nothing for is an
    error, never left for the model to read."""
    filled = slots(spec)
    missing = set(SLOT.findall(text)) - filled.keys()
    if missing:
        raise ValueError(f"{spec.agent}: nothing for the prompt slots {sorted(missing)}")
    return SLOT.sub(lambda match: filled[match.group(1)], text)


def prompt(name: str, spec: AgentSpec | None = None) -> str:
    """A prompt filled for `spec`, by default the agent in use."""
    return fill(spec or agent(), load_prompt(name))
