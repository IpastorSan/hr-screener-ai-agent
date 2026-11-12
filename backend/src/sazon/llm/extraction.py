"""What the extractor is asked to report, generated from the agent spec: the output model, with
one optional, typed attribute per field (its options as an enum); the prompt's line for each
field is filled in by `llm.prompting`. The engine reads `TurnExtraction`, with the fields as a
map by id: `to_turn` gives it."""

from typing import Any, Literal, cast

from pydantic import BaseModel, Field, create_model

from sazon.engine.models import FieldCandidate, PhoneInput, TurnExtraction
from sazon.engine.spec import AgentSpec, Channel, FieldSpec, FieldType, agent

VALUES: dict[FieldType, str] = {
    FieldType.CREDENTIAL: "true, false, or 'in_progress' if they are still getting it.",
    FieldType.YEARS: "Number of years (months / 12), or the candidate's words if vague.",
    FieldType.DATE: "ISO date (YYYY-MM-DD) resolved against today's date.",
}
"""The value's description, by type, where its JSON type does not say it all."""


def _value_type(field: FieldSpec) -> Any:
    """The value's type for the extractor. An option it may name otherwise (an "other") or
    in words the field's reader understands passes as a string: the enum guides it, never
    rejects an answer the engine reads."""
    options = Literal[field.choices] if field.choices else str
    match field.type:
        case FieldType.CREDENTIAL:
            return bool | str
        case FieldType.YES_NO:
            return bool
        case FieldType.ENUM:
            return options | str
        case FieldType.MULTI_ENUM:
            return list[options | str] | str
        case FieldType.YEARS:
            return float | str
        case FieldType.CONTACT if field.channel == Channel.PHONE:
            return str | PhoneInput
        case _:
            return str


def _candidate(field: FieldSpec) -> type[BaseModel]:
    return create_model(
        f"{field.id}_value",
        value=(_value_type(field), Field(description=VALUES.get(field.type))),
        confidence=(float, FieldCandidate.model_fields["confidence"]),
    )


_models: dict[int, tuple[AgentSpec, type[TurnExtraction]]] = {}


def output_model(spec: AgentSpec) -> type[TurnExtraction]:
    """`TurnExtraction` with `fields` as an object of the spec's fields, each optional; built
    once per spec."""
    if id(spec) not in _models:
        _models[id(spec)] = (spec, _build(spec))
    return _models[id(spec)][1]


def _build(spec: AgentSpec) -> type[TurnExtraction]:
    attributes: dict[str, Any] = {f.id: (_candidate(f) | None, None) for f in spec.fields}
    fields = create_model("Fields", **attributes)
    return create_model(
        "TurnExtraction",
        __base__=TurnExtraction,
        fields=(
            fields,
            Field(
                default_factory=fields,
                description="The fields stated in this message; leave out (or null) any the "
                "candidate did not actually state.",
            ),
        ),
    )


def to_turn(output: TurnExtraction) -> TurnExtraction:
    """The extractor's output as the engine reads it: the fields stated, by id."""
    stated = cast(BaseModel, output.fields)
    fields = {
        name: FieldCandidate(value=candidate.value, confidence=candidate.confidence)
        for name, candidate in stated
        if candidate is not None
    }
    return TurnExtraction.model_validate({**dict(output), "fields": fields})


def parse_output(data: Any, spec: AgentSpec | None = None) -> TurnExtraction:
    """An output as the extractor writes it (JSON-like), read as the engine reads it."""
    return to_turn(output_model(spec or agent()).model_validate(data))
