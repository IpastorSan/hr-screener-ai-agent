"""Chat widgets for the web client. Mirrors the `Ui` union in web/src/lib/types.ts.

Options always come from enums and engine state, never from the model.
"""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from sazon.engine.models import CandidateProfile, FieldId, InterviewMode, Language
from sazon.engine.service_areas import branch_name, shared_city
from sazon.engine.slots import Booked, Slot
from sazon.engine.spec import OTHER, Channel, FieldType, agent
from sazon.engine.validators import Country

MAX_BUTTONS = 4


class Option(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: str
    label: str


class ConfirmField(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    value: str


class Link(BaseModel):
    """A page the widget links to, opened in a new tab (the privacy notice on the consent step)."""

    model_config = ConfigDict(frozen=True)

    href: str
    label: str


class QuickReplies(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["quick_replies"] = "quick_replies"
    options: tuple[Option, ...]
    link: Link | None = None


class MultiSelect(BaseModel):
    """Several options; an `exclusive` one (schedule `flexible`) is picked on its own."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["multi_select"] = "multi_select"
    options: tuple[Option, ...]
    min: int | None = None
    max: int | None = None
    exclusive: tuple[str, ...] = ()


class Slots(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["slots"] = "slots"
    slots: tuple[Slot, ...]


class Confirm(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["confirm"] = "confirm"
    fields: tuple[ConfirmField, ...]


class DialCountry(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: Country
    dial: str
    label: str


class PhoneEntry(BaseModel):
    """Phone number input with a country prefix picker."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["phone"] = "phone"
    countries: tuple[DialCountry, ...]
    selected: Country


type Ui = Annotated[
    QuickReplies | MultiSelect | Slots | Confirm | PhoneEntry | Booked, Field(discriminator="kind")
]

DIAL_COUNTRIES = (
    DialCountry(code="ES", dial="+34", label="ES +34"),
    DialCountry(code="MX", dial="+52", label="MX +52"),
)


YES_NO = {
    Language.ES: (Option(value="yes", label="Sí"), Option(value="no", label="No")),
    Language.EN: (Option(value="yes", label="Yes"), Option(value="no", label="No")),
}

# Served by the web client (web/src/app/privacidad, web/src/app/en/privacy).
PRIVACY_LINKS = {
    Language.ES: Link(href="/privacidad", label="Leer el aviso de privacidad"),
    Language.EN: Link(href="/en/privacy", label="Read the privacy notice"),
}

MODE_LABELS = {
    Language.ES: {InterviewMode.ONSITE: "En persona", InterviewMode.VIDEO: "Videollamada"},
    Language.EN: {InterviewMode.ONSITE: "In person", InterviewMode.VIDEO: "Video call"},
}
"""The interview-mode buttons; the web booking card names the mode the same way."""

CANCEL = "cancel"
RESCHEDULE = "reschedule"
CANCEL_OPTIONS = {
    Language.ES: (
        Option(value=CANCEL, label="Cancelar"),
        Option(value=RESCHEDULE, label="Otra hora"),
    ),
    Language.EN: (
        Option(value=CANCEL, label="Cancel"),
        Option(value=RESCHEDULE, label="Another time"),
    ),
}
"""The answers to "¿La cancelo o prefieres otra hora?"."""

YEARS = {Language.ES: ("año", "años"), Language.EN: ("year", "years")}
MONTHS_UNIT = {Language.ES: ("mes", "meses"), Language.EN: ("month", "months")}
NO_EXPERIENCE = {Language.ES: "Sin experiencia", Language.EN: "No experience"}
# What the candidate's bubble says after a tap on the confirm card (web widgets/copy.ts).
CONFIRM_ANSWERS = {
    Language.ES: (
        Option(value="yes", label="Sí, es correcto"),
        Option(value="no", label="Corregir algo"),
    ),
    Language.EN: (
        Option(value="yes", label="Yes, that's right"),
        Option(value="no", label="Change something"),
    ),
}
WEEKDAYS = {
    Language.ES: ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"),
    Language.EN: ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
MONTHS = {
    Language.ES: (
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ),
    Language.EN: (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}


def field_options(field: FieldId, country: Country) -> tuple[str, ...]:
    """Button values for a field, or () when it is typed."""
    spec = agent().field(field)
    match spec.type:
        case FieldType.CREDENTIAL | FieldType.YES_NO:
            return ("yes", "no")
        case FieldType.ENUM:
            return spec.options
        case FieldType.MULTI_ENUM:
            return spec.buttons.get(country, spec.choices)
        case _:
            return ()


def field_ui(
    field: FieldId, options: tuple[str, ...], language: Language, country: Country
) -> Ui | None:
    """The widget for a field, by its type: buttons for the options (several to pick for a
    `multi_enum`), none for more than `MAX_BUTTONS`, except the locations of one city ("DF":
    every Mexico City location), which the candidate picks from on the web. A phone with no
    options to pick from is typed into the phone widget."""
    spec = agent().field(field)
    if not options and spec.channel == Channel.PHONE:
        return phone_ui(country)
    one_city = spec.type == FieldType.LOCATION and shared_city(options) is not None
    if not options or (len(options) > MAX_BUTTONS and not one_city):
        return None
    buttons = tuple(
        Option(value=value, label=option_label(field, value, language)) for value in options
    )
    if spec.type == FieldType.MULTI_ENUM:
        return MultiSelect(options=buttons, min=1, exclusive=spec.exclusive)
    return QuickReplies(options=buttons)


def phone_ui(country: Country) -> PhoneEntry:
    hires = agent().countries
    return PhoneEntry(
        countries=tuple(c for c in DIAL_COUNTRIES if c.code in hires), selected=country
    )


def slots_ui(slots: tuple[Slot, ...]) -> Slots | None:
    return Slots(slots=slots) if slots else None


def mode_ui(options: tuple[InterviewMode, ...], language: Language) -> QuickReplies | None:
    """In person or video call, as buttons; nothing to choose with fewer than two."""
    if len(options) < 2:
        return None
    labels = MODE_LABELS[language]
    return QuickReplies(options=tuple(Option(value=mode, label=labels[mode]) for mode in options))


def mode_label(mode: InterviewMode, language: Language) -> str:
    return MODE_LABELS[language][mode]


def yes_no_ui(language: Language) -> QuickReplies:
    return QuickReplies(options=YES_NO[language])


def cancel_ui(language: Language) -> QuickReplies:
    return QuickReplies(options=CANCEL_OPTIONS[language])


def consent_ui(language: Language) -> QuickReplies:
    """Yes/no on the privacy notice, with a link to read it."""
    return QuickReplies(options=YES_NO[language], link=PRIVACY_LINKS[language])


def privacy_ui(language: Language) -> QuickReplies:
    """Only the link to the privacy notice: no options, the application is closed."""
    return QuickReplies(options=(), link=PRIVACY_LINKS[language])


def option_label(field: FieldId, value: str, language: Language) -> str:
    spec = agent().field(field)
    match spec.type:
        case FieldType.CREDENTIAL | FieldType.YES_NO if value in ("yes", "no"):
            return YES_NO[language][0].label if value == "yes" else YES_NO[language][1].label
        case FieldType.CREDENTIAL | FieldType.ENUM | FieldType.MULTI_ENUM:
            return spec.option_labels[language][value]
        case FieldType.LOCATION:
            return branch_name(value)
        case _:
            return value


def summary_ui(
    profile: CandidateProfile, skipped: tuple[FieldId, ...], language: Language, country: Country
) -> Confirm:
    rows: list[ConfirmField] = []
    for field in agent().summary_fields:
        if field in skipped:
            continue
        value = summary_value(profile, field, language)
        if value is None:
            continue
        rows.append(ConfirmField(label=field_label(field, language, country), value=value))
    return Confirm(fields=tuple(rows))


def field_label(field: FieldId, language: Language, country: Country) -> str:
    """The field's name for the candidate or recruiter, in the country's vocabulary."""
    spec = agent().field(field)
    return spec.country_labels.get(country, spec.label)[language]


def summary_value(profile: CandidateProfile, field: FieldId, language: Language) -> str | None:
    """The value as the summary card shows it, by the field's type; None when there is none."""
    spec = agent().field(field)
    value = spec.value(profile)
    if value is None:
        return None
    match spec.type:
        case FieldType.CREDENTIAL | FieldType.YES_NO:
            # A credential's reading its gate flags is kept as said ("in_progress").
            reading = value if isinstance(value, str) else "yes" if value else "no"
            return option_label(field, reading, language)
        case FieldType.ENUM:
            return option_label(field, value, language)
        case FieldType.LOCATION:
            return place_value(value, spec.companion_value(profile), language)
        case FieldType.MULTI_ENUM:
            return choices_value(profile, field, language)
        case FieldType.YEARS:
            return format_years(value, language)
        case FieldType.DATE:
            return format_date(value, language)
        case _:
            return value


PLACE_AND_LOCATION = {
    Language.ES: "{place} · local {location}",
    Language.EN: "{place} · {location} location",
}


def place_value(location_id: str, said: str | None, language: Language) -> str:
    """The place as the candidate said it and the location it is in, short: "Roma · local
    Cuauhtémoc", "Gràcia · local Barcelona Gràcia" (as the agent words it, `place_label`); the
    location alone when they named it."""
    location = branch_name(location_id)
    if not said:
        return location
    line = agent().place_label.get(language) or PLACE_AND_LOCATION[language]
    return line.format(place=said, location=location)


def format_years(years: float, language: Language) -> str:
    """Years and months, or only months under a year: "6 meses", "1 año y 6 meses", "2 años";
    "6 months", "1 year 6 months". Stored as a float; shown to the nearest month. The web's
    `formatExperience` (web/src/lib/experience.ts) writes the same."""
    total = round(years * 12)
    if total == 0:
        return NO_EXPERIENCE[language]
    whole, months = divmod(total, 12)
    parts: list[str] = []
    if whole:
        singular, plural = YEARS[language]
        parts.append(f"{whole} {singular if whole == 1 else plural}")
    if months:
        singular, plural = MONTHS_UNIT[language]
        parts.append(f"{months} {singular if months == 1 else plural}")
    joiner = " y " if language == Language.ES else " "
    return joiner.join(parts)


def format_date(day: date, language: Language, *, year: bool = False) -> str:
    """ "lunes 19 de octubre", "Monday 19 October", with the year when asked."""
    weekday = WEEKDAYS[language][day.weekday()]
    month = MONTHS[language][day.month - 1]
    if language == Language.ES:
        text = f"{weekday} {day.day} de {month}"
        return f"{text} de {day.year}" if year else text
    text = f"{weekday} {day.day} {month}"
    return f"{text} {day.year}" if year else text


def day_month(day: date, language: Language, *, year: bool = False) -> str:
    """ "21 de octubre", "21 October", with the year when asked: no weekday."""
    month = MONTHS[language][day.month - 1]
    if language == Language.ES:
        return f"{day.day} de {month} de {day.year}" if year else f"{day.day} de {month}"
    return f"{day.day} {month} {day.year}" if year else f"{day.day} {month}"


def choices_value(profile: CandidateProfile, field: FieldId, language: Language) -> str | None:
    """The options picked, by their labels, with the words of an "other" for its own; an empty
    choice by the field's word for none ("Ninguna")."""
    spec = agent().field(field)
    picked = spec.value(profile)
    if picked is None:
        return None
    labels = spec.option_labels[language]
    names = [labels[option] for option in picked if not (spec.other and option == OTHER)]
    other = spec.companion_value(profile) if spec.other else None
    if other:
        names.append(other)
    return ", ".join(names) or spec.none_label.get(language)
