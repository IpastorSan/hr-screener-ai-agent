"""Read a slot pick from the candidate's own words, against the slots on offer.

Deterministic and conservative: it returns a slot id only when the words single one out
("el martes por la mañana", "the first one", "a las 16:30", "las once menos cuarto"), "none"
when the candidate turns them all down, "change" when they want to move a booked interview,
and None otherwise. `read_choice` also says when the words fit several offered slots ("a las
cuatro" with three at 16:00), name a time off the 15-minute grid ("10:40"), or ask for other
times ("¿tienes algo por la tarde?", "¿y el viernes?", "¿podría ser a las 12?", "¿y otras
opciones?"): the engine asks which, asks again, or looks the times up, and never books a slot
the words did not single out.
"""

import re
from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta

from pydantic import BaseModel, ConfigDict

from sazon.engine.slots import CHANGE, NONE_WORK, Slot, SlotQuery, TimeRequest
from sazon.engine.text import fold

WEEKDAY_WORDS = {
    0: ("lunes", "monday"),
    1: ("martes", "tuesday"),
    2: ("miercoles", "wednesday"),
    3: ("jueves", "thursday"),
    4: ("viernes", "friday"),
    5: ("sabado", "saturday"),
    6: ("domingo", "sunday"),
}
ORDINALS = {
    0: ("primer", "primero", "primera", "first", "1st", "1o", "1a"),
    1: ("segundo", "segunda", "second", "2nd", "2o", "2a", "medio", "middle"),
    2: ("tercer", "tercero", "tercera", "third", "3rd", "3o", "3a"),
    -1: ("ultimo", "ultima", "last"),
}
MORNING_PHRASES = re.compile(r"\b(?:por|de|en) la manana\b|\bla manana\b|\bmananas\b")
MORNING_WORDS = frozenset({"morning", "mornings", "temprano", "early"})
AFTERNOON_WORDS = frozenset({"tarde", "tardes", "afternoon", "afternoons", "evening", "pm"})
NONE_PHRASES = re.compile(
    r"\b(?:ninguno|ninguna|ningun|none|neither|"
    r"no me (?:viene|vienen|va|van|cuadra|cuadran|sirve|sirven|acomoda|acomodan|queda|quedan)|"
    r"no puedo|no podre|otro dia|otros dias|otra hora|otras horas|otro horario|otros horarios|"
    r"otras fechas|other times|other days|another day|another time|can t|cannot|"
    r"doesn t work|don t work|doesnt work|dont work|not good)\b"
)
CHANGE_PHRASES = re.compile(
    r"\b(?:cambiar|cambiarla|cambiarlo|cambio|mover|moverla|reprogramar|aplazar|"
    r"no voy a poder|no puedo ir|reschedule|change|move it|can t make it|cannot make it)\b"
)
HOUR_WORDS = {
    "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
}  # fmt: skip
_HOUR = r"(\d{1,2}|" + "|".join(HOUR_WORDS) + ")"
CLOCK = re.compile(r"\b(\d{1,2}) ([0-5]\d)\b")
HOUR = re.compile(
    r"\b(a las|at|las|sobre las|a eso de las|a la|la) " + _HOUR
    + r"(?: (y media|y cuarto|menos cuarto))?\b(?! \d{2}\b)"
)  # fmt: skip
QUARTER_TO = re.compile(r"\b(?:un )?cuarto para (?:las|la) " + _HOUR + r"\b")
HOUR_SUFFIX = re.compile(r"\b(\d{1,2}) ?(am|pm|h|hrs|horas)\b")
DAY_WORDS = {
    "uno": 1, "primero": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13,
    "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintiun": 21, "veintidos": 22,
    "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26,
    "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30,
    "treinta y uno": 31,
}  # fmt: skip
"""Days of the month in words, as a voice transcript may write them ("el veintisiete")."""
MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
    "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11,
    "diciembre": 12, "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}  # fmt: skip
"""No "may": "the 3rd may work" names no month (interviews are at most four weeks ahead)."""
_DAY = r"(\d{1,2}|" + "|".join(sorted(DAY_WORDS, key=len, reverse=True)) + r")(?:st|nd|rd|th)?"
_MONTH = r"(" + "|".join(MONTHS) + r")"
_WEEKDAY = "|".join(name for names in WEEKDAY_WORDS.values() for name in names)
DAY_NUMBER = re.compile(
    r"\b(?:el|dia|day|the|on the|on|" + _WEEKDAY + r") " + _DAY
    + r"(?: (?:de |of )?" + _MONTH + r")?\b"
)  # fmt: skip
"""A day of the month: "el 27", "el día 27", "martes 27", "el veintisiete", "el 3 de
noviembre"."""
DAY_MONTH = re.compile(r"\b" + _DAY + r" (?:de |of )?" + _MONTH + r"\b")
FIRST = re.compile(r"\bel primero\b(?! (?:de )?" + _MONTH + r"\b)")
""""El primero" without a month: the first option, or the 1st when an offer is on it."""
IN_MONTH = re.compile(r"\b(?:de|del|en|in) " + _MONTH + r"\b")
"""A month named alone: "el de noviembre", "el martes de octubre"."""
MONTH_DAY = re.compile(r"\b" + _MONTH + r" (?:the )?" + _DAY + r"\b")
BARE_NUMBER = re.compile(r"^(?:la |el )?(?:(?:opcion|option|numero|number) )?([1-9])$")
TOMORROW = re.compile(r"\b(?:manana|tomorrow)\b")
DAY_AFTER = re.compile(r"\b(?:pasado manana|day after tomorrow)\b")
MINUTES = {"y media": 30, "y cuarto": 15}
LAST_MORNING_HOUR = 13
LATER = re.compile(r"\b(?:mas tarde|later)\b")
EARLIER = re.compile(r"\b(?:mas temprano|mas pronto|earlier)\b")
NEXT_WEEK = re.compile(r"\b(?:semana que viene|proxima semana|siguiente semana|next week)\b")
THIS_WEEK = re.compile(r"\b(?:esta semana|this week)\b")
WEEKEND = re.compile(r"\b(?:fin de semana|finde|weekend)\b")
ASKING = re.compile(
    r"\b(?:tienes|tiene|tienen|tendras|tendra|hay|habra|algo|algun|alguno|alguna|"
    r"puede ser|podria ser|se puede|anything|something|do you have|is there|could it be)\b"
)
"""Words that make a message ask about times rather than pick one ("tienes algo el viernes");
a question mark does too."""
OTHER_OPTIONS = re.compile(
    r"\b(?:otras? opcion(?:es)?|mas opciones|otros? huecos?|mas huecos|algun otro|alguna otra|"
    r"algo mas|alguno mas|alguna mas|other options?|another option|more options|other slots|"
    r"more times|anything else|any others?)\b"
)
"""Asking for more times with no condition ("¿y otras opciones?", "¿qué otros huecos hay?"):
the next free times, never one already offered. Not a turn-down: "otras horas" is one."""
GRID_MINUTES = 15
"""Interviews start on a 15-minute grid: a time off it ("10:40") is never free."""

type Check = Callable[[datetime], bool]


class Reading(BaseModel):
    """What the candidate's words say about the offered slots: a single `choice` (a slot id,
    NONE_WORK or CHANGE) and, beyond it, the `request` for the engine."""

    model_config = ConfigDict(frozen=True)

    choice: str | None = None
    request: TimeRequest | None = None


def resolve_choice(
    text: str, offered: Sequence[Slot], *, booked: bool = False, today: date | None = None
) -> str | None:
    """The offered slot's id, NONE_WORK, CHANGE (only when `booked`), or None."""
    return read_choice(text, offered, booked=booked, today=today).choice


def read_choice(
    text: str, offered: Sequence[Slot], *, booked: bool = False, today: date | None = None
) -> Reading:
    """The candidate's pick, if their words single one out, and what else they say about the
    times: a request for other times (with "none", with "change" or alone), several offered
    slots their words fit, or a clock time none of them has."""
    words = fold(text)
    if not words:
        return Reading()
    asking = "?" in text or bool(ASKING.search(words))
    starts = [datetime.fromisoformat(slot.starts_at) for slot in offered]
    query = _query(words, starts, today)
    if booked and (CHANGE_PHRASES.search(words) or NONE_PHRASES.search(words)):
        return Reading(choice=CHANGE, request=_asked(query))
    if not offered:
        return Reading()
    if NONE_PHRASES.search(words):
        # "No puedo el martes" turns a day down; "ninguno, ¿tienes algo el viernes?" asks.
        return Reading(choice=NONE_WORK, request=_asked(query if asking else None))
    days = _days(words, starts)
    bare = BARE_NUMBER.match(words)
    if bare and not any((start.day, None) in days for start in starts):
        # "El 3" after "el martes 27 o el martes 3" is the day, not the third option.
        index = int(bare.group(1)) - 1
        return Reading(choice=offered[index].id if index < len(offered) else None)
    if asking and query is not None and not query.at:
        return Reading(request=TimeRequest(query=query))
    checks = _checks(words, today, days)
    if checks:
        matches = [
            slot.id
            for slot, start in zip(offered, starts, strict=True)
            if all(check(start) for check in checks)
        ]
        first = offered[0].id
        if len(matches) == 1 and matches[0] != first and FIRST.search(words):
            # "El primero" with an offer on the 1st that is not the first one: which?
            return Reading(request=TimeRequest(matches=(first, matches[0])))
        if len(matches) == 1:
            return Reading(choice=matches[0])
        if matches:
            return Reading(request=TimeRequest(matches=tuple(matches)))
        if query is not None and query.at:
            # "¿Podría ser a las 12?" with no 12:00 on offer: looked up, free or not.
            return Reading(request=TimeRequest(query=query))
        if _times(words)[0]:
            return Reading(request=TimeRequest(unmatched_time=True))
        return Reading(request=_asked(query))
    index = _ordinal(words)
    if index is None or index >= len(offered):
        return Reading(request=_asked(query))
    return Reading(choice=offered[index].id)


def _asked(query: SlotQuery | None) -> TimeRequest | None:
    return TimeRequest(query=query) if query is not None else None


def _query(words: str, starts: Sequence[datetime], today: date | None) -> SlotQuery | None:
    """The times the words ask about, or None when they name no day, week, time of day, clock
    time or other options. "Más tarde" is later than every time on offer. A clock time on the
    grid is the target (`at`); with no day named, it is on the day of the times on offer when
    they share one ("No, pregunto que si a las 12 puede ser")."""
    clocks = sorted(start.time().replace(tzinfo=None) for start in starts)
    later = clocks[-1] if clocks and LATER.search(words) else None
    earlier = clocks[0] if clocks and EARLIER.search(words) else None
    words = LATER.sub(" ", EARLIER.sub(" ", words))
    other = bool(OTHER_OPTIONS.search(words))
    at = tuple(sorted(time(h, m) for h, m in _times(words)[0] if m % GRID_MINUTES == 0))
    morning = bool(MORNING_PHRASES.search(words))
    words = MORNING_PHRASES.sub(" ", words)
    tokens = set(words.split())
    weekdays = {day for day, names in WEEKDAY_WORDS.items() if tokens & set(names)}
    if WEEKEND.search(words):
        weekdays |= {5, 6}
    days: set[date] = set()
    week_of: date | None = None
    if today is not None:
        if DAY_AFTER.search(words):
            days.add(today + timedelta(days=2))
        elif TOMORROW.search(words):
            days.add(today + timedelta(days=1))
        monday = today - timedelta(days=today.weekday())
        if NEXT_WEEK.search(words):
            week_of = monday + timedelta(days=7)
        elif THIS_WEEK.search(words):
            week_of = monday
        days |= {
            day
            for number, month in _days(words, starts)
            if (day := _next_day(today, number, month)) is not None
        }
    part = None
    if morning or tokens & MORNING_WORDS:
        part = "morning"
    elif tokens & AFTERNOON_WORDS:
        part = "afternoon"
    offered_days = {start.date() for start in starts}
    if at and not (weekdays or days or week_of) and len(offered_days) == 1:
        days = offered_days
    if not (weekdays or days or week_of or part or later or earlier or at or other):
        return None
    return SlotQuery(
        weekdays=tuple(sorted(weekdays)),
        days=tuple(sorted(days)),
        week_of=week_of,
        part=part,
        later_than=later,
        earlier_than=earlier,
        at=at,
        other=other,
    )


def _days(words: str, starts: Sequence[datetime]) -> set[tuple[int, int | None]]:
    """Days of the month the words name, each with its month when they name one: "el 27",
    "martes 27", "el día 27", "el veintisiete", "27 de octubre", "October 27th". "El primero"
    is the first option, unless an offer is on the 1st."""
    used = _times(words)[1]
    found: set[tuple[int, int | None]] = set()
    for pattern, day_group, month_group in (
        (DAY_NUMBER, 1, 2),
        (DAY_MONTH, 1, 2),
        (MONTH_DAY, 2, 1),
    ):
        for match in pattern.finditer(words):
            word, month_word = match.group(day_group), match.group(month_group)
            month = MONTHS[month_word] if month_word else None
            if month is None and word in used:
                continue  # "a las 4" is a time
            if FIRST.match(match.group(0)) and not any(start.day == 1 for start in starts):
                continue  # "el primero": the first option
            day = DAY_WORDS[word] if word in DAY_WORDS else int(word)
            if 1 <= day <= 31:
                found.add((day, month))
    return found


def _next_day(today: date, number: int, month: int | None = None) -> date | None:
    """The next date with that day of the month (and month), today included; None when there
    is none within a year ("31 de febrero")."""
    for ahead in range(366):
        day = today + timedelta(days=ahead)
        if day.day == number and month in (None, day.month):
            return day
    return None


def _checks(words: str, today: date | None, days: set[tuple[int, int | None]]) -> list[Check]:
    checks: list[Check] = []
    morning = bool(MORNING_PHRASES.search(words))
    words = MORNING_PHRASES.sub(" MORNING ", words)
    tokens = set(words.split())

    weekdays = {day for day, names in WEEKDAY_WORDS.items() if tokens & set(names)}
    if weekdays:
        checks.append(lambda start: start.weekday() in weekdays)

    if today is not None and DAY_AFTER.search(words):
        target = today + timedelta(days=2)
        checks.append(lambda start: start.date() == target)
    elif today is not None and TOMORROW.search(words):
        target = today + timedelta(days=1)
        checks.append(lambda start: start.date() == target)

    hours, _ = _times(words)
    if hours:
        checks.append(lambda start: (start.hour, start.minute) in hours)

    months = {MONTHS[match.group(1)] for match in IN_MONTH.finditer(words)}
    if months:
        checks.append(lambda start: start.month in months)

    if days:
        checks.append(
            lambda start: any(
                start.day == day and month in (None, start.month) for day, month in days
            )
        )

    if morning or tokens & MORNING_WORDS:
        checks.append(lambda start: start.hour <= LAST_MORNING_HOUR)
    elif tokens & AFTERNOON_WORDS:
        checks.append(lambda start: start.hour > LAST_MORNING_HOUR)
    return checks


def _times(words: str) -> tuple[set[tuple[int, int]], set[str]]:
    """Clock times the candidate named, both readings of an ambiguous "a las 4" (4:00, 16:00).
    "Las once menos cuarto" and "un cuarto para las once" are 10:45."""
    found: set[tuple[int, int]] = set()
    used: set[str] = set()
    for match in CLOCK.finditer(words):
        found |= _readings(int(match.group(1)), int(match.group(2)))
        used.update(match.group(1, 2))
    for match in HOUR.finditer(words):
        hour = _hour(match.group(2))
        if match.group(1) in ("a la", "la") and hour != 1:
            continue  # "la 2" is the second option; only "la una" is a time
        if match.group(3) == "menos cuarto":
            found |= _readings(hour - 1 or 12, 45)
        else:
            found |= _readings(hour, MINUTES.get(match.group(3) or "", 0))
        used.add(match.group(2))
    for match in QUARTER_TO.finditer(words):
        found |= _readings(_hour(match.group(1)) - 1 or 12, 45)
        used.add(match.group(1))
    for match in HOUR_SUFFIX.finditer(words):
        hour = int(match.group(1))
        if match.group(2) == "pm" and hour < 12:
            hour += 12
        found |= {(hour, 0)} if match.group(2) in ("am", "pm") else _readings(hour, 0)
        used.add(match.group(1))
    return {(h, m) for h, m in found if 0 <= h <= 23}, used


def _hour(word: str) -> int:
    return HOUR_WORDS[word] if word in HOUR_WORDS else int(word)


def _readings(hour: int, minute: int) -> set[tuple[int, int]]:
    if 1 <= hour <= 11:
        return {(hour, minute), (hour + 12, minute)}
    return {(hour, minute)}


def _ordinal(words: str) -> int | None:
    tokens = set(words.split())
    found = [index for index, names in ORDINALS.items() if tokens & set(names)]
    return found[0] if len(found) == 1 else None
