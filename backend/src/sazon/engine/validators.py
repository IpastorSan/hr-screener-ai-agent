import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Literal, NamedTuple

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat, PhoneNumberType
from rapidfuzz import fuzz

from sazon.engine.regions import region_of, regions_named
from sazon.engine.service_areas import (
    Location,
    branch_name,
    get_location,
    load_locations,
    match_city,
    shared_city,
)
from sazon.engine.text import fold, opens_with_yes, strip_accents

CITY_VALID_SCORE = 85.0
# A weak match tied between the locations of one city is that city ("Madriz", "Mexico" in
# "Mexico City") from this score, or when the answer is a word of its name; below it, a
# neighbouring town with a similar name ("Badalona" for Barcelona) that we don't serve.
CITY_TIE_SCORE = 80.0
CITY_MARGIN = 10.0
MAX_EXPERIENCE_YEARS = 20
LATE_START_DAYS = 60
MAX_START_DAYS = 90
MAX_NAME_LENGTH = 100
LICENSE_IN_PROGRESS = "in_progress"
LICENSE_NOT_VALID = "not_valid"
OTHER = "other"
"""The option of a `multi_enum` with `other`: the words that named it are kept as they are."""


class ReasonCode(StrEnum):
    NAME_TOO_SHORT = "name_too_short"
    NAME_NEEDS_SURNAME = "name_needs_surname"
    NAME_TOO_LONG = "name_too_long"
    NAME_HAS_DIGITS = "name_has_digits"
    NAME_INVALID_CHARS = "name_invalid_chars"
    PHONE_INVALID = "phone_invalid"
    PHONE_NOT_MOBILE = "phone_not_mobile"
    EMAIL_INVALID = "email_invalid"
    LICENSE_INVALID = "license_invalid"
    LICENSE_IN_PROGRESS = "license_in_progress"
    LICENSE_NOT_VALID = "license_not_valid"
    OUT_OF_AREA = "out_of_area"
    CITY_AMBIGUOUS = "city_ambiguous"
    AVAILABILITY_INVALID = "availability_invalid"
    SCHEDULE_INVALID = "schedule_invalid"
    EXPERIENCE_INVALID = "experience_invalid"
    EXPERIENCE_OUT_OF_RANGE = "experience_out_of_range"
    EXPERIENCE_NEEDS_NUMBER = "experience_needs_number"
    PLATFORMS_INVALID = "platforms_invalid"
    DATE_INVALID = "date_invalid"
    PAST_DATE = "past_date"
    LATE_START = "late_start"
    ANSWER_INVALID = "answer_invalid"
    """A yes or no, an option or a text the answer did not give."""
    DECLINED = "declined"
    """The candidate would rather not give the value asked for: why it is asked, then again."""
    DECLINED_AGAIN = "declined_again"
    """Refused once more: what happens without it, then the question again."""


REFUSALS = frozenset({ReasonCode.DECLINED, ReasonCode.DECLINED_AGAIN})


@dataclass(frozen=True)
class Valid[T]:
    value: T
    note: ReasonCode | None = None
    needs_confirmation: bool = False


@dataclass(frozen=True)
class Invalid:
    reason: ReasonCode


@dataclass(frozen=True)
class Ambiguous[T]:
    options: tuple[T, ...]
    reason: ReasonCode


type Result[T] = Valid[T] | Invalid | Ambiguous[T]

Country = Literal["ES", "MX"]


class StartDate(NamedTuple):
    start_date: date
    late_start_days: int | None


def _find[K](patterns: Mapping[K, re.Pattern[str]], text: str) -> list[K]:
    return [key for key, pattern in patterns.items() if pattern.search(text)]


def _words(*phrases: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(phrases) + r")\b")


# Clauses are read on their own: the "no" of "No, está vencida" answers the question and must
# not negate the problem in the next clause.
_CLAUSE = re.compile(r"[,.;:!?¡¿()\n]+|\b(?:pero|but)\b")


# --- name ---

_NAME_ALLOWED = re.compile(r"^[^\W\d_]+(?:[ '\u2019.\-][^\W\d_]*)*$")

# Surname abbreviations common in Spain and Mexico, expanded whole-token with or without the
# dot. Conservative: "Sanz", "Garza" and "Gil" are surnames of their own and stay as written.
SURNAME_ABBREVIATIONS = {
    "hdz": "Hernández",
    "hdez": "Hernández",
    "fdz": "Fernández",
    "fdez": "Fernández",
    "glz": "González",
    "glez": "González",
    "gzlz": "González",
    "gonz": "González",
    "rdz": "Rodríguez",
    "rguez": "Rodríguez",
    "mtz": "Martínez",
    "mtnez": "Martínez",
    "gtz": "Gutiérrez",
    "gtrrz": "Gutiérrez",
    "rmz": "Ramírez",
    "lpz": "López",
    "pz": "Pérez",
    "sz": "Sánchez",
    "dz": "Díaz",
    "gcia": "García",
    "gca": "García",
}
# Compound first names common in Spain and Mexico: two words that read like a full name but
# are only the first name ("Juan Carlos", "María José"). The whole answer being one of them,
# compared folded (any case, accents optional), asks for the surname. Forms with "del" and
# "de los" are listed as said; names with a surname after them stay valid.
COMPOUND_FIRST_NAMES = frozenset(
    {
        "ana belen",
        "ana isabel",
        "ana lucia",
        "ana maria",
        "ana paula",
        "ana sofia",
        "francisco javier",
        "francisco jose",
        "jesus maria",
        "jose angel",
        "jose antonio",
        "jose carlos",
        "jose francisco",
        "jose ignacio",
        "jose juan",
        "jose luis",
        "jose manuel",
        "jose maria",
        "jose miguel",
        "jose ramon",
        "juan antonio",
        "juan carlos",
        "juan francisco",
        "juan ignacio",
        "juan jose",
        "juan luis",
        "juan manuel",
        "juan miguel",
        "juan pablo",
        "juan ramon",
        "luis alberto",
        "luis angel",
        "luis fernando",
        "luis miguel",
        "luz maria",
        "maria angeles",
        "maria de los angeles",
        "maria belen",
        "maria carmen",
        "maria del carmen",
        "maria cristina",
        "maria dolores",
        "maria elena",
        "maria eugenia",
        "maria fernanda",
        "maria guadalupe",
        "maria isabel",
        "maria jesus",
        "maria jose",
        "maria luisa",
        "maria del mar",
        "maria mercedes",
        "maria pilar",
        "maria del pilar",
        "maria del rocio",
        "maria rosa",
        "maria del rosario",
        "maria soledad",
        "maria teresa",
        "miguel angel",
        "pedro pablo",
        "rosa maria",
    }
)
_NAME_TOKEN = re.compile(r"(?<![^\s])([^\W\d_]+)\.?(?![^\s])")


def expand_surnames(name: str) -> str:
    """ "juan carlos hdz" -> "juan carlos Hernández"; "Mtz." -> "Martínez". Only a whole word
    that is a known abbreviation (any case, optional trailing dot) changes."""

    def expand(match: re.Match[str]) -> str:
        return SURNAME_ABBREVIATIONS.get(strip_accents(match.group(1)).lower(), match.group(0))

    return _NAME_TOKEN.sub(expand, name)


def validate_person_name(raw: object) -> Result[str]:
    """A first name and at least one surname: two words of two letters or more ("Carlos
    Sánchez"). A lone first name, a compound one alone ("Juan Carlos") or one with only an
    initial ("Carlos S."), needs the surname."""
    if not isinstance(raw, str):
        return Invalid(ReasonCode.NAME_TOO_SHORT)
    name = " ".join(raw.split())
    if any(c.isdigit() for c in name):
        return Invalid(ReasonCode.NAME_HAS_DIGITS)
    if sum(c.isalpha() for c in name) < 2:
        return Invalid(ReasonCode.NAME_TOO_SHORT)
    if len(name) > MAX_NAME_LENGTH:
        return Invalid(ReasonCode.NAME_TOO_LONG)
    if not _NAME_ALLOWED.match(name):
        return Invalid(ReasonCode.NAME_INVALID_CHARS)
    if sum(sum(c.isalpha() for c in word) >= 2 for word in name.split()) < 2:
        return Invalid(ReasonCode.NAME_NEEDS_SURNAME)
    if fold(name) in COMPOUND_FIRST_NAMES:
        return Invalid(ReasonCode.NAME_NEEDS_SURNAME)
    return Valid(expand_surnames(name))


# --- contact ---

_MOBILE_TYPES = {PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE}


def validate_phone(raw: object, *, country: Country = "ES", fallback: bool = True) -> Result[str]:
    """Normalize a mobile number to E.164.

    Without a country code, the number is read as `country` first and then, if `fallback`,
    as the other country: Spanish mobiles have 9 digits and Mexican numbers 10, so they
    never collide.
    """
    if not isinstance(raw, str | int) or isinstance(raw, bool):
        return Invalid(ReasonCode.PHONE_INVALID)
    as_given = _parse_mobile(str(raw), country)
    if isinstance(as_given, Valid) or not fallback:
        return as_given
    as_other = _parse_mobile(str(raw), _other_country(country))
    if isinstance(as_other, Valid):
        return as_other
    return as_given


def phone_country(e164: str) -> Country | None:
    if e164.startswith("+34"):
        return "ES"
    if e164.startswith("+52"):
        return "MX"
    return None


def _other_country(country: Country) -> Country:
    return "MX" if country == "ES" else "ES"


def _parse_mobile(raw: str, region: Country) -> Valid[str] | Invalid:
    try:
        number = phonenumbers.parse(raw, region)
    except NumberParseException:
        return Invalid(ReasonCode.PHONE_INVALID)
    national = str(number.national_number)
    if number.country_code == 52 and len(national) == 11 and national.startswith("1"):
        number.national_number = int(national[1:])
    if not phonenumbers.is_valid_number(number):
        return Invalid(ReasonCode.PHONE_INVALID)
    if phonenumbers.number_type(number) not in _MOBILE_TYPES:
        return Invalid(ReasonCode.PHONE_NOT_MOBILE)
    return Valid(phonenumbers.format_number(number, PhoneNumberFormat.E164))


_EMAIL = re.compile(
    r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


# Mistyped domains of the common providers, and of ".com" (D2): the address is read back as
# corrected ("¿Quisiste decir lucia@gmail.com?") before it is kept.
_PROVIDER_TYPOS: dict[str, str] = {
    **dict.fromkeys(
        ("gmial", "gmal", "gmaill", "gamil", "gnail", "gmai", "gmali", "gmil"), "gmail"
    ),
    **dict.fromkeys(
        ("hotmial", "hotmal", "hotmai", "hotmil", "homail", "hotamil", "hotmaill", "hotmali"),
        "hotmail",
    ),
    **dict.fromkeys(("outlok", "outllok", "outloo", "otlook", "outlookk", "oulook"), "outlook"),
    **dict.fromkeys(("yaho", "yahooo", "yhaoo", "yhoo"), "yahoo"),
    **dict.fromkeys(("iclod", "icoud", "iclud", "icloude"), "icloud"),
}
_TLD_TYPOS: dict[str, str] = dict.fromkeys(
    ("con", "cmo", "comm", "vom", "xom", "cpm", "ocm", "cim"), "com"
)
_PROVIDERS = frozenset(_PROVIDER_TYPOS.values())


def correct_email_domain(email: str) -> str:
    """The address with a mistyped provider or ".com" fixed: "lucia@gmail.con" ->
    "lucia@gmail.com", "x@hotmial.es" -> "x@hotmail.es". Unchanged when nothing looks
    mistyped."""
    local, _, domain = email.rpartition("@")
    labels = domain.split(".")
    labels[0] = _PROVIDER_TYPOS.get(labels[0], labels[0])
    labels = [labels[0], *(_TLD_TYPOS.get(label, label) for label in labels[1:])]
    if labels[0] in _PROVIDERS and labels[1:] == ["co"]:
        labels[1] = "com"
    return f"{local}@{'.'.join(labels)}"


def validate_email(raw: object) -> Result[str]:
    """Syntax, lowercased. A domain that looks mistyped ("gmail.con", "hotmial.com") is Valid
    as corrected, with `needs_confirmation`: the engine asks once whether they meant it."""
    if not isinstance(raw, str):
        return Invalid(ReasonCode.EMAIL_INVALID)
    email = raw.strip().lower()
    if len(email) > 254 or not _EMAIL.match(email):
        return Invalid(ReasonCode.EMAIL_INVALID)
    corrected = correct_email_domain(email)
    if corrected != email:
        return Valid(corrected, needs_confirmation=True)
    return Valid(email)


# --- license ---

_LICENSE_IN_PROGRESS = _words(
    "in progress",
    "en tramite",
    "tramitandol[oa]",
    "tramitando",
    r"sacando\w*",
    "autoescuela",
    "getting it",
    "working on it",
    "permiso de aprendiz",
    "licencia de aprendiz",
    "permiso provisional",
    "learner s permit",
    "learners permit",
    "learner permit",
    "learner s licen[cs]e",
    "provisional licen[cs]e",
    "provisional",
)
# Held once but not valid now. Matched outside a negation ("no está vencida" is valid).
_LICENSE_NOT_VALID = _words(
    r"vencid[oa]s?",
    r"caducad[oa]s?",
    "expired",
    "expirad[oa]",
    r"renovando\w*",
    "en renovacion",
    "being renewed",
    "renewing",
    "renewal",
    r"suspendid[oa]",
    "suspended",
    "revoked",
    "retirad[oa]",
    r"me (?:l[oa]|el carnet|la licencia) (?:quitaron|retiraron|suspendieron)",
    r"me quitaron (?:el carnet|la licencia|los puntos|todos los puntos)",
    "sin puntos",
    "took (?:it|my licen[cs]e) away",
    "lost my licen[cs]e",
)
_LICENSE_NEGATED = re.compile(r"\b(?:no|not|isn t|is not|nunca|never|sin)\s+(?:\w+\s+){0,2}$")
_LICENSE_UNSURE = _words(
    "no se",
    "no estoy segur[oa]",
    "ni idea",
    "creo que",
    "quizas",
    "tal vez",
    "not sure",
    "don t know",
    "dont know",
    "no idea",
    "i think",
    "maybe",
)
_LICENSE_NO = re.compile(
    r"^(?:no|nope|nah|false)\b"
    r"|\b(?:no tengo|todavia no|aun no|not yet|sin (?:carnet|licencia)"
    r"|don t have|do not have|dont have|i don t)\b"
)
_LICENSE_YES = re.compile(
    r"^(?:si|yes|yeah|yep|yup|true|claro|sure|of course|por supuesto)\b"
    r"|\b(?:tengo|i have|i do|(?:tipo|clase|class|type) \w+|carnet|licencia|permiso|licen[cs]e)\b"
)


type ReadingWords = Mapping[str, re.Pattern[str]]
"""A credential's words for its readings but yes and no (`spec.FieldSpec.words`)."""

LICENSE_WORDS: ReadingWords = {
    LICENSE_IN_PROGRESS: _LICENSE_IN_PROGRESS,
    LICENSE_NOT_VALID: _LICENSE_NOT_VALID,
}
"""A driving licence's: the words of a credential whose spec gives none."""
_NOTHING = re.compile(r"(?!)")


def license_reading(text: str, words: ReadingWords = LICENSE_WORDS) -> ReasonCode | None:
    """A credential that does not count, read from the candidate's own words: still getting it
    (`LICENSE_IN_PROGRESS`; for a licence, a learner's permit too), or held once but not valid
    now (`LICENSE_NOT_VALID`; expired, being renewed, suspended or taken away), by the
    credential's `words`. None when the words say neither."""
    in_progress = words.get(LICENSE_IN_PROGRESS, _NOTHING)
    not_valid = words.get(LICENSE_NOT_VALID, _NOTHING)
    for clause in map(fold, _CLAUSE.split(text.casefold())):
        if in_progress.search(clause):
            return ReasonCode.LICENSE_IN_PROGRESS
        for match in not_valid.finditer(clause):
            if not _LICENSE_NEGATED.search(clause[: match.start()]):
                return ReasonCode.LICENSE_NOT_VALID
    return None


# A typed answer to the license question that opens with a yes or a no ("Sí", "No", "sí, tengo
# el B"): read without the model, as the buttons are.
_LICENSE_YES_NO_START = re.compile(
    r"^(?:si|yes|yeah|yep|yup|claro|por supuesto|of course|no|nope|nah)\b"
)


_LICENSE_NOUN = r"(?:licen[cs]e|licencia|carnet|permiso de conducir)"
# A license claimed outright, in Spanish, English or both at once ("tengo driver license
# vigente", "I have a license", "yes license"): enough to overrule a no from the extractor.
_LICENSE_AFFIRMED = re.compile(
    r"^(?:si|yes|yeah|yep|yup|claro|por supuesto|of course)\b"
    r"|\b(?:tengo|i have|i ve got|i got|cuento con)(?:\s+\w+){0,3}?\s+" + _LICENSE_NOUN + r"\b"
    r"|\b" + _LICENSE_NOUN + r"(?:\s+\w+){0,4}?\s+(?:vigente|valid[ao]?|al dia|current)\b"
)
# Any negation leaves the overruling to the extractor: "no está vencida" read as no stays a no
# (owner decision, QA sweep 2026-10-21), and so does "sí, pero no la tengo aquí".
_ANY_NEGATION = re.compile(r"\b(?:no|not|nunca|never|sin|nel|ni|isn t|don t|dont|ya no)\b")


def license_affirmed(text: str, words: ReadingWords = LICENSE_WORDS) -> bool:
    """The candidate's own words clearly say they hold a valid license: a yes or a license
    claimed with no negation, hedge or problem anywhere in them."""
    folded = fold(text)
    return (
        bool(_LICENSE_AFFIRMED.search(folded))
        and not _ANY_NEGATION.search(folded)
        and not _LICENSE_UNSURE.search(folded)
        and license_reading(text, words) is None
    )


def license_unsure(text: str) -> bool:
    """The candidate's own words hedge ("creo que no", "no estoy segura"): never acted on."""
    return bool(_LICENSE_UNSURE.search(fold(text)))


def reads_as_license_answer(text: str, words: ReadingWords = LICENSE_WORDS) -> bool:
    """Words that open with a yes or a no and that the validator reads for sure, so they can
    be stored as written when the extractor reported no license answer."""
    return bool(_LICENSE_YES_NO_START.search(fold(text))) and isinstance(
        validate_credential(text, words), Valid
    )


def validate_credential(raw: object, words: ReadingWords = LICENSE_WORDS) -> Result[bool]:
    """A yes or no, also from the candidate's own words as a fallback for the extractor.

    Only a credential valid when applying counts: still getting it, or held once and expired,
    being renewed or suspended, are a no, with the reason as the note (by the credential's
    `words`, a driving licence's by default). Anything uncertain is asked again rather than
    guessed.
    """
    if isinstance(raw, bool):
        return Valid(raw)
    if not isinstance(raw, str):
        return Invalid(ReasonCode.LICENSE_INVALID)
    if raw.strip() == LICENSE_IN_PROGRESS:
        return Valid(False, note=ReasonCode.LICENSE_IN_PROGRESS)
    if raw.strip() == LICENSE_NOT_VALID:
        return Valid(False, note=ReasonCode.LICENSE_NOT_VALID)
    reading = license_reading(raw, words)
    if reading is not None:
        return Valid(False, note=reading)
    text = fold(raw)
    if _LICENSE_UNSURE.search(text):
        return Invalid(ReasonCode.LICENSE_INVALID)
    rest, denied = _without_denied_problems(text, words.get(LICENSE_NOT_VALID, _NOTHING))
    if _LICENSE_NO.search(rest):
        return Valid(False)
    if denied or _LICENSE_YES.search(rest):
        return Valid(True)  # "no está vencida": a license that isn't expired is held
    return Invalid(ReasonCode.LICENSE_INVALID)


def _without_denied_problems(folded: str, not_valid: re.Pattern[str]) -> tuple[str, bool]:
    """The words with each denied problem ("no está vencida", "not expired") taken out, so its
    "no" doesn't read as having no license, and whether there was one."""
    kept: list[str] = []
    start = 0
    for match in not_valid.finditer(folded):
        negation = _LICENSE_NEGATED.search(folded[: match.start()])
        if negation is not None and negation.start() >= start:
            kept.append(folded[start : negation.start()])
            start = match.end()
    kept.append(folded[start:])
    return "".join(kept), start > 0


# --- yes or no, options, free text ---

_NO = re.compile(r"^(?:no|nope|nah|false)\b")
ITEM_SEPARATORS = re.compile(r"\s*(?:,|;|/|&|\+|\by\b|\band\b|\be\b)\s*", re.IGNORECASE)
"""Between the items of a list said in one breath: "Glovo y Uber", "ESO, Bachillerato"."""
MAX_FREE_TEXT = 500


def validate_yes_no(raw: object) -> Result[bool]:
    """A yes or a no: the button's, or words that open with one and hedge nothing ("si" only
    on its own: "si me pagan bien" is no yes)."""
    if isinstance(raw, bool):
        return Valid(raw)
    if not isinstance(raw, str) or _LICENSE_UNSURE.search(fold(raw)):
        return Invalid(ReasonCode.ANSWER_INVALID)
    if opens_with_yes(raw):
        return Valid(True)
    if _NO.search(fold(raw)):
        return Valid(False)
    return Invalid(ReasonCode.ANSWER_INVALID)


def validate_free_text(raw: object, *, most: int = MAX_FREE_TEXT) -> Result[str]:
    """The answer as said, trimmed: not empty, at most `most` characters."""
    if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > most:
        return Invalid(ReasonCode.ANSWER_INVALID)
    return Valid(" ".join(raw.split()))


def _named(item: str, words: Mapping[str, re.Pattern[str]]) -> list[str]:
    """The options an answer names: its id as the button sends it, else by its words."""
    if item in words:
        return [item]
    return _find(words, fold(item))


def validate_choice(raw: object, words: Mapping[str, re.Pattern[str]]) -> Result[str]:
    """One option, by the words that name each (`spec.FieldSpec.words`); which one, when the
    answer names several."""
    if not isinstance(raw, str):
        return Invalid(ReasonCode.ANSWER_INVALID)
    named = _named(raw.strip(), words)
    if len(named) > 1:
        return Ambiguous(tuple(named), ReasonCode.ANSWER_INVALID)
    return Valid(named[0]) if named else Invalid(ReasonCode.ANSWER_INVALID)


def validate_choices(
    raw: object,
    words: Mapping[str, re.Pattern[str]],
    *,
    other: bool = False,
    exclusive: tuple[str, ...] = (),
) -> Result[Any]:
    """Several options, in their order, by the words that name each. With `other`, the parts
    that name none are its words, and the value is the options with them: of a list, every
    such item ("Robótica" beside "matematicas"); of a sentence, only when it names no option
    at all ("Robótica"), since its other parts are mostly filler ("mates, sobre todo"). An
    exclusive option stands alone."""
    if isinstance(raw, str):
        items, listed = ITEM_SEPARATORS.split(raw), False
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        items, listed = [str(item) for item in raw], True
    else:
        return Invalid(ReasonCode.ANSWER_INVALID)
    chosen: set[str] = set()
    unnamed: list[str] = []
    for item in (item.strip() for item in items if item.strip()):
        named = _named(item, words)
        chosen.update(named)
        if not named:
            unnamed.append(item)
    said = unnamed if listed or not chosen else []
    if other and said:
        chosen.add(OTHER)
    picked = [option for option in words if option in chosen]
    alone = next((option for option in exclusive if option in chosen), None)
    if alone is not None:
        picked = [alone]
    if not picked:
        return Invalid(ReasonCode.ANSWER_INVALID)
    if not other:
        return Valid(picked)
    return Valid((picked, ", ".join(said) if OTHER in picked and said else None))


# --- city ---


# Words a candidate (or speech to text) puts before a zone. Only dropped when reading the
# parts; a part that names only the country ("México") matches nothing strongly and is ignored.
PLACE_FILLER = re.compile(
    r"^(?:(?:en|in|el|la|mmm*|mm|eh|pues|bueno|alcaldia|delegacion|colonia|barrio|distrito"
    r"|municipio|zona|entre|between|vivo|vive|vivimos|resido|radico|i live|live)\s+)+"
)
PLACE_SEPARATORS = re.compile(r"[,;()/]|\s+-\s+")
# "Narvarte y Polanco", "Gràcia o Sants": two places joined by a word, as the extractor gives
# them as often as with a comma (2026-10-22). On folded parts that are not one region's name
# ("Castilla y León").
PLACE_CONJUNCTIONS = re.compile(r"\s+(?:y|e|o|u|and|or)\s+")


def validate_location(raw: object, *, country: Country | None = None) -> Result[str]:
    """Match a city, zone or alias to a location_id.

    One location clearly ahead is Valid. A city with several locations ("DF", "Madrid") is
    Ambiguous with every location in it; so is a weak match tied between several. A single
    weak match ("Cancum", but also "Santander" for Álvaro Obregón) is Valid with
    `needs_confirmation`: the engine asks once whether they mean that location, never a
    one-option clarification (SAZ-42).

    When the whole answer is not one location, its parts are read on their own, without the
    filler words: "México, DF, Benito Juárez" is Benito Juárez, and "México, DF"
    is the city of Mexico City.

    A part that names a state, community or province the location is not in ("Guadalupe,
    Zacatecas" against Guadalupe, Nuevo León) makes it a place outside the service areas.
    """
    if not isinstance(raw, str):
        return Invalid(ReasonCode.OUT_OF_AREA)
    result = _validate_place(raw, country)
    if isinstance(result, Invalid):
        return result
    options = (result.value,) if isinstance(result, Valid) else result.options
    kept = tuple(option for option in options if _in_regions_named(raw, option, country))
    if kept == options:
        return result
    if not kept:
        return Invalid(ReasonCode.OUT_OF_AREA)
    if len(kept) == 1:
        return Valid(kept[0])
    return Ambiguous(kept, ReasonCode.CITY_AMBIGUOUS)


def place_said(raw: object, location_id: str) -> str | None:
    """The zone or colonia of `location_id` the candidate named ("Vivo en Roma, en Ciudad de
    México" -> "Roma"), spelled as the service areas spell it, so the summary shows the place
    as they said it next to the location. None when they named only the location itself
    ("Cuauhtémoc"), its city ("DF"), or a button's location id."""
    location = get_location(location_id)
    if location is None or not isinstance(raw, str):
        return None
    own = {fold(location.city), fold(branch_name(location_id)), fold(location.name)}
    shared = _city_terms(location)
    terms: dict[str, str] = {}
    for zone in location.zones:
        terms.setdefault(fold(zone), zone)
    for alias in location.aliases:
        terms.setdefault(fold(alias), alias)
    terms = {key: name for key, name in terms.items() if key not in own and key not in shared}
    best: tuple[float, str] | None = None
    for part in {PLACE_FILLER.sub("", fold(raw)), *_place_parts(raw)}:
        for key, name in terms.items():
            score = fuzz.ratio(part, key)
            if score >= CITY_VALID_SCORE and (best is None or score > best[0]):
                best = (score, name)
    return best[1] if best else None


def _city_terms(location: Location) -> set[str]:
    """Aliases the other locations of the same city share ("CDMX", "DF"): the city, not a zone."""
    terms: set[str] = set()
    for other in load_locations().values():
        if other.location_id != location.location_id and other.city == location.city:
            terms.update(fold(alias) for alias in other.aliases)
    return terms & {fold(alias) for alias in location.aliases}


def _in_regions_named(raw: str, location_id: str, country: Country | None) -> bool:
    """No part of the answer names a region `location_id` is not in, unless that part is the
    name of a location too, or is said to be a colonia ("Colonia Guerrero" is no state)."""
    region = region_of(location_id)
    folded = fold(raw)
    for part in _place_parts(raw):
        named = regions_named(part)
        if not named or region in named or _names_a_location(part, country):
            continue
        if not re.search(rf"\b(?:colonia|col|barrio){re.escape(' ' + part)}\b", folded):
            return False
    return True


def _names_a_location(part: str, country: Country | None) -> bool:
    """ "León" is a city of ours as well as a Spanish province; "Galicia" is only like Gràcia."""
    matches = match_city(part, country)
    return bool(matches) and matches[0][1] >= CITY_VALID_SCORE


def _validate_place(raw: str, country: Country | None) -> Result[str]:
    whole, score = _match_place(raw, country)
    if isinstance(whole, Valid) and not whole.needs_confirmation:
        return whole
    parts = [_match_place(part, country, strong_only=True) for part in _place_parts(raw)]
    found = [(result, part_score) for result, part_score in parts if part_score]
    named = {result.value for result, _ in found if isinstance(result, Valid)}
    areas = [set(result.options) for result, _ in found if isinstance(result, Ambiguous)]
    if len(named) == 1 and all(named <= area for area in areas):
        return Valid(named.pop())
    if named:
        # Parts that disagree ("Narvarte, Polanco", "DF, Gràcia"): which of them.
        options = named.union(*areas)
        return Ambiguous(tuple(sorted(options)), ReasonCode.CITY_AMBIGUOUS)
    best = max([(whole, score), *found], key=_narrowest)
    return best[0]


def _narrowest(item: tuple[Result[str], float]) -> tuple[float, int]:
    result, score = item
    return score, -len(result.options) if isinstance(result, Ambiguous) else 0


def _place_parts(raw: str) -> list[str]:
    pieces = [fold(piece) for piece in PLACE_SEPARATORS.split(raw)]
    pieces = [
        word_part
        for piece in pieces
        for word_part in ([piece] if regions_named(piece) else PLACE_CONJUNCTIONS.split(piece))
    ]
    parts = [PLACE_FILLER.sub("", piece) for piece in pieces]
    return [part for part in parts if part]


def _match_place(
    text: str, country: Country | None, *, strong_only: bool = False
) -> tuple[Result[str], float]:
    """The result for one place and its best score (0 with no match)."""
    matches = match_city(text, country)
    if not matches:
        return Invalid(ReasonCode.OUT_OF_AREA), 0.0
    top = matches[0][1]
    if top < CITY_VALID_SCORE and strong_only:
        return Invalid(ReasonCode.OUT_OF_AREA), 0.0
    floor = max(CITY_VALID_SCORE, top - CITY_MARGIN) if top >= CITY_VALID_SCORE else top
    close = [loc.location_id for loc, score in matches if score >= floor]
    if len(close) > 1 and top < CITY_VALID_SCORE and _other_town(text, close, top):
        return Invalid(ReasonCode.OUT_OF_AREA), 0.0
    if len(close) > 1:
        return Ambiguous(tuple(close), ReasonCode.CITY_AMBIGUOUS), top
    return Valid(close[0], needs_confirmation=top < CITY_VALID_SCORE), top


def _other_town(text: str, tied: list[str], score: float) -> bool:
    """A weak match tied between every location of one city is a town of its own with a
    similar name ("Badalona" for Barcelona), read back as outside the areas rather than asked
    which part of the city (QA sweep 2026-10-21); unless it scores close to the city's name or
    every word of it is a word of the city's names ("México DF")."""
    city = shared_city(tied)
    if city is None or score >= CITY_TIE_SCORE:
        return False
    names = {fold(city), *(fold(alias) for alias in _city_aliases(tied))}
    known = {word for name in names for word in name.split()}
    return not set(fold(text).split()) <= known


def _city_aliases(location_ids: list[str]) -> set[str]:
    aliases: set[str] = set()
    for location_id in location_ids:
        location = get_location(location_id)
        if location is not None:
            aliases.update(location.aliases)
    return aliases


# --- experience ---

NUMBER_WORDS = {
    "un": 1,
    "una": 1,
    "uno": 1,
    "one": 1,
    "dos": 2,
    "two": 2,
    "tres": 3,
    "three": 3,
    "cuatro": 4,
    "four": 4,
    "cinco": 5,
    "five": 5,
    "seis": 6,
    "six": 6,
    "siete": 7,
    "seven": 7,
    "ocho": 8,
    "eight": 8,
    "nueve": 9,
    "nine": 9,
    "diez": 10,
    "ten": 10,
    "once": 11,
    "eleven": 11,
    "doce": 12,
    "twelve": 12,
}
# "once" is 11 only before a unit: "I did it once" is not eleven years.
_NUMBER_WORD = re.compile(
    r"\b(?:" + "|".join(word for word in NUMBER_WORDS if word != "once") + r"|once(?= anos?\b))\b"
)
_COUPLE = _words("un par de", "a couple of", "a couple")
_HALF_YEAR = _words("medio ano", "media ano", "half a year", "half year")
_YEAR_AND_HALF = _words("ano y medio", "a year and a half")
_VAGUE = _words(
    r"(?:unos|algunos|varios|pocos|a few|some|several) (?:meses|months|anos|years)",
    "a while",
    "un tiempo",
)
_NO_EXPERIENCE = _words(
    "ninguna", "ninguno", "nada", "none", "no", "nunca", "never", "sin experiencia", "cero", "zero"
)
# Not knowing or not saying is no number, never zero years (QA sweep 2026-10-21).
_EXPERIENCE_UNSURE = _words(
    "no se",
    "no lo se",
    "no me acuerdo",
    "no lo recuerdo",
    "no recuerdo",
    "ni idea",
    "no estoy segur[oa]",
    "prefiero no",
    "no quiero decir\\w*",
    "not sure",
    "don t know",
    "dont know",
    "no idea",
    "can t remember",
    "don t remember",
    "rather not",
)
# "Dos años no, tres": an amount the candidate takes back is left out.
_AMOUNT_CLAUSE = re.compile(r"[,;]|\b(?:pero|but)\b")
_TAKEN_BACK = re.compile(
    r"(?:^|\s)(?:no|not)$"
    r"|^(?:no|not)\s+(?:\d|(?:un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez"
    r"|one|two|three|four|five|six|seven|eight|nine|ten)\b)"
)
# Words that are not one number, whatever the extractor made of them: a year ("desde 2019"), a
# range ("entre 2 y 3"), or not knowing ("no sé").
_SINCE_YEAR = re.compile(r"\b(?:desde|since|from)\s+(?:el\s+)?(?:ano\s+)?(?:19|20)\d\d\b")
_RANGE_WORDS = re.compile(r"\bentre\s+\S+\s+y\s+\S+|\bbetween\s+\S+\s+and\s+\S+")
# Two numbers with no unit between them are a range ("entre 2 y 3 años", "4-5 años"): ask for
# one number instead of adding them up. A year ("desde 2019") is not an amount either.
_RANGE = re.compile(r"\b\d+(?:\.\d+)?\s+(?:(?:a|o|or|y|and|to|hasta)\s+)?\d")
_CALENDAR_YEAR = re.compile(r"\b(?:19|20)\d\d\b")
_AMOUNT = re.compile(
    r"(\d+(?:\.\d+)?)\s*(anos?|years?|yrs?|meses|mes|months?|semanas?|weeks?|dias?|days?)?"
    r"(\s*(?:y|and a|and)\s*(?:medio|media|half))?"
)
_PER_YEAR = {
    **dict.fromkeys(("mes", "meses", "month", "months"), 12),
    **dict.fromkeys(("semana", "semanas", "week", "weeks"), 52),
    **dict.fromkeys(("dia", "dias", "day", "days"), 365),
}


def validate_years(raw: object, *, most: float | None = MAX_EXPERIENCE_YEARS) -> Result[float]:
    """Years, from a number or the candidate's words; up to `most` when there is a cap."""
    if isinstance(raw, bool):
        return Invalid(ReasonCode.EXPERIENCE_INVALID)
    if isinstance(raw, int | float):
        years = float(raw)
    elif isinstance(raw, str):
        parsed = _parse_experience(raw)
        if isinstance(parsed, ReasonCode):
            return Ambiguous((), parsed)
        if parsed is None:
            return Invalid(ReasonCode.EXPERIENCE_INVALID)
        years = parsed
    else:
        return Invalid(ReasonCode.EXPERIENCE_INVALID)
    if years < 0 or (most is not None and years > most):
        return Invalid(ReasonCode.EXPERIENCE_OUT_OF_RANGE)
    return Valid(round(years, 2))


def years_need_words(text: str) -> bool:
    """The candidate's words about their experience are not one number however the extractor
    read them: a year ("desde 2019"), a range ("entre 2 y 3 años"), not knowing ("no sé"), or
    an amount taken back ("dos años no, tres"). The validator reads the words instead."""
    folded = fold(text)
    if _SINCE_YEAR.search(folded) or _RANGE_WORDS.search(folded):
        return True
    if _EXPERIENCE_UNSURE.search(folded):
        return True
    clauses = [fold(clause) for clause in _AMOUNT_CLAUSE.split(_decimal_points(text))]
    return any(_TAKEN_BACK.search(clause) for clause in clauses if clause)


def _decimal_points(text: str) -> str:
    return re.sub(r"(\d),(\d)", r"\1.\2", text)


def _without_taken_back(raw: str) -> str:
    """ "Dos años no, tres" -> "tres"; "No, ninguna" -> "ninguna". A lone "no" stays."""
    clauses = [clause for clause in _AMOUNT_CLAUSE.split(raw) if fold(clause)]
    kept = [clause for clause in clauses if not _TAKEN_BACK.search(fold(clause))]
    return " ".join(kept) if kept else raw


def _parse_experience(raw: str) -> float | ReasonCode | None:
    raw = _without_taken_back(_decimal_points(raw))
    text = strip_accents(raw.casefold())
    text = re.sub(r"[^a-z0-9.]+", " ", text).strip()
    text = _COUPLE.sub("2", text)
    text = _NUMBER_WORD.sub(lambda m: str(NUMBER_WORDS[m.group(0)]), text)
    if _RANGE.search(text) or _CALENDAR_YEAR.search(text):
        return ReasonCode.EXPERIENCE_NEEDS_NUMBER
    total: float | None = None
    for match in _AMOUNT.finditer(text):
        amount = float(match.group(1)) + (0.5 if match.group(3) else 0)
        total = (total or 0) + amount / _PER_YEAR.get(match.group(2) or "", 1)
    if total is not None:
        return total
    if _YEAR_AND_HALF.search(text):
        return 1.5
    if _HALF_YEAR.search(text):
        return 0.5
    if _VAGUE.search(text):
        return ReasonCode.EXPERIENCE_NEEDS_NUMBER
    if _EXPERIENCE_UNSURE.search(fold(text)):
        return None
    if _NO_EXPERIENCE.search(text):
        return 0.0
    return None


# --- start date ---


def validate_date(
    raw: object,
    *,
    today: date,
    confirmed: bool = False,
    late_after: int | None = LATE_START_DAYS,
    read_back_after: int | None = MAX_START_DAYS,
) -> Result[StartDate]:
    """Accept an ISO date from today on. One more than `late_after` days away is a late start;
    more than `read_back_after`, it needs one confirmation."""
    if isinstance(raw, date):
        start = raw
    elif isinstance(raw, str):
        try:
            start = date.fromisoformat(raw.strip())
        except ValueError:
            return Invalid(ReasonCode.DATE_INVALID)
    else:
        return Invalid(ReasonCode.DATE_INVALID)
    days = (start - today).days
    if days < 0:
        return Invalid(ReasonCode.PAST_DATE)
    if late_after is None or days <= late_after:
        return Valid(StartDate(start, None))
    far = read_back_after is not None and days > read_back_after
    return Valid(
        StartDate(start, days), note=ReasonCode.LATE_START, needs_confirmation=far and not confirmed
    )
