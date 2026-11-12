"""An agent's text packs: what it says, one pack per register (`es-ES`, `es-MX`, `en`), read
from `<register>.yaml` in its `texts/` directory by the agent loader (`sazon.agents.loader`).

The engine's own lines (offering slots, confirming a booking, the pause, abuse and policy closes,
"¿Quieres continuar como...?") are its skeleton, in `llm/fallback_templates.py`. A pack holds
the domain's lines: the greeting and the overview, one ask per field, the hints, the refusals,
the read-backs and the closes. It must supply every line its spec's fields and gates need, and
may override a skeleton line by its name (`skeleton:`): loading fails on a missing line, on a
line for no field, or on a placeholder the line is never given.
"""

import string
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Literal

from pydantic import model_validator

from sazon.engine.models import FieldId, Language, StepId
from sazon.engine.spoken import Register
from sazon.engine.state import ReasonId, Strict
from sazon.engine.validators import Country, ReasonCode

if TYPE_CHECKING:
    from sazon.engine.spec import AgentSpec

REGISTERS: tuple[Register, ...] = ("es-ES", "es-MX", "en")
SPANISH: Mapping[Country, Register] = {"ES": "es-ES", "MX": "es-MX"}
"""Spanish as it is spoken in each country: tú in Spain, usted in Mexico."""


def registers(countries: Iterable[Country]) -> tuple[Register, ...]:
    """The registers an agent hiring in `countries` speaks: each one's Spanish, and English."""
    spoken = {SPANISH[country] for country in countries}
    return tuple(register for register in REGISTERS if register == "en" or register in spoken)


class SpecError(ValueError):
    """A mistake in an agent's spec, and where it is: `path`, the keys and list positions to it
    in the data of the model that found it, or in the file `source` names (`labels`, `prompts`,
    a register's pack) when the mistake is in one of those."""

    def __init__(self, message: str, *path: str | int, source: str | None = None) -> None:
        super().__init__(message)
        self.path = path
        self.source = source


class FieldTexts(Strict):
    """What is said about one field. Which lines a field needs follows from its type and rules
    (`needed_lines`); the placeholders each line is given are in `PLACEHOLDERS`."""

    ask: str
    ask_voice: str | None = None
    """By voice, when the chat's question names options the buttons show."""
    refusal: str | None = None
    """Why it is asked, to a candidate who would rather not say. An optional field without
    one gets the skeleton's "No es obligatorio..."."""
    refusal_again: str | None = None
    """What happens without it, to one who refuses again."""
    unresolved: str | None = None
    """`required`: what could not be resolved, before "así que no puedo seguir..."."""
    read_back: str | None = None
    """The value read back before it is kept: `years` past `over`, a `date` past `after_days`,
    a `location` outside the areas, a `credential` no by voice."""
    read_back_voice: str | None = None
    """`years`: the read-back by voice."""
    check: str | None = None
    """`years` with `checks`: the option that makes the value doubtful, asked about."""
    zone: str | None = None
    """`location` with `zone_question_from`: which zone of a city with many locations."""


PLACEHOLDERS: Mapping[str, frozenset[str]] = {
    "read_back": frozenset({"value", "years"}),
    "read_back_voice": frozenset({"years"}),
    "check": frozenset({"option"}),
    "zone": frozenset({"city"}),
}
"""The placeholders a line may use; a line not listed takes none."""


class TextPack(Strict):
    """The domain's lines in one register."""

    greet: str
    greet_voice: str
    overview: str
    """Right after consent, before the first question: what the process looks like."""
    overview_voice: str
    busy: str
    """A new chat while the daily model budget is spent."""
    clarify_open: str
    """An answer that matched too many values to list: ask for more detail."""
    fields: Mapping[FieldId, FieldTexts]
    hints: Mapping[ReasonCode, str] = {}
    """Before the question again, what was wrong with the answer; else the skeleton's retry."""
    closes: Mapping[ReasonId, str] = {}
    """Each gate close, by its reason."""
    skeleton: Mapping[str, str] = {}
    """Skeleton lines said otherwise, by the name of their `Skeleton` in
    `llm/fallback_templates.py` (the agent loader checks the names)."""

    @model_validator(mode="after")
    def _known_placeholders(self) -> "TextPack":
        for field, texts in self.fields.items():
            for name, line in texts:
                if line is None:
                    continue
                extra = _placeholders(line) - PLACEHOLDERS.get(name, frozenset())
                if extra:
                    raise SpecError(
                        f"{field}.{name}: no placeholder {sorted(extra)}", "fields", field, name
                    )
        return self


class FieldLabels(Strict):
    """A field's name and its options' names, by language, as `FieldSpec` holds them, and the
    words that read as a credential's readings (`FieldSpec.readings`)."""

    label: Mapping[Language, str]
    country_labels: Mapping[Country, Mapping[Language, str]] = {}
    """`label` where a country names it otherwise ("Licencia de conducir" in Mexico)."""
    option_labels: Mapping[Language, Mapping[str, str]] = {}
    none_label: Mapping[Language, str] = {}
    """`multi_enum`: an empty choice ("Ninguna")."""
    readings: Mapping[str, tuple[str, ...]] = {}


class DashboardLabels(Strict):
    """The recruiters' names for fields and funnel steps, where not the summary card's."""

    fields: Mapping[FieldId, str] = {}
    by_country: Mapping[FieldId, Mapping[Country, str]] = {}
    steps: Mapping[StepId, str] = {}
    columns: Mapping[FieldId, str] = {}
    """A column's header, when not the field's name."""


class Labels(Strict):
    """What an agent calls things (`labels.yaml`): by language, not by register, the summary
    card and the dashboard are the same in both countries but where `country_labels` says so."""

    fields: Mapping[FieldId, FieldLabels]
    dashboard: DashboardLabels = DashboardLabels()
    fix_words: Mapping[FieldId, tuple[str, ...]] = {}
    """The words that name each field when the candidate says it is wrong, as regexes on
    folded text: any of them, field by field in this order."""
    place_label: Mapping[Language, str] = {}
    """A place said and the location it is in ("{place} · sede de {location}")."""
    interviewers: Mapping[Literal["onsite", "video"], tuple[str, ...]] = {}
    """Who runs the interview in each mode, as regexes on folded text (`AgentSpec`)."""

    def fix_patterns(self) -> dict[FieldId, str]:
        return {field: "|".join(words) for field, words in self.fix_words.items()}


class Persona(Strict):
    """Who the agent is to the candidate."""

    name: str
    company: str


class Prompts(Strict):
    """An agent in the model prompts (`prompts.yaml`): who it is, the text of each `{slot}` in
    `llm/prompts/*.md` that is about its domain, and how the extractor reads each field."""

    persona: Persona
    slots: Mapping[str, str] = {}
    fields: Mapping[FieldId, str]


def _placeholders(line: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(line) if name}


def needed_lines(spec: "AgentSpec") -> dict[FieldId, set[str]]:
    """The lines each field needs, by its type and rules."""
    from sazon.engine.spec import FieldType

    needed: dict[FieldId, set[str]] = {}
    for field in spec.fields:
        lines = {"ask"}
        if field.required is not None or field.type == FieldType.CONTACT:
            lines |= {"refusal", "refusal_again"}
        if field.required is not None:
            lines.add("unresolved")
        match field.type:
            case FieldType.YEARS if field.read_back.over is not None:
                lines |= {"read_back", "read_back_voice"}
            case FieldType.DATE if field.read_back.after_days is not None:
                lines.add("read_back")
            case FieldType.CREDENTIAL if field.voice_read_back:
                lines.add("read_back")
            case FieldType.LOCATION if (
                field.gate is not None and "out_of_area" in field.gate.readings
            ):
                lines.add("read_back")  # a place outside the areas, before it closes
            case _:
                pass
        if field.checks:
            lines.add("check")
        if field.zone_question_from is not None:
            lines.add("zone")
        needed[field.id] = lines
    return needed


def _check_labels(spec: "AgentSpec") -> None:
    """Every field has a name, and every option of one a label, in every language."""

    for field in spec.fields:
        nameless = sorted(language.value for language in set(Language) - set(field.label))
        if nameless:
            raise SpecError(
                f"labels: {field.id} has no name in {nameless}",
                *("fields", field.id, "label"),
                source="labels",
            )
        named = set(field.choices)
        if field.gate is not None:  # a reading kept as the value is shown by its name
            named |= {reading for reading in field.gate.readings if field.gate.flags(reading)}
        if not named:
            continue
        for language in Language:
            unnamed = named - set(field.option_labels.get(language, {}))
            if unnamed:
                raise SpecError(
                    f"labels: {field.id} has no {language} label for {sorted(unnamed)}",
                    *("fields", field.id, "option_labels", language.value),
                    source="labels",
                )


def check_texts(spec: "AgentSpec", texts: Mapping[Register, TextPack]) -> None:
    """Every register has a pack, and each pack every line the spec needs, a line for a field
    the spec has, a close for each gate reason and the same hints as the others; every field
    and option has a name in every language, and an extraction line; the agent has a persona."""
    _check_labels(spec)
    if spec.persona is None:
        raise SpecError("texts: an agent that speaks has a persona", "persona", source="prompts")
    unread = [field.id for field in spec.fields if not field.extract]
    if unread:
        raise SpecError(
            f"prompts: no extraction line for {unread}", "fields", unread[0], source="prompts"
        )
    missing_registers = sorted(set(registers(spec.countries)) - set(texts))
    if missing_registers:
        raise SpecError(f"texts: no pack for {missing_registers}", source=missing_registers[0])
    needed = needed_lines(spec)
    reasons = spec.gate_reasons
    hints = set(next(iter(texts.values())).hints)
    for register, pack in texts.items():
        if set(pack.hints) != hints:
            raise SpecError(
                "texts: the packs give hints for different reasons", "hints", source=register
            )
        unknown = sorted(set(pack.fields) - set(needed))
        if unknown:
            raise SpecError(
                f"texts {register}: no field {unknown}", "fields", unknown[0], source=register
            )
        for field, lines in needed.items():
            given = pack.fields.get(field)
            have = {name for name, line in given if line is not None} if given else set()
            missing = sorted(lines - have)
            if missing:
                raise SpecError(
                    f"texts {register}: {field} has no {missing}",
                    *("fields", field, missing[0]),
                    source=register,
                )
        if set(pack.closes) != reasons:
            raise SpecError(
                f"texts {register}: closes for {sorted(pack.closes)}, gates close on "
                f"{sorted(reasons)}",
                "closes",
                source=register,
            )
