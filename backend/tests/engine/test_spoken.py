"""Interview times in words for the voice channel (SAZ-48), with the day of the month in digits
(SAZ-50): built in code, never by the model, the same way in every register."""

from datetime import date, time

import pytest

from sazon.engine.spoken import (
    Register,
    en_number,
    es_number,
    spoken_day,
    spoken_time,
    spoken_when,
)


@pytest.mark.parametrize(
    ("at", "es", "en"),
    [
        (time(9), "a las nueve de la mañana", "at nine a.m."),
        (time(16, 30), "a las cuatro y media de la tarde", "at four thirty p.m."),
        (time(13), "a la una de la tarde", "at one p.m."),
        (time(1), "a la una de la madrugada", "at one a.m."),
        (time(12), "a las doce del mediodía", "at noon"),
        (time(12, 30), "a las doce y media del mediodía", "at twelve thirty p.m."),
        (time(9, 15), "a las nueve y cuarto de la mañana", "at nine fifteen a.m."),
        (time(9, 5), "a las nueve y cinco de la mañana", "at nine oh five a.m."),
        (time(11, 10), "a las once y diez de la mañana", "at eleven ten a.m."),
        (time(5, 59), "a las cinco y cincuenta y nueve de la madrugada", "at five fifty-nine a.m."),
        (time(6), "a las seis de la mañana", "at six a.m."),
        (time(11, 59), "a las once y cincuenta y nueve de la mañana", "at eleven fifty-nine a.m."),
        (time(20, 20), "a las ocho y veinte de la tarde", "at eight twenty p.m."),
        (time(21), "a las nueve de la noche", "at nine p.m."),
        (time(0), "a las doce de la noche", "at midnight"),
        (time(0, 30), "a las doce y media de la noche", "at twelve thirty a.m."),
    ],
)
def test_times_in_words(at: time, es: str, en: str):
    assert spoken_time(at, "es-ES") == es
    assert spoken_time(at, "es-MX") == es  # Mexico says them the same way
    assert spoken_time(at, "en") == en


@pytest.mark.parametrize(
    ("at", "es", "mx", "en"),
    [
        (
            time(10, 45),
            "a las once menos cuarto de la mañana",
            "a un cuarto para las once de la mañana",
            "at ten forty-five a.m.",
        ),
        (
            time(11, 45),
            "a las doce menos cuarto del mediodía",
            "a un cuarto para las doce del mediodía",
            "at eleven forty-five a.m.",
        ),
        (
            time(12, 45),
            "a la una menos cuarto de la tarde",
            "a un cuarto para la una de la tarde",
            "at twelve forty-five p.m.",
        ),
        (
            time(19, 45),
            "a las ocho menos cuarto de la tarde",
            "a un cuarto para las ocho de la tarde",
            "at seven forty-five p.m.",
        ),
    ],
)
def test_a_quarter_to_the_hour_is_said_from_the_next_hour(at: time, es: str, mx: str, en: str):
    """Interviews start at :00, :15, :30 and :45: "las once menos cuarto" in Spain, "un cuarto
    para las once" in Mexico, never "las diez y cuarenta y cinco"."""
    assert spoken_time(at, "es-ES") == es
    assert spoken_time(at, "es-MX") == mx
    assert spoken_time(at, "en") == en


@pytest.mark.parametrize(
    ("day", "register", "spoken"),
    [
        (date(2026, 10, 22), "es-ES", "jueves 22"),
        (date(2026, 10, 22), "es-MX", "jueves 22"),
        (date(2026, 10, 22), "en", "Thursday the 22nd"),
        (date(2026, 10, 21), "es-ES", "miércoles 21"),
        (date(2026, 10, 21), "en", "Wednesday the 21st"),
        (date(2026, 10, 1), "es-ES", "jueves 1"),
        (date(2026, 10, 1), "es-MX", "jueves 1"),
        (date(2026, 10, 1), "en", "Thursday the 1st"),
        (date(2026, 10, 3), "en", "Saturday the 3rd"),
        (date(2026, 10, 11), "en", "Sunday the 11th"),
        (date(2026, 10, 12), "en", "Monday the 12th"),
        (date(2026, 10, 13), "en", "Tuesday the 13th"),
        (date(2026, 10, 20), "es-ES", "martes 20"),
        (date(2026, 10, 20), "en", "Tuesday the 20th"),
        (date(2026, 10, 23), "en", "Friday the 23rd"),
        (date(2026, 10, 30), "es-ES", "viernes 30"),
        (date(2026, 10, 30), "en", "Friday the 30th"),
        (date(2026, 10, 31), "es-MX", "sábado 31"),
        (date(2026, 10, 31), "en", "Saturday the 31st"),
        (date(2026, 10, 25), "es-ES", "domingo 25"),
    ],
)
def test_days_in_digits(day: date, register: Register, spoken: str):
    assert spoken_day(day, register) == spoken


@pytest.mark.parametrize(
    ("register", "spoken"),
    [
        ("es-ES", "miércoles 21 a las nueve de la mañana"),
        ("es-MX", "miércoles 21 a las nueve de la mañana"),
        ("en", "Wednesday the 21st at nine a.m."),
    ],
)
def test_an_interview_start_with_the_hour_in_words(register: Register, spoken: str):
    """Call 13b77d6e offered "el miércoles 21 a las 09:00" and the candidate asked "¿A las
    qué?". The local time of the slot is what is said, whatever its offset."""
    assert spoken_when("2026-10-21T09:00:00+02:00", register) == spoken
    assert spoken_when("2026-10-21T09:00:00-06:00", register) == spoken


def test_numbers_and_ordinals():
    assert [es_number(n) for n in (0, 16, 21, 29, 30, 31, 40, 45, 50, 59)] == [
        "cero",
        "dieciséis",
        "veintiuno",
        "veintinueve",
        "treinta",
        "treinta y uno",
        "cuarenta",
        "cuarenta y cinco",
        "cincuenta",
        "cincuenta y nueve",
    ]
    assert [en_number(n) for n in (0, 13, 20, 21, 30, 45, 59)] == [
        "zero",
        "thirteen",
        "twenty",
        "twenty-one",
        "thirty",
        "forty-five",
        "fifty-nine",
    ]
