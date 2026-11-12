"""Text packs: the rider agent's lines, a pack that misses a line the spec needs is rejected,
and a pack can say a skeleton line otherwise."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from riders import SPEC
from sazon.agents.riders import Close
from sazon.engine.actions import ConfirmBooking
from sazon.engine.models import Language
from sazon.engine.slots import Booked
from sazon.engine.spec import AgentSpec, use
from sazon.engine.spoken import Register
from sazon.engine.texts import REGISTERS, TextPack, check_texts
from sazon.engine.validators import ReasonCode
from sazon.llm import ReplyContext
from sazon.llm.fallback_templates import fallback_reply

BOOKED = Booked(
    slotId="s1",
    startsAt="2026-10-22T09:00:00+02:00",
    label="jueves 22 de octubre a las 09:00",
    location="Madrid Centro",
    address="Calle Mayor 1",
)


def _pack(register: Register = "es-ES") -> dict[str, Any]:
    return SPEC.texts[register].model_dump(mode="json", exclude_defaults=True)


def _texts(**changes: dict[str, Any]) -> dict[Register, TextPack]:
    """The rider packs, with `changes` (by register) made to their data."""
    packs: dict[Register, TextPack] = {}
    for register in REGISTERS:
        data = _pack(register)
        for key, value in changes.get(register.replace("-", "_"), {}).items():
            data[key] = value
        packs[register] = TextPack.model_validate(data)
    return packs


def test_the_rider_packs_have_a_line_for_every_field():
    for register in REGISTERS:
        assert set(SPEC.texts[register].fields) == set(SPEC.ids)


@pytest.mark.parametrize(
    ("field", "line"),
    [
        ("full_name", "unresolved"),  # required
        ("email", "refusal"),  # a contact
        ("has_license", "read_back"),  # a credential read back by voice
        ("city", "zone"),  # a location with a zone question
        ("experience_years", "read_back_voice"),  # years read back past `over`
        ("experience_years", "check"),  # years with a check
        ("start_date", "read_back"),  # a date read back past `after_days`
    ],
)
def test_a_pack_missing_a_line_the_spec_needs_is_rejected(field: str, line: str):
    fields = _pack()["fields"]
    del fields[field][line]
    texts = _texts(es_ES={"fields": fields})
    with pytest.raises(ValueError, match=f"es-ES: {field} has no \\['{line}'\\]"):
        check_texts(SPEC, texts)


def test_a_pack_for_a_field_or_a_close_the_spec_lacks_is_rejected():
    fields = {**_pack()["fields"], "campus": {"ask": "¿En qué campus?"}}
    with pytest.raises(ValueError, match="no field \\['campus'\\]"):
        check_texts(SPEC, _texts(es_ES={"fields": fields}))
    closes = {**_pack()["closes"], Close.UNRESOLVED_NAME.value: "No."}
    with pytest.raises(ValueError, match="closes for"):
        check_texts(SPEC, _texts(es_MX={"closes": closes}))
    with pytest.raises(ValueError, match="no pack for \\['en'\\]"):
        check_texts(SPEC, {r: p for r, p in SPEC.texts.items() if r != "en"})


def test_the_packs_give_the_same_hints():
    hints = {k: v for k, v in _pack("en")["hints"].items() if k != ReasonCode.PAST_DATE}
    with pytest.raises(ValueError, match="hints for different reasons"):
        check_texts(SPEC, _texts(en={"hints": hints}))


def test_a_line_with_a_placeholder_it_is_never_given_is_rejected():
    fields = _pack()["fields"]
    fields["start_date"]["read_back"] = "Tengo el {date} como fecha de inicio."
    with pytest.raises(ValidationError, match="no placeholder \\['date'\\]"):
        _texts(es_ES={"fields": fields})


def test_a_pack_may_say_a_skeleton_line_otherwise():
    line = "Listo, tu entrevista es el {when} con la jefa de estudios de {location}, {address}."
    texts = _texts(es_ES={"skeleton": {"confirm_booking": line}})
    action = ConfirmBooking(booking=BOOKED, language=Language.ES, country="ES")
    ctx = ReplyContext.for_action(action, today=date(2026, 10, 21))
    with use(SPEC.model_copy(update={"texts": texts})):
        said = fallback_reply(action, ctx)
    assert said.startswith(
        "Listo, tu entrevista es el jueves 22 de octubre a las 09:00 con la jefa"
    )
    assert "persona encargada" in fallback_reply(action, ctx)


def _with_field(field: str, **update: Any) -> AgentSpec:
    fields = tuple(f.model_copy(update=update) if f.id == field else f for f in SPEC.fields)
    return SPEC.model_copy(update={"fields": fields})


def test_a_field_or_an_option_without_a_name_is_rejected():
    labels = SPEC.field("schedule").option_labels
    english = {k: v for k, v in labels[Language.EN].items() if k != "evening"}
    spec = _with_field("schedule", option_labels={**labels, Language.EN: english})
    with pytest.raises(ValueError, match="schedule has no en label for \\['evening'\\]"):
        check_texts(spec, SPEC.texts)
    with pytest.raises(ValueError, match="email has no name in \\['en'\\]"):
        check_texts(_with_field("email", label={Language.ES: "Email"}), SPEC.texts)


def test_a_field_without_an_extraction_line_or_an_agent_without_a_persona_is_rejected():
    with pytest.raises(ValueError, match="no extraction line for \\['city'\\]"):
        check_texts(_with_field("city", extract=""), SPEC.texts)
    with pytest.raises(ValueError, match="has a persona"):
        check_texts(SPEC.model_copy(update={"persona": None}), SPEC.texts)
