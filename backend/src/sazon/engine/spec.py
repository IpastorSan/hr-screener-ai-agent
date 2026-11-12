"""The agent spec: what an agent screens for. Its fields, in the order they are asked, each
with a type (the engine's behaviour for it) and the rules it carries: required, a gate, a
condition on earlier answers, a read-back, named checks. The engine reads the spec and never
names a field; the domain lives in a package of its own (`sazon.agents.riders`).

Config chooses among the behaviours the engine has, it can't add one: a rule no field type or
predicate covers is a named Python hook in the domain package (`Hooks`).
"""

import importlib
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from functools import cached_property
from typing import Any, Self

from pydantic import model_validator

from sazon.engine.models import (
    CandidateProfile,
    FieldId,
    Language,
    Platform,
    QualityFlag,
    Stage,
    StepId,
)
from sazon.engine.spoken import Register
from sazon.engine.state import DisqualifyReason, ReasonId, Strict
from sazon.engine.text import fold
from sazon.engine.texts import Persona, SpecError, TextPack, check_texts
from sazon.engine.validators import LICENSE_WORDS, OTHER, Country, Result, _words

AGENTS = "sazon.agents"
"""Where the agent the engine screens for comes from (`selected()`: the one `SAZON_AGENT`
names), imported by name: the engine reads no settings and no files."""


class FieldType(StrEnum):
    """How the engine reads, stores, asks and shows a field."""

    PERSON_NAME = "person_name"
    """First name and surname, a lone surname joined to the first name given before."""
    CONTACT = "contact"
    """A phone (country picker, E.164) or an email (domain-typo read-back): either is enough,
    and one in starts the duplicate check."""
    CREDENTIAL = "credential"
    """Has it, or why not: still getting it, not valid now, or no. A no is read back by voice
    and taken only past a confidence floor."""
    YES_NO = "yes_no"
    ENUM = "enum"
    MULTI_ENUM = "multi_enum"
    """Several options, one of them maybe exclusive, and an "other" with its free text."""
    YEARS = "years"
    """Months to years, vague answers asked again, a cap and a read-back past a threshold."""
    DATE = "date"
    """Relative dates, a late flag, a read-back of a far date or a day and month gone by."""
    LOCATION = "location"
    """Matched against the service areas: a loose match read back, the zone asked in a city
    with many locations, a place outside them read back before it closes."""
    FREE_TEXT = "free_text"


class Channel(StrEnum):
    PHONE = "phone"
    EMAIL = "email"


class GateOutcome(StrEnum):
    PASS = "pass"
    FLAG = "flag"
    CLOSE = "close"
    CLOSE_TALENT_POOL = "close_talent_pool"
    """Close, and offer to keep the data for future vacancies."""


CLOSES = frozenset({GateOutcome.CLOSE, GateOutcome.CLOSE_TALENT_POOL})

YES_NO_TYPES = frozenset({FieldType.CREDENTIAL, FieldType.YES_NO})
"""Answered with a yes or a no, as buttons."""


READINGS: Mapping[FieldType, frozenset[str]] = {
    FieldType.CREDENTIAL: frozenset({"yes", "no", "in_progress", "not_valid"}),
    FieldType.LOCATION: frozenset({"out_of_area"}),
}
"""What a gate can be on, by type: the readings of a value the engine tells apart."""


class Gate(Strict):
    """What each reading of the field leads to (one not listed passes), and the reason each
    close is recorded with. `reopenable`: the next message may undo the close by contradicting
    it. `reapply`: the close invites the candidate to apply again once it no longer holds."""

    readings: Mapping[str, GateOutcome]
    reasons: Mapping[str, ReasonId] = {}
    reopenable: bool = False
    reapply: bool = False

    @model_validator(mode="after")
    def _a_reason_per_close(self) -> Self:
        closing = {reading for reading, outcome in self.readings.items() if outcome in CLOSES}
        if closing != set(self.reasons):
            raise SpecError(
                f"reasons for {sorted(self.reasons)}, closes on {sorted(closing)}", "reasons"
            )
        return self

    def outcome(self, reading: str) -> GateOutcome:
        return self.readings.get(reading, GateOutcome.PASS)

    def close(self, reading: str) -> ReasonId | None:
        """The close a reading leads to; None when it passes or flags."""
        return self.reasons[reading] if self.outcome(reading) in CLOSES else None

    def flags(self, reading: str) -> bool:
        """The reading is kept and the screening goes on, flagged for the recruiter."""
        return self.outcome(reading) == GateOutcome.FLAG


class Condition(Strict):
    """A predicate over an earlier field's value, exactly one operator. A field not answered
    (or left out) holds none: the condition is false."""

    gt: float | None = None
    eq: str | float | bool | None = None
    any_of: tuple[str, ...] | None = None
    none_of: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _one_operator(self) -> Self:
        if len(self.model_fields_set) != 1:
            raise ValueError("a condition has exactly one of gt, eq, any_of, none_of")
        return self

    @cached_property
    def operator(self) -> str:
        return next(iter(self.model_fields_set))

    def holds(self, value: object) -> bool:
        if value is None:
            return False
        match self.operator:
            case "gt":
                assert self.gt is not None
                return isinstance(value, int | float) and value > self.gt
            case "eq":
                return value == self.eq
        chosen = set(value) if isinstance(value, list | tuple) else {value}
        if self.any_of is not None:
            return bool(chosen & set(self.any_of))
        assert self.none_of is not None
        return not chosen & set(self.none_of)


class ReadBack(Strict):
    """When a value is read back before it is kept."""

    over: float | None = None
    """`years`: more than this."""
    after_days: int | None = None
    """`date`: further away than this many days."""
    loose_match: bool = False
    """`location`: a place that matched a location only loosely."""
    domain_typo: bool = False
    """`contact` by email: an address whose domain looks mistyped."""


READ_BACK_TYPES: Mapping[str, FieldType] = {
    "over": FieldType.YEARS,
    "after_days": FieldType.DATE,
    "loose_match": FieldType.LOCATION,
    "domain_typo": FieldType.CONTACT,
}


type Check = Callable[[CandidateProfile, float, Country, date], Platform | None]
"""A cross-field check of a `years` value: what makes it doubtful, read back with it."""
type Reader = Callable[[object], Result[Any]]
"""Reads a `multi_enum` answer into its options."""


class Hooks(Strict):
    """The domain's Python, by the name the spec gives it."""

    checks: Mapping[str, Check] = {}
    readers: Mapping[str, Reader] = {}


class FieldSpec(Strict):
    id: FieldId
    type: FieldType
    funnel_step: StepId
    """The analytics bucket it is asked at (`ConversationState.stage`)."""
    stored_as: tuple[str, ...] = ()
    """The profile attributes that hold it, when not the one named `id`: the value first, then
    what goes with it (a tuple value fills them in order) and is forgotten with it."""
    required: ReasonId | None = None
    """The close when the last try fails; an optional field is left out and flagged."""
    max_tries: int = 3
    gate: Gate | None = None
    ask_if: Mapping[FieldId, Condition] = {}
    read_back: ReadBack = ReadBack()
    checks: tuple[str, ...] = ()
    """Named `Hooks.checks` the value must pass, or it is read back."""

    channel: Channel | None = None
    """`contact`: which one."""
    options: tuple[str, ...] = ()
    """`enum`, `multi_enum`: the values, in order."""
    other: bool = False
    """`multi_enum`: an `other` option, with the words that named it kept as free text."""
    exclusive: tuple[str, ...] = ()
    """`multi_enum`: options picked only on their own."""
    buttons: Mapping[Country, tuple[str, ...]] = {}
    """`multi_enum`: the options shown as buttons in each country, when not all of them."""
    reader: str | None = None
    """`multi_enum`: the `Hooks.readers` entry that reads an answer into options; without one,
    the options are read by their names and `aliases`."""
    aliases: Mapping[str, tuple[str, ...]] = {}
    """`enum`, `multi_enum`: more words for an option than its id and labels, as regexes on
    folded text ("mates" for matematicas)."""
    min_confidence: float = 0.0
    """`credential`: a no the extractor is less sure of is asked again, never acted on."""
    voice_read_back: bool = False
    """`credential`: by voice, a no that closes is read back once before it does."""
    readings: Mapping[str, tuple[str, ...]] = {}
    """`credential`: the words that read as each reading but yes and no ("lo estoy cursando"
    as in_progress), as regexes on folded text; without them, a driving licence's."""
    zone_question_from: int | None = None
    """`location`: with this many locations in one city or more, ask which zone."""
    most: float | None = None
    """`years`: the cap."""
    late_after_days: int | None = None
    """`date`: further away than this is flagged as a late start."""

    extract: str = ""
    """How the extractor reads it: the field's line in the extraction prompt."""
    label: Mapping[Language, str] = {}
    """The field's name on the summary card and for the recruiter."""
    country_labels: Mapping[Country, Mapping[Language, str]] = {}
    """`label` where a country names it otherwise ("Licencia de conducir" in Mexico)."""
    option_labels: Mapping[Language, Mapping[str, str]] = {}
    none_label: Mapping[Language, str] = {}
    """`multi_enum`: an empty choice on the card ("Ninguna")."""

    @property
    def attrs(self) -> tuple[str, ...]:
        return self.stored_as or (self.id,)

    @property
    def attr(self) -> str:
        return self.attrs[0]

    @property
    def choices(self) -> tuple[str, ...]:
        return (*self.options, OTHER) if self.other else self.options

    @cached_property
    def words(self) -> Mapping[str, re.Pattern[str]]:
        """`enum`, `multi_enum`: each option by the words that name it, its id ("fisica
        quimica"), its labels and its aliases; `credential`: each of its `readings`, a driving
        licence's when it gives none."""
        if self.type == FieldType.CREDENTIAL and not self.readings:
            return LICENSE_WORDS
        if self.type == FieldType.CREDENTIAL:
            return {reading: _words(*said) for reading, said in self.readings.items()}
        named: dict[str, list[str]] = {choice: [re.escape(fold(choice))] for choice in self.choices}
        for labels in self.option_labels.values():
            for choice, label in labels.items():
                named[choice].append(re.escape(fold(label)))
        for choice, said in self.aliases.items():
            named[choice].extend(said)
        return {choice: _words(*dict.fromkeys(said)) for choice, said in named.items()}

    @property
    def companion(self) -> str | None:
        """The attribute kept with the value: the place as said for a `location`, the words
        of an "other" for a `multi_enum`, whether confirmed `years` failed a check."""
        return self.attrs[1] if len(self.attrs) > 1 else None

    def value(self, profile: CandidateProfile) -> Any:
        return getattr(profile, self.attr, None)

    def companion_value(self, profile: CandidateProfile) -> Any:
        return getattr(profile, self.companion, None) if self.companion else None

    @model_validator(mode="after")
    def _type_options(self) -> Self:
        """Each option belongs to a type: one set on another type is a mistake in the spec."""
        only: dict[str, tuple[FieldType, ...]] = {
            "channel": (FieldType.CONTACT,),
            "options": (FieldType.ENUM, FieldType.MULTI_ENUM),
            "other": (FieldType.MULTI_ENUM,),
            "exclusive": (FieldType.MULTI_ENUM,),
            "buttons": (FieldType.MULTI_ENUM,),
            "reader": (FieldType.MULTI_ENUM,),
            "aliases": (FieldType.ENUM, FieldType.MULTI_ENUM),
            "readings": (FieldType.CREDENTIAL,),
            "min_confidence": (FieldType.CREDENTIAL,),
            "voice_read_back": (FieldType.CREDENTIAL,),
            "zone_question_from": (FieldType.LOCATION,),
            "most": (FieldType.YEARS,),
            "late_after_days": (FieldType.DATE,),
            "checks": (FieldType.YEARS,),
        }
        for name in sorted(self.model_fields_set & only.keys()):
            if self.type not in only[name]:
                raise SpecError(f"{self.id}: `{name}` is not for a {self.type} field", name)
        for name in sorted(self.read_back.model_fields_set):
            if READ_BACK_TYPES[name] != self.type:
                raise SpecError(
                    f"{self.id}: read_back `{name}` is not for a {self.type} field",
                    *("read_back", name),
                )
        if (self.channel is None) == (self.type == FieldType.CONTACT):
            raise SpecError(f"{self.id}: a contact field, and only one, has a channel", "channel")
        if self.read_back.domain_typo and self.channel != Channel.EMAIL:
            raise SpecError(
                f"{self.id}: only an email has a domain to read back", "read_back", "domain_typo"
            )
        if self.type in (FieldType.ENUM, FieldType.MULTI_ENUM) and not self.options:
            raise SpecError(f"{self.id}: a {self.type} field lists its options", "options")
        offered = {("exclusive",): self.exclusive, ("aliases",): tuple(self.aliases)} | {
            ("buttons", country): shown for country, shown in self.buttons.items()
        }
        for path, chosen in offered.items():
            if set(chosen) - set(self.choices):
                raise SpecError(
                    f"{self.id}: {sorted(set(chosen) - set(self.choices))} are no options", *path
                )
        unread = sorted(set(self.readings) - (READINGS[FieldType.CREDENTIAL] - {"yes", "no"}))
        if self.type == FieldType.CREDENTIAL and unread:
            raise SpecError(f"{self.id}: no words for a reading {unread}", "readings", unread[0])
        if self.gate is not None:
            impossible = sorted(set(self.gate.readings) - READINGS.get(self.type, frozenset()))
            if impossible:
                raise SpecError(
                    f"{self.id}: a {self.type} is never read as {impossible}",
                    *("gate", "readings", impossible[0]),
                )
        return self


class InterviewSpec(Strict):
    """What the interview's times are fitted to: `blocks`, the parts of the day the candidate
    can work, and `before`, the day they can start. "Cambiar la hora" before any interview is
    about `blocks`. `scope`: who interviews whom, the location field (always matched) and the
    fields whose answers a recruiter is scoped on (`Recruiter.scope`): the head of department of
    the subject a teacher teaches."""

    blocks: FieldId | None = None
    before: FieldId | None = None
    scope: tuple[FieldId, ...] = ()


class Column(Strict):
    """A column of the recruiters' candidate list."""

    field: FieldId
    label: str | None = None
    """The header, when not the field's name on the dashboard."""
    sortable: bool = False
    """The list sorts by it (by the attribute that holds the value)."""
    wrap: bool = False
    """Long values wrap in a narrow column instead of widening it."""


class DashboardSpec(Strict):
    """The recruiters' dashboard, in their language: the list's columns and filters, and the
    names it gives fields and funnel steps where they are not the summary card's."""

    language: Language = Language.ES
    columns: tuple[Column, ...] = ()
    filters: tuple[FieldId, ...] = ()
    """Fields the list filters by one of their options."""
    labels: Mapping[FieldId, str] = {}
    country_labels: Mapping[FieldId, Mapping[Country, str]] = {}
    step_labels: Mapping[StepId, str] = {}
    """A funnel step's name, when not its first field's."""


QUALIFIED_STEP = "qualified"
"""The funnel's last step, after the read-back: the screening passed."""


@dataclass(frozen=True)
class Funnel:
    """The analytics funnel's steps: consent, the fields' `funnel_step`s in the order they are
    asked (phone and email are one step for riders), the read-back, qualified.

    `closes_at`: the step each close stops at, the step of the field whose gate or last try
    gives that reason (no contact at the contacts'). `firsts`: the first field asked at each
    field's step: a policy violation (prompt injection) closes at whatever step it happened,
    the first whose first field the profile does not have yet.
    """

    steps: tuple[str, ...]
    closes_at: Mapping[ReasonId, StepId]
    firsts: Mapping[StepId, FieldSpec]
    first_gate: StepId
    """Where a close for a reason no field gives stops: the first gate applied (the first
    field's step without one)."""


class AgentSpec(Strict):
    agent: str
    countries: tuple[Country, ...] = ("ES", "MX")
    """Where it hires: the conversation's country is one of them, and its texts come in their
    registers (`texts.registers`)."""
    locations: str | None = None
    """The file of the places it hires for (`service_areas.Places`)."""
    recruiters: str | None = None
    """The file of the team that interviews, for the demo and the evals (`sazon.recruiters`)."""
    fields: tuple[FieldSpec, ...]
    """In the order they are asked."""
    apply_order: tuple[FieldId, ...] = ()
    """Applied first when a message answers several, in this order (the gates: a close stores
    nothing after it); the rest in question order."""
    summary: tuple[FieldId, ...] = ()
    """The summary card's rows; every field when empty."""
    interview: InterviewSpec = InterviewSpec()
    fix_words: Mapping[FieldId, str] = {}
    """The words that name each field in "mi email está mal" (a regex on folded text), tried in
    this order: the first that matches is the field."""
    hooks: Hooks = Hooks()
    dashboard: DashboardSpec = DashboardSpec()
    texts: Mapping[Register, TextPack] = {}
    """What the agent says, by register (`engine.texts`); a spec without texts can't speak."""
    persona: Persona | None = None
    place_label: Mapping[Language, str] = {}
    """A place said and the location it is in, on the summary card ("{place} · sede de
    {location}"); the riders' "local" when not given."""
    interviewers: Mapping[str, tuple[str, ...]] = {}
    """Who runs the interview in each mode (`onsite`, `video`), as the replies must name them:
    regexes on folded text ("jefatura de estudios"); the riders' when not given."""
    prompt_slots: Mapping[str, str] = {}
    """The domain's text in the model prompts, by slot (`llm.prompting`)."""

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        ids = [field.id for field in self.fields]
        for position, id in enumerate(ids):
            if id in ids[:position]:
                raise SpecError(f"field ids repeat: {id}", "fields", position, "id")
        for path, ref in self._references():
            if ref not in ids:
                raise SpecError(f"{path[0]}: no field {ref}", *path)
        for position, field in enumerate(self.fields):
            _check_own_names(field, position)
            for index, name in enumerate(field.checks):
                if name not in self.hooks.checks:
                    raise SpecError(
                        f"{field.id}: no check hook `{name}`", "fields", position, "checks", index
                    )
            if field.reader is not None and field.reader not in self.hooks.readers:
                raise SpecError(
                    f"{field.id}: no reader hook `{field.reader}`", "fields", position, "reader"
                )
            for source, condition in field.ask_if.items():
                where = ("fields", position, "ask_if", source)
                _check_condition(field, source, condition, self.fields[:position], where)
            for country in set(field.buttons) - set(self.countries):
                raise SpecError(f"{field.id}: no country {country}", "fields", position, "buttons")
        for index, name in enumerate(self.dashboard.filters):
            if self.field(name).type not in (FieldType.ENUM, FieldType.MULTI_ENUM):
                raise SpecError(
                    f"dashboard: {name} has no options to filter by", "dashboard", "filters", index
                )
        if self.texts:
            check_texts(self, self.texts)
        return self

    def _references(self) -> Iterator[tuple[tuple[str | int, ...], FieldId]]:
        """Every field named outside `fields`, with where."""
        dashboard = self.dashboard
        listed = {
            ("apply_order",): self.apply_order,
            ("summary",): self.summary,
            ("dashboard", "filters"): dashboard.filters,
        }
        for path, refs in listed.items():
            yield from (((*path, index), ref) for index, ref in enumerate(refs))
        for name in ("blocks", "before"):
            ref = getattr(self.interview, name)
            if ref is not None:
                yield ("interview", name), ref
        for index, ref in enumerate(self.interview.scope):
            yield ("interview", "scope", index), ref
        for index, column in enumerate(dashboard.columns):
            yield ("dashboard", "columns", index, "field"), column.field
        named = {
            ("fix_words",): self.fix_words,
            ("dashboard", "labels"): dashboard.labels,
            ("dashboard", "country_labels"): dashboard.country_labels,
        }
        for path, keyed in named.items():
            yield from (((*path, ref), ref) for ref in keyed)

    @cached_property
    def by_id(self) -> Mapping[FieldId, FieldSpec]:
        return {field.id: field for field in self.fields}

    @cached_property
    def summary_fields(self) -> tuple[FieldId, ...]:
        return self.summary or self.ids

    @cached_property
    def interviewer_words(self) -> Mapping[str, re.Pattern[str]]:
        """`interviewers`, each mode's compiled once."""
        return {mode: _words(*words) for mode, words in self.interviewers.items() if words}

    @cached_property
    def by_attr(self) -> Mapping[str, FieldSpec]:
        """Each field by the profile attribute that holds its value."""
        return {field.attr: field for field in self.fields}

    def field(self, field: FieldId) -> FieldSpec:
        return self.by_id[field]

    @cached_property
    def ids(self) -> tuple[FieldId, ...]:
        return tuple(self.by_id)

    @cached_property
    def applied(self) -> tuple[FieldSpec, ...]:
        """Every field, in the order a message's answers are applied."""
        rest = [field for field in self.fields if field.id not in self.apply_order]
        return (*(self.field(field) for field in self.apply_order), *rest)

    @cached_property
    def contacts(self) -> tuple[FieldSpec, ...]:
        return self.of_type(FieldType.CONTACT)

    @cached_property
    def after_contacts(self) -> FieldSpec | None:
        """The field asked after the last contact: the duplicate check runs before it."""
        if not self.contacts:
            return None
        position = self.fields.index(self.contacts[-1]) + 1
        return self.fields[position] if position < len(self.fields) else None

    @cached_property
    def _by_type(self) -> Mapping[FieldType, tuple[FieldSpec, ...]]:
        return {kind: tuple(f for f in self.fields if f.type == kind) for kind in FieldType}

    def of_type(self, kind: FieldType) -> tuple[FieldSpec, ...]:
        return self._by_type[kind]

    def one(self, kind: FieldType) -> FieldSpec | None:
        """The field of a type an agent has at most one of (the location, the years)."""
        return next(iter(self.of_type(kind)), None)

    @cached_property
    def _dependents(self) -> Mapping[FieldId, tuple[tuple[FieldSpec, Condition], ...]]:
        return {
            field.id: tuple(
                (later, later.ask_if[field.id]) for later in self.fields if field.id in later.ask_if
            )
            for field in self.fields
        }

    def dependents(self, field: FieldId) -> tuple[tuple[FieldSpec, Condition], ...]:
        """The fields asked only on some answers to `field`, with their condition on it."""
        return self._dependents[field]

    @property
    def contact_step(self) -> StepId:
        """The funnel step of the duplicate check after a phone or email: the contacts'."""
        return self.contacts[0].funnel_step

    def channel(self, channel: Channel) -> FieldSpec | None:
        return next((field for field in self.contacts if field.channel == channel), None)

    @cached_property
    def _closing(self) -> Mapping[ReasonId, FieldSpec]:
        return {
            reason: field
            for field in self.fields
            if field.gate is not None
            for reason in field.gate.reasons.values()
        }

    @property
    def gate_reasons(self) -> frozenset[ReasonId]:
        """Every reason a gate closes with."""
        return frozenset(self._closing)

    def closing(self, reason: ReasonId | None) -> FieldSpec | None:
        """The gated field that closes with `reason`."""
        return self._closing.get(reason) if reason is not None else None

    def gate(self, reason: ReasonId | None) -> Gate | None:
        """The gate that closes with `reason`."""
        field = self.closing(reason)
        return field.gate if field is not None else None

    @cached_property
    def _talent_pool(self) -> frozenset[ReasonId]:
        return frozenset(
            gate.reasons[reading]
            for gate in (field.gate for field in self.fields if field.gate is not None)
            for reading, outcome in gate.readings.items()
            if outcome == GateOutcome.CLOSE_TALENT_POOL
        )

    @cached_property
    def quality_flags(self) -> tuple[QualityFlag, ...]:
        """The flags a conversation may raise: a gate's only when some gate keeps a reading for
        the recruiter instead of closing (`GateOutcome.FLAG`)."""
        gates = (field.gate for field in self.fields if field.gate is not None)
        flags = any(GateOutcome.FLAG in gate.readings.values() for gate in gates)
        return tuple(flag for flag in QualityFlag if flags or flag != QualityFlag.GATE_FLAG)

    def talent_pool(self, reason: ReasonId | None) -> bool:
        """`reason` closes at a gate that offers the talent pool."""
        return reason in self._talent_pool

    @cached_property
    def _unresolved(self) -> frozenset[ReasonId]:
        required = (field.required for field in self.fields if field.required is not None)
        return frozenset({DisqualifyReason.NO_CONTACT, *required})

    def unresolved(self, reason: ReasonId | None) -> bool:
        """`reason` closes for required data that could not be resolved: the candidate may
        start again at once."""
        return reason in self._unresolved

    @cached_property
    def funnel(self) -> Funnel:
        firsts: dict[StepId, FieldSpec] = {}
        for field in self.fields:
            firsts.setdefault(field.funnel_step, field)
        closes_at = {
            reason: field.funnel_step
            for field in self.fields
            for reason in (
                *(field.gate.reasons.values() if field.gate else ()),
                *((field.required,) if field.required else ()),
            )
        }
        if self.contacts:
            closes_at[DisqualifyReason.NO_CONTACT] = self.contacts[0].funnel_step
        gated = next((field for field in self.applied if field.gate is not None), self.fields[0])
        return Funnel(
            steps=(Stage.CONSENT, *firsts, Stage.CONFIRM, QUALIFIED_STEP),
            closes_at=closes_at,
            firsts=firsts,
            first_gate=gated.funnel_step,
        )

    @cached_property
    def scoped(self) -> tuple[FieldSpec, ...]:
        """The fields a recruiter may be scoped on beyond the location (`interview.scope`)."""
        fields = (self.field(id) for id in self.interview.scope)
        return tuple(field for field in fields if field.type != FieldType.LOCATION)

    def interview_scope(self, profile: CandidateProfile) -> dict[FieldId, list[str]]:
        """The candidate's answers a recruiter must share to interview them, by field."""
        scope: dict[FieldId, list[str]] = {}
        for field in self.scoped:
            value = field.value(profile)
            if value is not None:
                scope[field.id] = (
                    [str(v) for v in value] if isinstance(value, list) else [str(value)]
                )
        return scope

    def asked(self, field: FieldSpec, profile: CandidateProfile) -> bool:
        """Its conditions on earlier answers hold (`ask_if`): a field with none always is."""
        return all(
            condition.holds(self.field(source).value(profile))
            for source, condition in field.ask_if.items()
        )


ENGINE_STEPS = frozenset({*Stage, QUALIFIED_STEP})
ENGINE_REASONS = frozenset(DisqualifyReason)


def _check_own_names(field: FieldSpec, position: int) -> None:
    """A field's funnel step and closes are the agent's own: none is a name the engine has
    for its own steps (`Stage`) or closes (`DisqualifyReason`)."""
    where = ("fields", position)
    if field.funnel_step in ENGINE_STEPS:
        raise SpecError(
            f"{field.id}: {field.funnel_step} is the engine's step", *where, "funnel_step"
        )
    reasons: dict[tuple[str, ...], ReasonId] = {}
    if field.required is not None:
        reasons[("required",)] = field.required
    if field.gate is not None:
        reasons |= {("gate", "reasons", r): reason for r, reason in field.gate.reasons.items()}
    for path, reason in reasons.items():
        if reason in ENGINE_REASONS:
            raise SpecError(f"{field.id}: {reason} is the engine's close", *where, *path)


def _check_condition(
    field: FieldSpec,
    source: FieldId,
    condition: Condition,
    earlier: tuple[FieldSpec, ...],
    where: tuple[str | int, ...],
) -> None:
    """`ask_if` reads an earlier field, with an operator its type has: `gt` a number, `any_of`
    and `none_of` the options."""
    on = next((spec for spec in earlier if spec.id == source), None)
    if on is None:
        raise SpecError(f"{field.id}: ask_if on {source}, which is not asked before it", *where)
    operator = condition.operator
    if operator == "gt" and on.type != FieldType.YEARS:
        raise SpecError(f"{field.id}: `gt` on {source}, a {on.type}", *where, operator)
    if operator in ("any_of", "none_of"):
        if on.type not in (FieldType.ENUM, FieldType.MULTI_ENUM):
            raise SpecError(f"{field.id}: `{operator}` on {source}, a {on.type}", *where, operator)
        values: tuple[str, ...] = getattr(condition, operator)
        if set(values) - set(on.choices):
            raise SpecError(
                f"{field.id}: {sorted(set(values) - set(on.choices))} are no options",
                *where,
                operator,
            )


_current: AgentSpec | None = None


def agent() -> AgentSpec:
    """The spec the engine screens for: the one in use (`use`), else the selected agent's,
    loaded once."""
    global _current
    if _current is None:
        _current = importlib.import_module(AGENTS).selected()
    assert _current is not None
    return _current


@contextmanager
def use(spec: AgentSpec) -> Iterator[AgentSpec]:
    """Screen for `spec` within the block."""
    global _current
    before = _current
    _current = spec
    try:
        yield spec
    finally:
        _current = before
