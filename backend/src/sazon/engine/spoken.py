"""Interview times in words, for the voice channel (SAZ-48).

Text to speech reads "a las 09:00" badly ("¿A las qué?", call 13b77d6e). Every voice message
that carries an interview time says it in words instead, built here and never by a model:
"el jueves 22 a las nueve de la mañana", "Thursday the 22nd at nine a.m.". The day of the month
stays in digits (SAZ-50).
The web keeps "jueves 22 a las 09:00".
"""

from datetime import date, datetime, time
from typing import Literal

type Register = Literal["es-ES", "es-MX", "en"]

_ES_UNITS = (
    "cero",
    "uno",
    "dos",
    "tres",
    "cuatro",
    "cinco",
    "seis",
    "siete",
    "ocho",
    "nueve",
    "diez",
    "once",
    "doce",
    "trece",
    "catorce",
    "quince",
    "dieciséis",
    "diecisiete",
    "dieciocho",
    "diecinueve",
    "veinte",
    "veintiuno",
    "veintidós",
    "veintitrés",
    "veinticuatro",
    "veinticinco",
    "veintiséis",
    "veintisiete",
    "veintiocho",
    "veintinueve",
)
_ES_TENS = {3: "treinta", 4: "cuarenta", 5: "cincuenta"}
_EN_UNITS = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_EN_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty"}
_ES_WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_EN_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
NOON = 12
MORNING_FROM = 6
AFTERNOON_FROM = 13
NIGHT_FROM = 21
HALF_DAY = 12
QUARTER = 15
HALF_HOUR = 30
QUARTER_TO = 45
TEN = 10


def es_number(n: int) -> str:
    """0 to 59 in Spanish words: "veintidós", "treinta y uno"."""
    if n < len(_ES_UNITS):
        return _ES_UNITS[n]
    tens, units = divmod(n, 10)
    return _ES_TENS[tens] if units == 0 else f"{_ES_TENS[tens]} y {_ES_UNITS[units]}"


def en_number(n: int) -> str:
    """0 to 59 in English words: "twenty-two", "forty-five"."""
    if n < len(_EN_UNITS):
        return _EN_UNITS[n]
    tens, units = divmod(n, 10)
    return _EN_TENS[tens] if units == 0 else f"{_EN_TENS[tens]}-{_EN_UNITS[units]}"


def spoken_time(moment: time, register: Register) -> str:
    """A time of day as said aloud, with its preposition: "a las nueve de la mañana", "a la una
    y media de la tarde", "a las doce del mediodía"; a quarter to the hour as people say it,
    "a las once menos cuarto de la mañana" in Spain and "a un cuarto para las once de la
    mañana" in Mexico; "at nine a.m.", "at four thirty p.m.", "at noon"."""
    if register == "en":
        return _en_time(moment)
    return _es_time(moment, register)


def _es_time(moment: time, register: Register = "es-ES") -> str:
    hour, minute = moment.hour, moment.minute
    if minute == QUARTER_TO:
        hour = (hour + 1) % 24  # "las once menos cuarto": said from the next hour
    twelve = hour % HALF_DAY or HALF_DAY
    at = "la una" if twelve == 1 else f"las {es_number(twelve)}"
    if minute == QUARTER_TO and register == "es-MX":
        head = f"a un cuarto para {at}"
    elif minute == QUARTER_TO:
        head = f"a {at} menos cuarto"
    elif minute == QUARTER:
        head = f"a {at} y cuarto"
    elif minute == HALF_HOUR:
        head = f"a {at} y media"
    elif minute:
        head = f"a {at} y {es_number(minute)}"
    else:
        head = f"a {at}"
    if hour == NOON:
        period = "del mediodía"
    elif hour < MORNING_FROM:
        period = "de la madrugada" if hour else "de la noche"
    elif hour < NOON:
        period = "de la mañana"
    elif hour < NIGHT_FROM:
        period = "de la tarde"
    else:
        period = "de la noche"
    return f"{head} {period}"


def _en_time(moment: time) -> str:
    hour, minute = moment.hour, moment.minute
    if minute == 0 and hour == NOON:
        return "at noon"
    if minute == 0 and hour == 0:
        return "at midnight"
    twelve = hour % HALF_DAY or HALF_DAY
    words = en_number(twelve)
    if minute:
        words += f" oh {en_number(minute)}" if minute < TEN else f" {en_number(minute)}"
    return f"at {words} {'a.m.' if hour < NOON else 'p.m.'}"


def spoken_day(day: date, register: Register) -> str:
    """A day as said aloud, without the article: "jueves 22", "Thursday the 22nd". The day of
    the month stays in digits (text to speech reads it well, and spelled out it reads oddly in
    the transcript); only the hour is said in words. No month: interviews are offered at most
    four weeks ahead, so the weekday and the day are never ambiguous."""
    if register == "en":
        return f"{_EN_WEEKDAYS[day.weekday()]} the {day.day}{_en_suffix(day.day)}"
    return f"{_ES_WEEKDAYS[day.weekday()]} {day.day}"


def _en_suffix(n: int) -> str:
    if n % 100 in (11, 12, 13):
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def spoken_when(starts_at: str, register: Register) -> str:
    """An interview's local start (ISO 8601, as in `Slot.starts_at`) as said aloud: "jueves 22
    a las nueve de la mañana", "Thursday the 22nd at nine a.m."."""
    moment = datetime.fromisoformat(starts_at)
    day = spoken_day(moment.date(), register)
    return f"{day} {spoken_time(time(moment.hour, moment.minute), register)}"
