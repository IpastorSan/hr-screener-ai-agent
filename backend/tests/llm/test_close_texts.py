"""Every close an agent declares has its wording in every register it speaks: the engine's own
(`DisqualifyReason`), each gate's `reasons` and each field's `required` close in `agent.yaml`,
said by the register pack (`texts/<register>.yaml`) or the skeleton, by chat and by voice."""

import re

import pytest

from riders import SPEC
from sazon.agents import agent_dir
from sazon.agents.loader import load_agent
from sazon.engine.spec import AgentSpec, use
from sazon.engine.state import DisqualifyReason, ReasonId
from sazon.engine.texts import registers
from sazon.llm.fallback_templates import disqualify_text

TEACHERS = load_agent(agent_dir("teachers"))
PLACEHOLDER = re.compile(r"\{\w*\}")


def closes(spec: AgentSpec) -> set[ReasonId]:
    """Every close a candidate of `spec` can get."""
    required = {field.required for field in spec.fields if field.required is not None}
    return {*DisqualifyReason, *spec.gate_reasons, *required}


@pytest.mark.parametrize("spec", [SPEC, TEACHERS], ids=lambda spec: spec.agent)
def test_every_declared_close_is_worded_in_every_register(spec: AgentSpec) -> None:
    with use(spec):
        for register in registers(spec.countries):
            for reason in sorted(closes(spec)):
                for voice in (False, True):
                    text = disqualify_text(reason, register, voice)
                    assert text.strip(), (spec.agent, register, reason, voice)
                    assert not PLACEHOLDER.search(text), (spec.agent, register, reason, text)


def test_the_closes_cover_the_gates_and_the_required_fields() -> None:
    assert {"no_license", "out_of_area", "unresolved_name", "no_contact"} <= closes(SPEC)
    assert {"no_master", "no_certificate", "unresolved_campus"} <= closes(TEACHERS)


def test_a_close_no_agent_declares_has_no_wording() -> None:
    with use(SPEC), pytest.raises(StopIteration):
        disqualify_text("no_master", "es-ES")
