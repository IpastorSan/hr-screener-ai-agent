"""The prompts are templates the agent fills: the riders' fill every slot, every slot they give
is used, and a slot with nothing to fill it stops the prompt from being built."""

from importlib.resources import files

import pytest

from riders import SPEC
from sazon.llm.prompting import SLOT, fill, load_prompt, prompt

PROMPTS = sorted(p.name for p in files("sazon.llm.prompts").iterdir() if p.name.endswith(".md"))


@pytest.mark.parametrize("name", PROMPTS)
def test_the_rider_agent_fills_every_slot(name: str):
    text = prompt(name, SPEC)
    assert not SLOT.search(text)


def test_every_slot_the_riders_give_is_in_a_prompt():
    used = {slot for name in PROMPTS for slot in SLOT.findall(load_prompt(name))}
    assert set(SPEC.prompt_slots) <= used
    assert {"persona", "company", "fields"} <= used


def test_the_prompts_name_no_rider():
    for name in PROMPTS:
        text = load_prompt(name)
        for word in ("Ana", "Sazón", "carnet", "licencia", "reparto", "delivery", "Glovo", "Rappi"):
            assert word not in text, (name, word)


def test_a_slot_with_nothing_to_fill_it_is_an_error():
    with pytest.raises(ValueError, match="nothing for the prompt slots \\['campus'\\]"):
        fill(SPEC, "You work at the {campus}.")
