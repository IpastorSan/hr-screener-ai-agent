"""How the riders' answers about their availability, schedule and platforms are read into
the options: the words for each, in Spanish and English (the `multi_enum` readers of the rider
spec)."""

import re
from typing import NamedTuple

from sazon.engine.models import Availability, Platform, Schedule
from sazon.engine.text import fold
from sazon.engine.validators import (
    _CLAUSE,
    ITEM_SEPARATORS,
    Invalid,
    ReasonCode,
    Result,
    Valid,
    _find,
    _words,
)


class Platforms(NamedTuple):
    platforms: list[Platform]
    other: str | None


_AVAILABILITY = {
    Availability.FULL_TIME: _words(
        "full ?time", "tiempo completo", "jornada completa", "completo", "completa"
    ),
    Availability.PART_TIME: _words(
        "part ?time",
        "medio tiempo",
        "media jornada",
        "tiempo parcial",
        "jornada parcial",
        "parcial",
    ),
    Availability.WEEKENDS: _words(
        "week ?ends?", "fin(?:es)? de semana", "findes?", "sabados?", "domingos?", "saturdays?"
    ),
}


def _as_text(raw: object) -> str | None:
    if isinstance(raw, str):
        return fold(raw)
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return " , ".join(fold(item) for item in raw)
    return None


def validate_availability(raw: object) -> Result[list[Availability]]:
    text = _as_text(raw)
    if text is None:
        return Invalid(ReasonCode.AVAILABILITY_INVALID)
    found = _find(_AVAILABILITY, text)
    if not found:
        return Invalid(ReasonCode.AVAILABILITY_INVALID)
    return Valid(found)


_SCHEDULE = {
    Schedule.MORNING: _words("mornings?", "mananas?", "temprano", "early"),
    Schedule.AFTERNOON: _words("afternoons?", "tardes?", "mediodia"),
    Schedule.EVENING: _words("evenings?", "nights?", "noches?", "nocturno", "late"),
    Schedule.FLEXIBLE: _words(
        "flexible",
        "cualquiera",
        "cualquier",
        "any",
        "anytime",
        "whenever",
        "whatever",
        "me da igual",
        "da igual",
        "indiferente",
        "lo que sea",
        "todo el dia",
        "all day",
    ),
}


_NEGATED_BEFORE = re.compile(
    r"\b(?:no|ni|sin|nada de|menos|excepto|salvo|not|no puedo|can t|cannot|except|but not)\s+"
    r"(?:\w+\s+){0,3}$"
)
_NEGATED_AFTER = re.compile(r"^\s*(?:no|tampoco|not|nope)\b")
_BLOCKS = (Schedule.MORNING, Schedule.AFTERNOON, Schedule.EVENING)


def validate_schedule(raw: object) -> Result[list[Schedule]]:
    """One or more blocks: "mañanas y tardes, noches no" -> [morning, afternoon].

    `flexible` stands alone; with a block refused ("cualquiera menos noches") it is the other
    blocks.
    """
    if isinstance(raw, str):
        clauses = _CLAUSE.split(raw.casefold())
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        clauses = [part for item in raw for part in _CLAUSE.split(item.casefold())]
    else:
        return Invalid(ReasonCode.SCHEDULE_INVALID)
    chosen: set[Schedule] = set()
    refused: set[Schedule] = set()
    for clause in clauses:
        words = fold(clause)
        for block, pattern in _SCHEDULE.items():
            for match in pattern.finditer(words):
                before, after = words[: match.start()], words[match.end() :]
                negated = _NEGATED_BEFORE.search(before) or _NEGATED_AFTER.match(after)
                (refused if negated else chosen).add(block)
    chosen -= refused
    if Schedule.FLEXIBLE in chosen:
        if refused & set(_BLOCKS):
            return Valid([block for block in _BLOCKS if block not in refused])
        return Valid([Schedule.FLEXIBLE])
    found = [block for block in _BLOCKS if block in chosen]
    if found:
        return Valid(found)
    return Invalid(ReasonCode.SCHEDULE_INVALID)


_PLATFORMS = {
    Platform.GLOVO: _words("glovo"),
    Platform.UBER_EATS: _words("uber ?eats", "uber"),
    Platform.JUST_EAT: _words("just ?eat"),
    Platform.RAPPI: _words("rappi", "rapi"),
    Platform.DIDI_FOOD: _words("didi ?food", "didi"),
    Platform.DELIVEROO: _words("deliveroo"),
    Platform.AMAZON_FLEX: _words("amazon ?flex", "amazon"),
}
_NO_PLATFORMS = {"", "no", "none", "ninguna", "ninguno", "nada", "nothing", "n a"}
# Words that name the kind of work, not a platform ("hice delivery", "repartos", "apps de
# comida"): an item made only of these and of filler is no platform (E2E 2026-10-22).
_GENERIC_PLATFORM_WORDS = frozenset(
    {
        "delivery",
        "deliveries",
        "reparto",
        "repartos",
        "repartidor",
        "repartidora",
        "rider",
        "riders",
        "courier",
        "mensajeria",
        "domicilio",
        "domicilios",
        "app",
        "apps",
        "aplicacion",
        "aplicaciones",
        "plataforma",
        "plataformas",
        "food",
        "comida",
        "pedidos",
        "envios",
        "hice",
        "he",
        "hecho",
        "trabaje",
        "trabajado",
        "did",
        "do",
        "worked",
        "work",
        "working",
        "de",
        "del",
        "a",
        "al",
        "en",
        "con",
        "por",
        "para",
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "mi",
        "mis",
        "in",
        "on",
        "for",
        "with",
        "the",
        "my",
    }
)


def validate_platforms(raw: object) -> Result[Platforms]:
    if isinstance(raw, str):
        items = ITEM_SEPARATORS.split(raw)
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        items = [part for item in raw for part in ITEM_SEPARATORS.split(item)]
    else:
        return Invalid(ReasonCode.PLATFORMS_INVALID)
    platforms: list[Platform] = []
    others: list[str] = []
    for item in (item.strip() for item in items):
        folded = fold(item)
        if folded in _NO_PLATFORMS or set(folded.split()) <= _GENERIC_PLATFORM_WORDS:
            continue
        known = _find(_PLATFORMS, folded)
        if known:
            platforms.extend(p for p in known if p not in platforms)
        else:
            others.append(item)
    if others and Platform.OTHER not in platforms:
        platforms.append(Platform.OTHER)
    return Valid(Platforms(platforms, ", ".join(others) or None))
